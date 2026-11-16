"""Agent config files lab wrote, so a refresh never overwrites the user's edits.

Each write records the file's hash. A forced write then replaces a file only
while it still matches that hash. A file the user changed, or one written
before this record existed, counts as theirs:

* inside :func:`keeping_user_edits` (``agent update``) it is left alone and
  reported;
* otherwise (``agent setup --force``, an explicit reset) it is copied to
  ``<name>.bak`` first.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path

RECORD_NAME = "agent-files.json"

_kept: ContextVar[list[Path] | None] = ContextVar("agent_files_kept", default=None)


def _record_path() -> Path:
    from canfar_lab import config_dir

    return config_dir() / RECORD_NAME


def _load() -> dict[str, str]:
    try:
        data = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _remember(dst: Path, content: bytes) -> None:
    record = _load()
    record[str(dst)] = hashlib.sha256(content).hexdigest()
    path = _record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass  # Without the record, later refreshes keep the file: the safe side.


@contextlib.contextmanager
def keeping_user_edits() -> Generator[list[Path]]:
    """Within this block, forced writes skip user-edited files and list them."""
    kept: list[Path] = []
    token = _kept.set(kept)
    try:
        yield kept
    finally:
        _kept.reset(token)


def write_bytes(dst: Path, content: bytes, *, force: bool, dry_run: bool) -> bool:
    """Write ``content`` to ``dst`` unless that would lose the user's edits."""
    if dst.is_file():
        if not force:
            return False
        current = dst.read_bytes()
        if current == content:
            if not dry_run:
                _remember(dst, content)
            return False
        ours = _load().get(str(dst)) == hashlib.sha256(current).hexdigest()
        if not ours:
            kept = _kept.get()
            if kept is not None:
                kept.append(dst)
                return False
            if not dry_run:
                shutil.copy2(dst, dst.with_name(dst.name + ".bak"))
    if dry_run:
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(content)
    _remember(dst, content)
    return True


def copy_file(src: Path, dst: Path, *, force: bool, dry_run: bool) -> bool:
    if not src.is_file():
        return False
    wrote = write_bytes(dst, src.read_bytes(), force=force, dry_run=dry_run)
    if wrote and not dry_run:
        shutil.copymode(src, dst)
    return wrote
