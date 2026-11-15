"""Where code and caches live, chosen once and kept across sessions.

Settings persist in ``~/.canfar/lab/workspace.json`` (0600)::

    {"code_dir": {"mode": "scratch" | "home" | "custom", "path": "/abs/path"},
     "cache_dir": null | "/abs/path",
     "autosave": {"enabled": true, "interval_min": 30, "keep": 5},
     "restore_at_start": "latest" | "ask" | "never"}

* ``scratch`` → ``$SCRATCH/src``: fast, wiped with the session; snapshots to
  ``/arc`` (automatic ones every ``interval_min`` and at shutdown) bring it back.
* ``home`` → ``~/src`` on ``/arc``: persistent, slower, counts against the
  home quota. Environments (``.pixi``/``.venv``) there are big; prefer scratch.
* ``custom`` → any absolute, writable directory (e.g. ``/arc/projects/<g>/me``).

Caches (pip/uv/pixi/npm, agent runtime state) default to ``$SCRATCH`` whatever
the code dir is; ``cache_dir`` moves them elsewhere but never onto the small
``/arc/home``. A file that does not exist means "never configured", and every
caller keeps its historical default.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from canfar_lab.errors import LabError

SETTINGS_FILE = "workspace.json"
MODES = ("scratch", "home", "custom")
RESTORE_CHOICES = ("latest", "ask", "never")
#: System locations a code or cache dir may never be (or sit under).
_FORBIDDEN_ROOTS = tuple(
    Path(p)
    for p in (
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib64",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/sys",
        "/usr",
        "/var",
    )
)


@dataclass
class Autosave:
    enabled: bool = True
    interval_min: int = 30
    keep: int = 5


@dataclass
class WorkspaceSettings:
    mode: str = "scratch"
    path: str | None = None  # only for mode == "custom"
    cache_dir: str | None = None
    autosave: Autosave = field(default_factory=Autosave)
    restore_at_start: str = "latest"
    configured: bool = False  # False: no settings file yet (defaults apply)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_dir": {"mode": self.mode, "path": self.path},
            "cache_dir": self.cache_dir,
            "autosave": asdict(self.autosave),
            "restore_at_start": self.restore_at_start,
        }


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def settings_path(home: Path | None = None) -> Path:
    from canfar_lab.core.state_home import lab_home

    return lab_home(home) / SETTINGS_FILE


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def load(home: Path | None = None) -> WorkspaceSettings:
    """Saved settings, or defaults (``configured=False``) when there are none.

    A damaged file falls back to defaults rather than breaking every shell.
    """
    path = settings_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return WorkspaceSettings()
    except (OSError, ValueError):
        return WorkspaceSettings(configured=True)
    if not isinstance(raw, dict):
        return WorkspaceSettings(configured=True)
    code: dict[str, Any] = raw["code_dir"] if isinstance(raw.get("code_dir"), dict) else {}
    mode = str(code["mode"]) if code.get("mode") in MODES else "scratch"
    custom = str(code["path"]) if isinstance(code.get("path"), str) else None
    auto: dict[str, Any] = raw["autosave"] if isinstance(raw.get("autosave"), dict) else {}
    restore = str(raw.get("restore_at_start") or "")
    cache = str(raw["cache_dir"]) if isinstance(raw.get("cache_dir"), str) else None
    return WorkspaceSettings(
        mode="scratch" if mode == "custom" and not custom else mode,
        path=custom if mode == "custom" else None,
        cache_dir=cache or None,
        autosave=Autosave(
            enabled=bool(auto.get("enabled", True)),
            interval_min=_int(auto.get("interval_min"), 30, 5, 24 * 60),
            keep=_int(auto.get("keep"), 5, 1, 50),
        ),
        restore_at_start=restore if restore in RESTORE_CHOICES else "latest",
        configured=True,
    )


def save(settings: WorkspaceSettings, home: Path | None = None) -> None:
    from canfar_lab.utils.json_utils import atomic_write_text

    path = settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(settings.to_dict(), indent=2) + "\n")
    os.chmod(path, 0o600)
    settings.configured = True


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def scratch_dir() -> Path | None:
    from canfar_lab.studio_paths import _scratch_dir

    return _scratch_dir(dict(os.environ))


def code_dir_for(
    mode: str, path: str | None = None, *, home: Path | None = None, scratch: Path | None = None
) -> Path | None:
    """The directory a mode points at; ``None`` for scratch mode without a scratch."""
    home = home or Path.home()
    if mode == "home":
        return home / "src"
    if mode == "custom":
        if not path:
            raise LabError("A custom code directory needs a path.")
        return Path(path).expanduser()
    if mode != "scratch":
        raise LabError(f"Unknown code directory mode {mode!r}.", hint="scratch, home or custom")
    scratch = scratch or scratch_dir()
    return scratch / "src" if scratch is not None else None


def configured_code_dir(home: Path | None = None, scratch: Path | None = None) -> Path | None:
    """The saved choice, or ``None`` when nothing was saved (callers keep their default)."""
    settings = load(home)
    if not settings.configured:
        return None
    target = code_dir_for(settings.mode, settings.path, home=home, scratch=scratch)
    if target is None or settings.mode == "scratch":
        return target
    try:  # a hand-edited file or a symlink changed since: never follow it blindly
        return validate_code_dir(target, home)
    except LabError:
        return None


def code_dir(home: Path | None = None, scratch: Path | None = None) -> Path:
    """Where code lives in this session: the saved choice, else ``$SCRATCH/src``.

    Falls back to the historical work dir resolution (``$SRCDIR``/``$WORK``)
    when there is neither a saved choice nor a scratch disk.
    """
    configured = configured_code_dir(home, scratch)
    if configured is not None:
        return configured
    default = code_dir_for("scratch", home=home, scratch=scratch)
    if default is not None:
        return default
    from canfar_lab.config.settings import get_settings

    return get_settings().resolve_work_dir()


def is_ephemeral(path: Path, scratch: Path | None = None) -> bool:
    """True when *path* is on this session's scratch disk (gone at session end)."""
    scratch = scratch or scratch_dir()
    if scratch is None:
        return False
    try:
        return path.resolve().is_relative_to(scratch.resolve())
    except OSError:
        return False


