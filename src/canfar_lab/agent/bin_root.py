"""Where agent CLIs live: home ($HOME, /arc/home on CANFAR) or this session's scratch.

Config and keys always stay on home. Caches stay on scratch when scratch exists.
The choice is remembered under ``~/.astroai/lab/bin-root.json`` and is only
offered when a scratch disk is mounted. A laptop keeps each agent's own paths.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path

from canfar_lab.errors import LabError

ROOT_HOME = "home"
ROOT_SCRATCH = "scratch"
ROOTS = (ROOT_HOME, ROOT_SCRATCH)
ROOT_FILE = "bin-root.json"

#: Directories upstream installers and ``opencode upgrade`` write into.
#: Symlinked onto the scratch bin when the remembered root is scratch, so a
#: later self-upgrade does not drop a second copy on home.
DROP_DIR_RELS: tuple[str, ...] = (
    ".local/bin",
    ".kilo/bin",
    ".opencode/bin",
    ".hermes/bin",
    ".agy/bin",
)

_FAST_IO_BEGIN = "# canfar-lab: fast-io begin"
_FAST_IO_END = "# canfar-lab: fast-io end"
_BASHRC_SOURCE = (
    '[[ -f "${HOME}/.astroai/lab/agent-env.sh" ]] && source "${HOME}/.astroai/lab/agent-env.sh"'
)


def bin_root_path(home: Path) -> Path:
    return home / ".astroai" / "lab" / ROOT_FILE


def read_bin_root(home: Path) -> str | None:
    path = bin_root_path(home)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    root = data.get("root") if isinstance(data, dict) else None
    return root if root in ROOTS else None


def write_bin_root(home: Path, root: str) -> None:
    if root not in ROOTS:
        raise LabError(
            f"Unknown install root {root!r}.",
            hint="canfar lab agent install --root home|scratch",
        )
    path = bin_root_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"root": root}) + "\n", encoding="utf-8")


def effective_root(home: Path, scratch: Path | None) -> str:
    """Remembered choice, or scratch when a session disk exists.

    Without scratch the toggle is not offered. Callers leave laptop install
    paths on the existing runtime directory.
    """
    if scratch is None:
        return ROOT_HOME
    return read_bin_root(home) or ROOT_SCRATCH


def managed_bin_dir(home: Path, scratch: Path | None) -> Path:
    """Directory new CLIs land in. ``CANFAR_LAB_BIN_DIR`` wins."""
    raw = os.environ.get("CANFAR_LAB_BIN_DIR", "").strip()
    if raw:
        return Path(raw)
    if scratch is not None and effective_root(home, scratch) == ROOT_SCRATCH:
        return scratch / ".local" / "bin"
    if scratch is not None:
        return home / ".local" / "bin"
    return home / ".local" / "bin"


def _same(path: Path, other: Path) -> bool:
    try:
        return path.resolve() == other.resolve()
    except OSError:
        return False


def _make_real_dir(src: Path, rel: str, *, dry_run: bool) -> list[str]:
    """Home root: drop directories stay real so upgrades write on home."""
    if src.is_symlink():
        try:
            target = src.resolve(strict=False)
        except OSError:
            target = None
        if dry_run:
            return [f"unlink:{rel}"]
        src.unlink()
        src.mkdir(parents=True, exist_ok=True)
        # ~/.local/bin is the PATH directory. Keep the binaries that the
        # scratch symlink was pointing at; other drop dirs just become real.
        copy_bins = (
            rel == ".local/bin"
            and target is not None
            and target.is_dir()
            and not _same(target, src)
        )
        if copy_bins:
            for child in target.iterdir():
                if not (child.is_file() or child.is_symlink()):
                    continue
                dest = src / child.name
                if dest.exists() or dest.is_symlink():
                    continue
                shutil.copy2(child, dest, follow_symlinks=True)
        return [f"unlink:{rel}"]
    if src.is_dir():
        return []
    if src.exists():
        return [f"kept:{rel}"]
    if dry_run:
        return [f"mkdir:{rel}"]
    src.mkdir(parents=True, exist_ok=True)
    return [f"mkdir:{rel}"]


def _symlink_drop(src: Path, bin_dir: Path, rel: str, *, dry_run: bool) -> list[str]:
    """Point one home drop directory at the scratch bin."""
    if _same(src, bin_dir):
        return []
    if src.is_symlink():
        if _same(src, bin_dir) and bin_dir.is_dir():
            return []
        if dry_run:
            return [f"relink:{rel}"]
        src.unlink()
        bin_dir.mkdir(parents=True, exist_ok=True)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.symlink_to(bin_dir, target_is_directory=True)
        return [f"relink:{rel}"]
    if src.is_dir():
        children = list(src.iterdir())
        if any(child.is_dir() and not child.is_symlink() for child in children):
            return [f"kept:{rel}"]
        if dry_run:
            return [f"link:{rel}"]
        bin_dir.mkdir(parents=True, exist_ok=True)
        for child in children:
            dest = bin_dir / child.name
            if dest.exists() or dest.is_symlink():
                child.unlink()
                continue
            shutil.move(str(child), str(dest))
        if any(src.iterdir()):
            return [f"kept:{rel}"]
        src.rmdir()
        src.symlink_to(bin_dir, target_is_directory=True)
        return [f"link:{rel}"]
    if src.exists():
        return [f"kept:{rel}"]
    if dry_run:
        return [f"link:{rel}"]
    bin_dir.mkdir(parents=True, exist_ok=True)
    src.parent.mkdir(parents=True, exist_ok=True)
    src.symlink_to(bin_dir, target_is_directory=True)
    return [f"link:{rel}"]


def align_home_bin_drops(
    home: Path,
    *,
    scratch: Path | None,
    root: str,
    dry_run: bool = False,
) -> list[str]:
    """Make upgrade drop directories follow the remembered root.

    No-op without scratch (a laptop). Scratch root symlinks the known drop
    directories onto the scratch bin. Home root leaves them as real directories.
    """
    if scratch is None:
        return []
    bin_dir = managed_bin_dir(home, scratch)
    actions: list[str] = []
    for rel in DROP_DIR_RELS:
        src = home / rel
        if root == ROOT_HOME:
            actions.extend(_make_real_dir(src, rel, dry_run=dry_run))
        else:
            actions.extend(_symlink_drop(src, bin_dir, rel, dry_run=dry_run))
    return actions


def _strip_fast_io(text: str) -> str:
    begin = text.find(_FAST_IO_BEGIN)
    end = text.find(_FAST_IO_END)
    if begin == -1 or end == -1 or end < begin:
        return text
    after = end + len(_FAST_IO_END)
    if after < len(text) and text[after] == "\n":
        after += 1
    body = (text[:begin] + text[after:]).strip()
    return (body + "\n") if body else ""


def _render_fast_io() -> str:
    from canfar_lab.shell.session_env import resolve_session_env

    env = resolve_session_env(ensure=False)
    bin_dir = str(env.canfar_lab_bin_dir)
    lines = [
        _FAST_IO_BEGIN,
        f"export CANFAR_LAB_BIN_DIR={_sh(bin_dir)}",
        f"export NPM_CONFIG_PREFIX={_sh(str(env.canfar_lab_npm_prefix))}",
        f"export XDG_CACHE_HOME={_sh(str(env.xdg_cache_home))}",
        f"export XDG_DATA_HOME={_sh(str(env.xdg_data_home))}",
        f"export XDG_STATE_HOME={_sh(str(env.xdg_state_home))}",
        f"export NPM_CONFIG_CACHE={_sh(str(env.npm_config_cache))}",
        f"export UV_CACHE_DIR={_sh(str(env.uv_cache_dir))}",
        f"export PIP_CACHE_DIR={_sh(str(env.pip_cache_dir))}",
        f"export TMPDIR={_sh(str(env.tmpdir))}",
        f"export PUPPETEER_CACHE_DIR={_sh(str(env.xdg_cache_home / 'puppeteer'))}",
        'case ":${PATH}:" in',
        '  *":${CANFAR_LAB_BIN_DIR}:"*) ;;',
        '  *) export PATH="${CANFAR_LAB_BIN_DIR}:${PATH}" ;;',
        "esac",
        _FAST_IO_END,
    ]
    return "\n".join(lines) + "\n"


def _sh(value: str) -> str:
    return shlex.quote(value)


def sync_fast_io_env(home: Path) -> str | None:
    """Export the bin root and scratch caches from ``agent-env.sh``.

    Interactive shells already source that file. Without scratch the block is
    removed so a laptop shell is left on its own paths.
    """
    from canfar_lab.shell.session_env import install_scratch_dir

    hook = home / ".astroai" / "lab" / "agent-env.sh"
    current = hook.read_text(encoding="utf-8") if hook.is_file() else ""
    base = _strip_fast_io(current)
    scratch = install_scratch_dir()
    if scratch is None:
        if base == current:
            return None
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(base, encoding="utf-8")
        return "cleared"
    block = _render_fast_io()
    text = base.rstrip() + ("\n\n" if base.strip() else "") + block
    if text == current:
        return None
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(text, encoding="utf-8")
    _ensure_bashrc_hook(home)
    return "updated"


def _ensure_bashrc_hook(home: Path) -> None:
    bashrc = home / ".bashrc"
    if not bashrc.is_file():
        return
    text = bashrc.read_text(encoding="utf-8")
    if _BASHRC_SOURCE in text:
        return
    with bashrc.open("a", encoding="utf-8") as fh:
        fh.write(f"\n# canfar lab agent setup\n{_BASHRC_SOURCE}\n")


def set_bin_root(home: Path, root: str, *, dry_run: bool = False) -> list[str]:
    """Remember home or scratch and point drop directories at that root."""
    from canfar_lab.shell.session_env import install_scratch_dir

    scratch = install_scratch_dir()
    if scratch is None:
        raise LabError(
            "Home vs scratch is a CANFAR session choice.",
            hint="On a laptop, agents install in their usual places.",
        )
    if root not in ROOTS:
        raise LabError(
            f"Unknown install root {root!r}.",
            hint="canfar lab agent install --root home|scratch",
        )
    actions = [f"root:{root}"]
    if not dry_run:
        write_bin_root(home, root)
    actions.extend(align_home_bin_drops(home, scratch=scratch, root=root, dry_run=dry_run))
    if not dry_run:
        synced = sync_fast_io_env(home)
        if synced:
            actions.append(f"env:{synced}")
    return actions


def second_home_binary_issues(home: Path, scratch: Path) -> list[str]:
    """A real file on home that also exists in the scratch bin."""
    bin_dir = scratch / ".local" / "bin"
    issues: list[str] = []
    for rel in DROP_DIR_RELS:
        src = home / rel
        if src.is_symlink() or not src.is_dir():
            continue
        try:
            children = list(src.iterdir())
        except OSError:
            continue
        for child in children:
            if not (child.is_file() or child.is_symlink()):
                continue
            if (bin_dir / child.name).exists():
                issues.append(f"second binary on home: {child} (install root is scratch)")
    return issues


def layout_report(home: Path) -> dict[str, object]:
    """Root, cache placement, and home-binary clashes. No secrets."""
    from canfar_lab.core.paths import quota_used_pct
    from canfar_lab.shell.session_env import install_scratch_dir, resolve_session_env

    scratch = install_scratch_dir()
    root = effective_root(home, scratch)
    env = resolve_session_env(ensure=False)
    issues: list[str] = []
    warnings: list[str] = []
    if scratch is not None:
        try:
            cache = env.xdg_cache_home.resolve()
            scratch_res = scratch.resolve()
            on_scratch = cache == scratch_res or scratch_res in cache.parents
        except OSError:
            on_scratch = False
        if not on_scratch:
            issues.append(f"XDG cache is not on scratch ({env.xdg_cache_home})")
        if root == ROOT_SCRATCH:
            issues.extend(second_home_binary_issues(home, scratch))
        if root == ROOT_HOME:
            quota = quota_used_pct(home)
            if quota is not None and quota >= 90:
                warnings.append(
                    f"Home quota {quota}% — binaries on home are slower and share a small disk"
                )
    return {
        "root": root,
        "choice": scratch is not None,
        # Scratch is the CANFAR default. Laptops have no home-vs-scratch choice.
        "default_root": ROOT_SCRATCH if scratch is not None else None,
        "bin_dir": str(env.canfar_lab_bin_dir),
        "warnings": warnings,
        "issues": issues,
    }


def settings_payload(home: Path) -> dict[str, object]:
    """Studio settings section: root, key names, doctor lines. Never secret values."""
    from canfar_lab.agent.keys import status

    report = layout_report(home)
    keys = [
        {
            "key": row["key"],
            "label": row.get("label") or row["key"],
            "present": bool(row["present"]),
            "sources": list(row["sources"]),
            "signup_url": row.get("signup_url") or "",
        }
        for row in status(home)
    ]
    return {**report, "keys": keys}
