"""Where canfar-lab keeps its per-user state: ``~/.canfar/lab``.

Older releases used ``~/.astroai/lab`` (lab state) and ``~/.astroai/ray``
(cluster state). :func:`migrate_legacy_state` moves both under
``~/.canfar/lab`` once and leaves symlinks at the old paths, so an older image
sharing the same ``/arc`` home keeps working. It is cheap to call on every
command: after the first run it is two ``lstat`` calls.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
from pathlib import Path

LAB_REL = Path(".canfar") / "lab"
LEGACY_REL = Path(".astroai")
#: legacy dir name under ~/.astroai -> new location relative to ~/.canfar/lab
_LEGACY_DIRS = {"lab": Path("."), "ray": Path("ray")}


def lab_home(home: Path | None = None) -> Path:
    """``~/.canfar/lab``: config, keys, setup state, snapshots, logs."""
    return (home or Path.home()) / LAB_REL


def ray_home(home: Path | None = None) -> Path:
    """``~/.canfar/lab/ray``: cluster state shared with the ray-manager session."""
    return lab_home(home) / "ray"


def _stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _mtime(path: Path) -> float:
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def _merge_newest(src: Path, dest: Path, aside: Path, actions: list[str]) -> None:
    """Move every entry of *src* into *dest*; on a clash the newer file wins.

    The losing copy goes to the same relative path under *aside* (never
    deleted). Directories merge recursively. This handles both an ancient
    ``~/.canfar/lab`` left by a very old release (legacy files are newer) and
    a few fresh files an image wrote to ``~/.astroai/lab`` after the move
    (keys and settings that only exist at the new path stay put).
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir()):
        target = dest / entry.name
        if not (target.exists() or target.is_symlink()):
            shutil.move(str(entry), str(target))
            continue
        both_dirs = entry.is_dir() and not entry.is_symlink() and target.is_dir()
        if both_dirs and not target.is_symlink():
            _merge_newest(entry, target, aside / entry.name, actions)
            with contextlib.suppress(OSError):
                entry.rmdir()
            continue
        aside.mkdir(parents=True, exist_ok=True)
        if _mtime(entry) > _mtime(target):
            shutil.move(str(target), str(aside / entry.name))
            shutil.move(str(entry), str(target))
            actions.append(f"newer:{target} (older copy in {aside})")
        else:
            shutil.move(str(entry), str(aside / entry.name))
            actions.append(f"kept:{target} (older legacy copy in {aside})")


def migrate_legacy_state(home: Path | None = None) -> list[str]:
    """Move ``~/.astroai/{lab,ray}`` into ``~/.canfar/lab``; symlink the old paths.

    Idempotent and safe to run from two sessions at once (lock + re-check).
    ``lab`` moves first, so ``ray`` lands inside the new lab dir.
    """
    home = home or Path.home()
    legacy_root = home / LEGACY_REL
    pending = [
        name
        for name in _LEGACY_DIRS
        if (legacy_root / name).is_dir() and not (legacy_root / name).is_symlink()
    ]
    if not pending:
        return []
    from canfar_lab.core.pathlock import path_lock

    new_root = lab_home(home)
    new_root.parent.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    with path_lock(home / ".canfar" / ".lab-migrate.lock", timeout=30):
        for name in pending:  # dict order: lab, then ray
            old = legacy_root / name
            if not old.is_dir() or old.is_symlink():
                continue  # another session finished it while we waited
            dest = new_root / _LEGACY_DIRS[name]
            if dest.exists() and dest.resolve() == old.resolve():
                if not dest.is_symlink():
                    continue  # ~/.astroai itself links to ~/.canfar: already one dir
                dest.unlink()  # ~/.canfar/lab -> ~/.astroai/lab: drop the link, move the real dir
                actions.append(f"unlinked:{dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Atomic, and never nests: it replaces an empty dest dir and
                # fails on a non-empty one (an image may have created and
                # written to ~/.canfar/lab without holding this lock).
                os.rename(old, dest)
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.EXDEV):
                    raise
                aside = new_root.parent / f"lab.pre-migrate-{_stamp()}-{os.getpid()}" / name
                _merge_newest(old, dest, aside, actions)
                try:
                    old.rmdir()
                except OSError:
                    actions.append(f"kept:{old} (could not empty it; no symlink)")
                    continue
            actions.append(f"moved:{old}->{dest}")
            rel = os.path.relpath(dest, old.parent)
            old.symlink_to(rel, target_is_directory=True)
            actions.append(f"symlink:{old}->{rel}")
        new_root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(new_root, 0o700)
    return actions