def on_home(path: Path, home: Path | None = None) -> bool:
    home = (home or Path.home()).resolve()
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    return resolved == home or resolved.is_relative_to(home)


def on_arc(path: Path) -> bool:
    try:
        return path.expanduser().resolve().is_relative_to(Path("/arc"))
    except OSError:
        return False


def _check_dir(path: Path, *, home: Path, what: str) -> Path:
    if not path.is_absolute():
        raise LabError(
            f"The {what} must be an absolute path.", hint="For example /arc/projects/<group>/me"
        )
    path = Path(os.path.normpath(path))
    resolved = path.resolve()
    if resolved == Path("/") or resolved == home.resolve():
        raise LabError(f"The {what} cannot be {resolved}: pick a folder inside it.")
    for root in _FORBIDDEN_ROOTS:
        if resolved == root or resolved.is_relative_to(root):
            raise LabError(f"The {what} cannot be under {root}.")
    if on_home(resolved, home):
        first = resolved.relative_to(home.resolve()).parts[0]
        if first.startswith("."):
            raise LabError(
                f"The {what} cannot be inside {home / first}: hidden folders in your home "
                "hold agent logins, keys and settings."
            )
    if resolved.exists() and not resolved.is_dir():
        raise LabError(f"{resolved} exists and is not a folder.")
    probe = resolved
    while not probe.exists():
        probe = probe.parent
    if not os.access(probe, os.W_OK | os.X_OK):
        raise LabError(f"You cannot write to {probe}.")
    return path


def validate_code_dir(path: Path, home: Path | None = None) -> Path:
    return _check_dir(path, home=home or Path.home(), what="code directory")


def validate_cache_dir(path: Path, home: Path | None = None) -> Path:
    home = home or Path.home()
    checked = _check_dir(path, home=home, what="cache directory")
    if on_home(checked, home):
        raise LabError(
            "Caches cannot live in your /arc home: it is small and slow.",
            hint="Use $SCRATCH (the default) or a folder under /arc/projects.",
        )
    return checked


