"""Save the session's code and dsh state to ``/arc``; restore it later.

On CANFAR, code lives on ``$SCRATCH/src`` and dsh's runtime state on
``$SCRATCH/.studio-$USER``; both die with the session. A snapshot packs them
(git repos including uncommitted and untracked work) into a directory on
``/arc``::

    <root>/<name>/manifest.json
    <root>/<name>/src.tar.zst         # contents of $SCRATCH/src
    <root>/<name>/dsh-state.tar.zst   # contents of the dsh state dir (optional)

Rebuildable environments (``.pixi``, ``.venv``, caches) are excluded by
default; lockfiles stay, so ``pixi install`` / ``uv sync`` rebuild them.
This is separate from ``canfar lab save``, which keeps lockfiles only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from canfar_lab.errors import LabError

MANIFEST = "manifest.json"
SRC_ARCHIVE = "src.tar.zst"
DSH_ARCHIVE = "dsh-state.tar.zst"
FORMAT_VERSION = 1
DEFAULT_EXCLUDES = (".pixi", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TAR_TIMEOUT_SEC = int(os.environ.get("CANFAR_LAB_SNAPSHOT_TIMEOUT", "3600"))


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def snapshot_root() -> Path:
    """Where snapshots live: ``$CANFAR_LAB_SNAPSHOT_DIR`` or ``~/.astroai/lab/snapshots``."""
    override = os.environ.get("CANFAR_LAB_SNAPSHOT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    from canfar_lab import config_dir

    return config_dir() / "snapshots"


def scratch_dir() -> Path | None:
    from canfar_lab.studio_paths import _scratch_dir

    return _scratch_dir(dict(os.environ))


def dsh_state_dir(scratch: Path) -> Path:
    """dsh's runtime state dir on *scratch* (see ``studio_paths.resolve_state_root``)."""
    from canfar_lab.studio_paths import resolve_state_root

    return resolve_state_root(Path.home(), profile="canfar", scratch=scratch).path


def _require_scratch(scratch: Path | None) -> Path:
    resolved = scratch or scratch_dir()
    if resolved is None:
        raise LabError("No writable $SCRATCH in this session.", hint="Set SCRATCH=/scratch")
    return resolved


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise LabError(
            f"Invalid snapshot name {name!r}.",
            hint="Use letters, digits, '.', '_' or '-' (max 64, no leading punctuation).",
        )
    return name


def default_name() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------
# tar | zstd
# --------------------------------------------------------------------------


def _need_tools() -> None:
    for tool in ("tar", "zstd"):
        if shutil.which(tool) is None:
            raise LabError(f"{tool} not found — cannot pack or unpack snapshots.")


def _pack(source: Path, dest: Path, excludes: tuple[str, ...]) -> None:
    """Pack the *contents* of *source* into *dest* (tar + zstd)."""
    cmd = ["tar", "--use-compress-program=zstd -T0 -3", "-C", str(source)]
    cmd += [f"--exclude={pattern}" for pattern in excludes]
    cmd += ["-cf", str(dest), "."]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TAR_TIMEOUT_SEC, check=False)
    # GNU tar exits 1 when a file changed while it was read; the archive is still usable.
    if proc.returncode not in (0, 1):
        raise LabError(f"Packing {source} failed: {proc.stderr.strip()[:400]}")


