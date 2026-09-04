"""Exclusive O_EXCL lock files with staleness recovery.

NFS-safe locking primitive shared by agent setup, plugins, verify --fix, wipe,
and the Ray cluster control plane. ``flock`` is unreliable over NFS, so locks
are ``O_CREAT | O_EXCL`` files recording ``PID TIMESTAMP``; a lock whose
recorded PID is dead is treated as stale and broken.

One lock namespace per resource family — never nest different families.
Same-thread nesting is re-entrant (wipe→remove, update→plugins); other
threads and processes still contend on the lock file.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from astroai_lab.errors import LabError

LOCK_TIMEOUT_SEC = int(os.environ.get("ASTROAI_LAB_LOCK_TIMEOUT", "30"))

# path -> (thread_id, nesting depth) for same-thread re-entrancy
_local_holds: dict[str, tuple[int, int]] = {}
_local_holds_guard = threading.Lock()


def _holder_alive(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8").strip()
        pid = int(text.split()[0])
    except (OSError, ValueError, IndexError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_stale(path: Path) -> bool:
    """Stale when the recorded holder PID is dead or the file is unreadable."""
    if not path.is_file():
        return True
    return not _holder_alive(path)


def _nest_enter(key: str) -> bool:
    """Return True if this thread already holds *key* (bump depth)."""
    tid = threading.get_ident()
    with _local_holds_guard:
        held = _local_holds.get(key)
        if held is not None and held[0] == tid:
            _local_holds[key] = (tid, held[1] + 1)
            return True
    return False


def _nest_exit(key: str) -> bool:
    """Pop one nesting level. Return True if the outer hold was released."""
    with _local_holds_guard:
        held = _local_holds.get(key)
        if held is None:
            return True
        tid, depth = held
        if tid != threading.get_ident():
            return False
        if depth <= 1:
            del _local_holds[key]
            return True
        _local_holds[key] = (tid, depth - 1)
        return False


@contextmanager
def path_lock(
    path: Path,
    *,
    timeout: float | None = None,
    busy_hint: str = "Another lab action holds this lock",
) -> Iterator[None]:
    """Acquire an exclusive lock file, breaking stale locks after *timeout*.

    Same-thread re-entrant: nested ``with path_lock(same_path)`` in one thread
    succeeds without a second file (wipe→remove, update→plugins). Other
    threads/processes still serialize on the O_EXCL file.
    """
    timeout = LOCK_TIMEOUT_SEC if timeout is None else timeout
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path)
    if _nest_enter(key):
        try:
            yield
        finally:
            _nest_exit(key)
        return

    deadline = time.monotonic() + timeout
    fd: int | None = None
    my_pid = os.getpid()
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, f"{my_pid} {time.time()}\n".encode())
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                if _lock_is_stale(path):
                    path.unlink(missing_ok=True)
                    continue
                raise LabError(
                    busy_hint,
                    hint=f"Wait or remove stale lock: {path}",
                ) from None
            time.sleep(0.25)
    with _local_holds_guard:
        _local_holds[key] = (threading.get_ident(), 1)
    try:
        yield
    finally:
        released = _nest_exit(key)
        if released:
            try:
                text = path.read_text(encoding="utf-8").strip()
                owner = text.split()[0] if text else ""
                if owner == str(my_pid):
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        if fd is not None:
            os.close(fd)