def cache_base(home: Path | None = None) -> Path | None:
    """Custom root for caches and runtime state, or ``None`` for the default ($SCRATCH)."""
    raw = load(home).cache_dir
    if not raw:
        return None
    path = Path(raw)
    try:
        return validate_cache_dir(path, home)
    except LabError:
        return None  # a stale or now-invalid choice falls back to the default


# --------------------------------------------------------------------------
# Changing settings
# --------------------------------------------------------------------------


def describe(home: Path | None = None) -> dict[str, Any]:
    """Settings plus what they resolve to in this session, for the Settings page."""
    home = home or Path.home()
    settings = load(home)
    scratch = scratch_dir()
    current = code_dir(home, scratch)
    live = live_code_dir()
    choices = []
    for mode in MODES:
        target = None
        if mode != "custom":
            target = code_dir_for(mode, home=home, scratch=scratch)
        elif settings.path:
            target = Path(settings.path)
        choices.append({"mode": mode, "path": str(target) if target else None})
    base = cache_base(home)
    return {
        **settings.to_dict(),
        "configured": settings.configured,
        "code_path": str(current),
        "ephemeral": is_ephemeral(current, scratch),
        "on_arc": on_arc(current),
        "choices": choices,
        # Tools started before a change still run in the old directory.
        "restart_needed": bool(live) and Path(live) != current,
        "live_code_path": live or None,
        "cache_path": str(base) if base else (str(scratch) if scratch else None),
        "cache_default": base is None,
        "scratch": str(scratch) if scratch else None,
        "privacy": privacy(current) if current.exists() and on_arc(current) else None,
    }


def live_code_dir() -> str:
    """The folder the session's tools run in right now.

    The dsh image writes it to ``$CANFAR_LAB_LIVE_CODE_FILE`` each time it
    (re)starts them; elsewhere the process's own ``$SRCDIR`` is the answer.
    """
    raw = os.environ.get("CANFAR_LAB_LIVE_CODE_FILE", "").strip()
    if raw:
        try:
            return Path(raw).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.environ.get("SRCDIR", "").strip()


def set_code_dir(mode: str, path: str | None = None, home: Path | None = None) -> dict[str, Any]:
    """Save the code directory choice and create the folder. Returns old and new paths."""
    home = home or Path.home()
    settings = load(home)
    old = code_dir(home)
    target = code_dir_for(mode, path, home=home)
    if target is None:
        raise LabError("This session has no $SCRATCH, so the scratch option is unavailable.")
    target = validate_code_dir(target, home)
    target.mkdir(parents=True, exist_ok=True)
    if on_arc(target) and not is_ephemeral(target):
        _ensure_private_new(target)
    settings.mode = mode
    settings.path = str(target.resolve()) if mode == "custom" else None
    save(settings, home)
    return {
        "ok": True,
        "old": str(old),
        "new": str(target),
        "changed": old != target,
        **describe(home),
    }