def _unpack(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["tar", "--use-compress-program=zstd -d", "-C", str(dest), "-xf", str(archive)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TAR_TIMEOUT_SEC, check=False)
    if proc.returncode != 0:
        raise LabError(f"Unpacking {archive.name} failed: {proc.stderr.strip()[:400]}")


# --------------------------------------------------------------------------
# Git summary (for the manifest and the Lab list)
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def repo_summary(src: Path) -> list[dict[str, Any]]:
    """Git repos directly under *src* (and *src* itself): branch, HEAD, dirty files."""
    candidates = [src, *sorted(p for p in src.iterdir() if p.is_dir())] if src.is_dir() else []
    repos: list[dict[str, Any]] = []
    for path in candidates:
        if not (path / ".git").exists():
            continue
        porcelain = _git(path, "status", "--porcelain")
        ahead = _git(path, "rev-list", "--count", "@{upstream}..HEAD")
        repos.append(
            {
                "path": "." if path == src else path.name,
                "branch": _git(path, "branch", "--show-current"),
                "head": _git(path, "rev-parse", "--short", "HEAD"),
                "remote": _git(path, "remote", "get-url", "origin"),
                "dirty": len([line for line in porcelain.splitlines() if line.strip()]),
                # Commits not on the remote; None when the branch tracks nothing.
                "ahead": int(ahead) if ahead.isdigit() else None,
            }
        )
    return repos


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _read_manifest(snap: Path) -> dict[str, Any]:
    try:
        data = json.loads((snap / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LabError(f"Snapshot {snap.name} has no readable manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise LabError(f"Snapshot {snap.name} manifest is not an object")
    return data


def _snapshot_path(name: str, root: Path | None) -> Path:
    return (root or snapshot_root()) / _check_name(name)


def save(
    name: str | None = None,
    *,
    root: Path | None = None,
    scratch: Path | None = None,
    include_dsh: bool = True,
    include_envs: bool = False,
    excludes: tuple[str, ...] = (),
    overwrite: bool = False,
) -> dict[str, Any]:
    """Snapshot ``$SCRATCH/src`` (+ dsh state) into ``<root>/<name>``.

    *include_envs* keeps ``.pixi``/``.venv`` and caches; *excludes* adds tar
    patterns. Writes into a staging dir and swaps it in, so a failed save
    never leaves a half-written snapshot under *name*.
    """
    from canfar_lab.core.project import _assert_save_space, _replace_dir

    _need_tools()
    scratch = _require_scratch(scratch)
    name = _check_name(name or default_name())
    root = root or snapshot_root()
    dest = root / name
    if dest.exists() and not overwrite:
        raise LabError(f"Snapshot {name!r} already exists.", hint="Pick another name or overwrite")
    src = scratch / "src"
    state = dsh_state_dir(scratch)
    has_src = src.is_dir()
    has_state = include_dsh and state.is_dir()
    if not has_src and not has_state:
        raise LabError(f"Nothing to save: no {src} and no dsh state.")

    root.mkdir(parents=True, exist_ok=True)
    _assert_save_space(root)
    patterns = (() if include_envs else DEFAULT_EXCLUDES) + tuple(excludes)
    staging = root / f".{name}.staging-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        if has_src:
            _pack(src, staging / SRC_ARCHIVE, patterns)
        if has_state:
            _pack(state, staging / DSH_ARCHIVE, ())
        manifest = {
            "format": FORMAT_VERSION,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user": os.environ.get("USER", "").strip() or "unknown",
            "session_id": os.environ.get("SKAHA_SESSION_ID", "") or os.environ.get("HOSTNAME", ""),
            "src": str(src) if has_src else None,
            "src_bytes": (staging / SRC_ARCHIVE).stat().st_size if has_src else 0,
            "dsh_state": str(state) if has_state else None,
            "dsh_bytes": (staging / DSH_ARCHIVE).stat().st_size if has_state else 0,
            "excludes": list(patterns),
            "repos": repo_summary(src) if has_src else [],
        }
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _replace_dir(staging, dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"ok": True, "path": str(dest), **manifest}


def list_snapshots(root: Path | None = None) -> list[dict[str, Any]]:
    """Snapshots under *root*, newest first. Unreadable entries are skipped."""
    root = root or snapshot_root()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if entry.name.startswith(".") or not (entry / MANIFEST).is_file():
            continue
        try:
            manifest = _read_manifest(entry)
        except LabError:
            continue
        rows.append({**manifest, "path": str(entry)})
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows


def get_snapshot(name: str, root: Path | None = None) -> dict[str, Any]:
    snap = _snapshot_path(name, root)
    if not (snap / MANIFEST).is_file():
        raise LabError(f"Snapshot {name!r} not found under {snap.parent}.")
    return {**_read_manifest(snap), "path": str(snap)}


def delete(name: str, root: Path | None = None) -> dict[str, Any]:
    snap = _snapshot_path(name, root)
    if not (snap / MANIFEST).is_file():
        raise LabError(f"Snapshot {name!r} not found under {snap.parent}.")
    shutil.rmtree(snap)
    return {"ok": True, "name": name, "deleted": str(snap)}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def restore(
    name: str,
    *,
    root: Path | None = None,
    scratch: Path | None = None,
    include_dsh: bool = True,
    overwrite: bool = False,
    replace_dsh: bool = False,
) -> dict[str, Any]:
    """Unpack snapshot *name* into this session's ``$SCRATCH``.

    Each top-level entry of ``src`` is restored on its own: an entry that
    already exists is skipped unless *overwrite*, in which case the current
    copy is moved aside to ``$SCRATCH/.snapshot-replaced-<time>/`` (never
    deleted). dsh state is swapped in only when the live state dir is empty
    or *replace_dsh* (separate from *overwrite*, so replacing code never hides
    the running assistant's history); the old dir is kept beside it, and dsh
    must be restarted to pick the restored state up.
    """
    _need_tools()
    scratch = _require_scratch(scratch)
    snap = _snapshot_path(name, root)
    manifest = get_snapshot(name, root)
    staging = scratch / f".snapshot-restore-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    restored: list[str] = []
    skipped: list[str] = []
    replaced: list[str] = []
    dsh: dict[str, Any] = {"restored": False, "restart_required": False, "note": ""}
    stamp = _stamp()
    aside: Path | None = None
    try:
        if (snap / SRC_ARCHIVE).is_file():
            _unpack(snap / SRC_ARCHIVE, staging / "src")
            target = scratch / "src"
            target.mkdir(parents=True, exist_ok=True)
            for entry in sorted((staging / "src").iterdir()):
                dest = target / entry.name
                if dest.exists() or dest.is_symlink():
                    if not overwrite:
                        skipped.append(entry.name)
                        continue
                    if aside is None:  # unique even for two restores in one second
                        aside = Path(
                            tempfile.mkdtemp(prefix=f".snapshot-replaced-{stamp}-", dir=scratch)
                        )
                    dest.rename(aside / entry.name)
                    replaced.append(entry.name)
                entry.rename(dest)
                restored.append(entry.name)
        if include_dsh and (snap / DSH_ARCHIVE).is_file():
            state = dsh_state_dir(scratch)
            live = state.is_dir() and any(state.iterdir())
            if live and not replace_dsh:
                dsh["note"] = f"{state} already has state; kept it (replace_dsh to swap it)."
            else:
                _unpack(snap / DSH_ARCHIVE, staging / "dsh-state")
                state.parent.mkdir(parents=True, exist_ok=True)
                existed = state.exists()
                if existed:
                    kept = state.with_name(f"{state.name}.pre-restore-{stamp}-{os.getpid()}")
                    state.rename(kept)
                    dsh["previous"] = str(kept)
                (staging / "dsh-state").rename(state)
                dsh.update(restored=True, restart_required=existed, path=str(state))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "ok": True,
        "name": name,
        "src": str(scratch / "src"),
        "restored": restored,
        "skipped": skipped,
        "replaced": replaced,
        "replaced_dir": str(aside) if replaced and aside is not None else None,
        "dsh": dsh,
        "repos": manifest.get("repos") or [],
    }
