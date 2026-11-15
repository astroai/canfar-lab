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
#: Work dirs restore leaves inside the code dir; never packed into a snapshot.
INTERNAL_EXCLUDES = (".snapshot-restore-*", ".snapshot-replaced-*")
AUTO_PREFIX = "auto-"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TAR_TIMEOUT_SEC = int(os.environ.get("CANFAR_LAB_SNAPSHOT_TIMEOUT", "3600"))


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def snapshot_root() -> Path:
    """Where snapshots live: ``$CANFAR_LAB_SNAPSHOT_DIR`` or ``~/.canfar/lab/snapshots``."""
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


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise LabError(
            f"Invalid snapshot name {name!r}.",
            hint="Use letters, digits, '.', '_' or '-' (max 64, no leading punctuation).",
        )
    return name


def session_id() -> str:
    """This session's id (Skaha), else the pod/host name: groups automatic saves."""
    for key in ("skaha_sessionid", "SKAHA_SESSIONID", "SKAHA_SESSION_ID", "HOSTNAME"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    import socket

    return socket.gethostname() or "local"


def _sweep_staging(root: Path, max_age_sec: float = 3600) -> None:
    """Remove half-written ``.<name>.staging-<pid>`` dirs a killed save left on /arc."""
    import time

    now = time.time()
    for entry in root.glob(".*.staging-*"):
        try:
            if entry.is_dir() and now - entry.stat().st_mtime > max_age_sec:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def default_name() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------
# tar | zstd
# --------------------------------------------------------------------------


def _need_tools() -> None:
    for tool in ("tar", "zstd"):
        if shutil.which(tool) is None:
            raise LabError(f"{tool} not found — cannot pack or unpack snapshots.")


def _pack(
    source: Path, dest: Path, excludes: tuple[str, ...], timeout: float | None = None
) -> None:
    """Pack the *contents* of *source* into *dest* (tar + zstd)."""
    cmd = ["tar", "--use-compress-program=zstd -T0 -3", "-C", str(source)]
    cmd += [f"--exclude={pattern}" for pattern in excludes]
    cmd += ["-cf", str(dest), "."]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout or TAR_TIMEOUT_SEC, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise LabError(f"Packing {source} took longer than {exc.timeout:.0f} s; stopped.") from exc
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
    src: Path | None = None,
    auto: bool = False,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Snapshot the code directory (+ dsh state) into ``<root>/<name>``.

    *src* defaults to the workspace code dir (``$SCRATCH/src`` unless the user
    chose another in Settings). *auto* marks a scheduled snapshot, which
    :func:`prune_auto` may later delete; manual snapshots are never pruned.

    *include_envs* keeps ``.pixi``/``.venv`` and caches; *excludes* adds tar
    patterns. Writes into a staging dir and swaps it in, so a failed save
    never leaves a half-written snapshot under *name*.
    """
    from canfar_lab.core.project import _assert_save_space, _replace_dir

    _need_tools()
    from canfar_lab.session import workspace

    scratch = scratch or scratch_dir()
    name = _check_name(name or ((AUTO_PREFIX if auto else "") + default_name()))
    root = root or snapshot_root()
    dest = root / name
    if dest.exists() and not overwrite:
        raise LabError(f"Snapshot {name!r} already exists.", hint="Pick another name or overwrite")
    src = src or workspace.code_dir(scratch=scratch)
    state = dsh_state_dir(scratch) if scratch is not None else None
    has_src = src.is_dir() and any(not p.name.startswith(".") for p in src.iterdir())
    has_state = include_dsh and state is not None and state.is_dir()
    if not has_src and not has_state:
        raise LabError(f"Nothing to save: no {src} and no dsh state.")

    root.mkdir(parents=True, exist_ok=True)
    _assert_save_space(root)
    patterns = (() if include_envs else DEFAULT_EXCLUDES) + INTERNAL_EXCLUDES + tuple(excludes)
    _sweep_staging(root)
    staging = root / f".{name}.staging-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        if has_src:
            _pack(src, staging / SRC_ARCHIVE, patterns, timeout)
        if has_state:
            assert state is not None
            _pack(state, staging / DSH_ARCHIVE, (), timeout)
        manifest = {
            "format": FORMAT_VERSION,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user": os.environ.get("USER", "").strip() or "unknown",
            "session_id": session_id(),
            "src": str(src) if has_src else None,
            "src_bytes": (staging / SRC_ARCHIVE).stat().st_size if has_src else 0,
            "dsh_state": str(state) if has_state else None,
            "dsh_bytes": (staging / DSH_ARCHIVE).stat().st_size if has_state else 0,
            "excludes": list(patterns),
            "repos": repo_summary(src) if has_src else [],
            "auto": auto,
            "fingerprint": fingerprint(src, patterns) if has_src else None,
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
    target: Path | None = None,
) -> dict[str, Any]:
    """Unpack snapshot *name* into the code directory (*target*, default: workspace).

    Each top-level entry of ``src`` is restored on its own: an entry that
    already exists is skipped unless *overwrite*, in which case the current
    copy is moved aside to ``<code dir>/.snapshot-replaced-<time>/`` (never
    deleted). dsh state is swapped in only when the live state dir is empty
    or *replace_dsh* (separate from *overwrite*, so replacing code never hides
    the running assistant's history); the old dir is kept beside it, and dsh
    must be restarted to pick the restored state up.
    """
    _need_tools()
    from canfar_lab.session import workspace

    scratch = scratch or scratch_dir()
    snap = _snapshot_path(name, root)
    manifest = get_snapshot(name, root)
    target = target or workspace.code_dir(scratch=scratch)
    target.mkdir(parents=True, exist_ok=True)
    # Stage inside the target so every rename stays on one filesystem.
    staging = Path(tempfile.mkdtemp(prefix=".snapshot-restore-", dir=target))
    restored: list[str] = []
    skipped: list[str] = []
    replaced: list[str] = []
    dsh: dict[str, Any] = {"restored": False, "restart_required": False, "note": ""}
    stamp = _stamp()
    aside: Path | None = None
    try:
        if (snap / SRC_ARCHIVE).is_file():
            _unpack(snap / SRC_ARCHIVE, staging / "src")
            for entry in sorted((staging / "src").iterdir()):
                dest = target / entry.name
                if dest.exists() or dest.is_symlink():
                    if not overwrite:
                        skipped.append(entry.name)
                        continue
                    if aside is None:  # unique even for two restores in one second
                        aside = Path(
                            tempfile.mkdtemp(prefix=f".snapshot-replaced-{stamp}-", dir=target)
                        )
                    dest.rename(aside / entry.name)
                    replaced.append(entry.name)
                entry.rename(dest)
                restored.append(entry.name)
        if include_dsh and scratch is not None and (snap / DSH_ARCHIVE).is_file():
            state = dsh_state_dir(scratch)
            live = state.is_dir() and any(state.iterdir())
            if live and not replace_dsh:
                dsh["note"] = f"{state} already has state; kept it (replace_dsh to swap it)."
            else:
                # Unpack beside the live dir: the code dir may be on another
                # filesystem (/arc) than dsh's state (/scratch).
                state.parent.mkdir(parents=True, exist_ok=True)
                fresh = Path(tempfile.mkdtemp(prefix=f".{state.name}.restore-", dir=state.parent))
                try:
                    _unpack(snap / DSH_ARCHIVE, fresh)
                    existed = state.exists()
                    if existed:
                        kept = state.with_name(f"{state.name}.pre-restore-{stamp}-{os.getpid()}")
                        state.rename(kept)
                        dsh["previous"] = str(kept)
                    fresh.rename(state)
                finally:
                    shutil.rmtree(fresh, ignore_errors=True)
                dsh.update(restored=True, restart_required=existed, path=str(state))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "ok": True,
        "name": name,
        "src": str(target),
        "restored": restored,
        "skipped": skipped,
        "replaced": replaced,
        "replaced_dir": str(aside) if replaced and aside is not None else None,
        "dsh": dsh,
        "repos": manifest.get("repos") or [],
    }


# --------------------------------------------------------------------------
# Automatic snapshots
# --------------------------------------------------------------------------


def fingerprint(src: Path, excludes: tuple[str, ...] = DEFAULT_EXCLUDES + INTERNAL_EXCLUDES) -> str:
    """Cheap change detector for *src*: file count, total size, newest mtime.

    Skips the excluded dirs (environments, caches) and ``.git/objects`` (git
    writes there on every fetch without changing the work). Not a content
    hash: good enough to skip an automatic snapshot when nothing moved.
    """
    import fnmatch
    import hashlib

    count = size = newest = 0
    names: list[str] = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [
            d
            for d in dirnames
            if not any(fnmatch.fnmatch(d, pat) for pat in excludes)
            and not (d == "objects" and dirpath.endswith(".git"))
        ]
        for fname in filenames:
            path = os.path.join(dirpath, fname)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            count += 1
            size += st.st_size
            newest = max(newest, int(st.st_mtime_ns))
        if dirpath == str(src):
            names = sorted(dirnames + filenames)
    digest = hashlib.sha256("\0".join(names).encode()).hexdigest()[:12]
    return f"{count}:{size}:{newest}:{digest}"


def _mine(row: dict[str, Any], session: str) -> bool:
    return bool(session) and str(row.get("session_id") or "") == session


def latest_fingerprint(root: Path | None = None, session: str | None = None) -> str | None:
    """Fingerprint of this session's newest automatic save (same exclude patterns).

    Another session sharing ``/arc`` saves different code; its snapshots must
    not make this session skip (or force) a save.
    """
    session = session_id() if session is None else session
    for row in list_snapshots(root):
        if row.get("auto") and _mine(row, session) and row.get("fingerprint"):
            return str(row["fingerprint"])
    return None


def latest_time(root: Path | None = None, session: str | None = None) -> float:
    """When this session last saved (manual or automatic), as epoch seconds; 0 if never."""
    session = session_id() if session is None else session
    for row in list_snapshots(root):
        if _mine(row, session):
            try:
                return datetime.fromisoformat(str(row.get("created_at"))).timestamp()
            except ValueError:
                return 0.0
    return 0.0


#: Other sessions whose newest automatic save is kept (older sessions' go).
KEEP_OTHER_SESSIONS = 10


def prune_auto(keep: int, root: Path | None = None, session: str | None = None) -> list[str]:
    """Delete automatic snapshots nobody needs. Manual ones are never touched.

    * this session: all but its newest *keep*;
    * every other session (which may still be running, or has ended and left
      its final save): only its newest is kept, for the
      ``KEEP_OTHER_SESSIONS`` most recent sessions.
    """
    session = session_id() if session is None else session
    autos = [r for r in list_snapshots(root) if r.get("auto")]  # newest first
    doomed: list[str] = []
    mine = [r for r in autos if _mine(r, session)]
    doomed += [str(r["name"]) for r in mine[max(keep, 1) :]]
    seen: list[str] = []
    for row in autos:
        if _mine(row, session):
            continue
        sid = str(row.get("session_id") or row["name"])
        if sid in seen:
            doomed.append(str(row["name"]))
            continue
        seen.append(sid)
        if len(seen) > KEEP_OTHER_SESSIONS:
            doomed.append(str(row["name"]))
    for name in doomed:
        delete(name, root)
    return doomed