def set_options(
    *,
    autosave: bool | None = None,
    interval_min: int | None = None,
    keep: int | None = None,
    restore_at_start: str | None = None,
    cache_dir: str | None = None,
    reset_cache_dir: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    home = home or Path.home()
    settings = load(home)
    if autosave is not None:
        settings.autosave.enabled = bool(autosave)
    if interval_min is not None:
        settings.autosave.interval_min = _int(interval_min, 30, 5, 24 * 60)
    if keep is not None:
        settings.autosave.keep = _int(keep, 5, 1, 50)
    if restore_at_start is not None:
        if restore_at_start not in RESTORE_CHOICES:
            raise LabError(f"restore_at_start must be one of {', '.join(RESTORE_CHOICES)}.")
        settings.restore_at_start = restore_at_start
    if reset_cache_dir:
        settings.cache_dir = None
    elif cache_dir is not None:
        target = validate_cache_dir(Path(cache_dir).expanduser(), home)
        target.mkdir(parents=True, exist_ok=True)
        settings.cache_dir = str(target)
    save(settings, home)
    return {"ok": True, **describe(home)}


# --------------------------------------------------------------------------
# Privacy of a code dir on /arc
# --------------------------------------------------------------------------


def _acl_entries(path: Path) -> list[str]:
    """Extended ACL entries (named users/groups) on *path*; [] when none or unknown."""
    if shutil.which("getfacl") is None:
        return []
    try:
        proc = subprocess.run(
            ["getfacl", "--omit-header", "--absolute-names", "--skip-base", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        line = line.split("#", 1)[0].strip()  # drop "#effective:" notes
        if not line:
            continue
        parts = line.split(":")
        if parts[0] == "default":
            out.append(line)  # inherited by new files: counts as sharing
        elif parts[0] in ("user", "group") and len(parts) >= 3 and parts[1]:
            out.append(line)  # a named user or group
    return out


def privacy(path: Path) -> dict[str, Any]:
    """Can anyone else read *path*? Mode bits plus named ACL entries."""
    try:
        st = path.stat()
    except OSError as exc:
        return {"private": None, "error": str(exc)}
    group = bool(st.st_mode & (stat.S_IRGRP | stat.S_IXGRP))
    other = bool(st.st_mode & (stat.S_IROTH | stat.S_IXOTH))
    acl = _acl_entries(path)
    return {
        "private": not (group or other or acl),
        "mode": oct(st.st_mode & 0o777),
        "group_access": group,
        "other_access": other,
        "acl": acl,
    }


def make_private(path: Path) -> dict[str, Any]:
    """Owner-only access on the folder itself (others can no longer enter it).

    Removes extended ACL entries on *path* only, not recursively: with the
    folder closed nobody else can reach what is inside.
    """
    if not path.is_dir():
        raise LabError(f"{path} is not a folder.")
    if path.stat().st_uid != os.getuid():
        raise LabError(f"{path} belongs to someone else; ask its owner.")
    os.chmod(path, 0o700)
    if shutil.which("setfacl"):
        subprocess.run(["setfacl", "-b", str(path)], capture_output=True, timeout=10, check=False)
    return {"ok": True, "path": str(path), **privacy(path)}


def _ensure_private_new(path: Path) -> None:
    """A folder we just created on /arc starts owner-only (existing ones are not touched)."""
    try:
        if not any(path.iterdir()):
            os.chmod(path, 0o700)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Moving projects between code dirs
# --------------------------------------------------------------------------


def copy_projects(src: Path, dest: Path, *, include_envs: bool = False) -> dict[str, Any]:
    """Copy each project in *src* into *dest*; an existing name in *dest* is skipped.

    Environments are path-dependent and rebuilt from lockfiles, so ``.pixi`` /
    ``.venv`` and caches are left behind unless *include_envs*. The originals
    stay where they are.
    """
    from canfar_lab.session.snapshots import DEFAULT_EXCLUDES

    if not src.is_dir():
        return {"ok": True, "copied": [], "skipped": [], "src": str(src), "dest": str(dest)}
    src_r, dest_r = src.resolve(), dest.resolve()
    if src_r == dest_r:
        raise LabError("The old and new code directories are the same.")
    if dest_r.is_relative_to(src_r) or src_r.is_relative_to(dest_r):
        raise LabError(
            "One code folder is inside the other, so copying would copy it into itself.",
            hint="Switch without copying, or pick a folder outside the current one.",
        )
    dest.mkdir(parents=True, exist_ok=True)
    ignore = None if include_envs else shutil.ignore_patterns(*DEFAULT_EXCLUDES)
    copied: list[str] = []
    skipped: list[str] = []
    for entry in sorted(src.iterdir()):
        if entry.name.startswith("."):
            continue
        target = dest / entry.name
        if target.exists() or target.is_symlink():
            skipped.append(entry.name)
            continue
        print(f"Copying {entry.name} …", flush=True)
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True, ignore=ignore)
        else:
            shutil.copy2(entry, target, follow_symlinks=False)
        copied.append(entry.name)
    return {"ok": True, "copied": copied, "skipped": skipped, "src": str(src), "dest": str(dest)}
