"""Exclusive O_EXCL lock files with staleness recovery.

NFS-safe locking primitive shared by agent setup, plugins, verify --fix, wipe,
and the Ray cluster control plane. ``flock`` is unreliable over NFS, so locks
are ``O_CREAT | O_EXCL`` files recording ``HOST PID TIMESTAMP``; a lock is
stale when (same host and PID is dead) or the wall-clock age exceeds the
stale threshold. Cross-pod holders on shared ``/arc/home`` are never treated
as dead just because ``os.kill`` fails in this PID namespace.

One lock namespace per resource family — never nest different families.
Same-thread nesting is re-entrant (wipe→remove, update→plugins); other
threads and processes still contend on the lock file.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from canfar_lab.errors import LabError

LOCK_TIMEOUT_SEC = int(os.environ.get("CANFAR_LAB_LOCK_TIMEOUT", "30"))
# Cross-host / crashed-holder recovery. Keep well above normal critical sections.
LOCK_STALE_AGE_SEC = int(os.environ.get("CANFAR_LAB_LOCK_STALE_AGE", "600"))

# path -> (thread_id, nesting depth) for same-thread re-entrancy
_local_holds: dict[str, tuple[int, int]] = {}
_local_holds_guard = threading.Lock()


def _hostname() -> str:
    return socket.gethostname() or "unknown"


def _parse_lock(path: Path) -> tuple[str | None, int | None, float | None]:
    """Return (host, pid, timestamp) from a lock file; unknowns are None."""
    try:
        text = path.read_text(encoding="utf-8").strip()
        parts = text.split()
    except OSError:
        return None, None, None
    if len(parts) >= 3:
        try:
            return parts[0], int(parts[1]), float(parts[2])
        except ValueError:
            return None, None, None
    if len(parts) == 2:
        # Legacy ``PID TIMESTAMP`` (pre-host field).
        try:
            return None, int(parts[0]), float(parts[1])
        except ValueError:
            return None, None, None
    if len(parts) == 1:
        try:
            return None, int(parts[0]), None
        except ValueError:
            return None, None, None
    return None, None, None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_stale(path: Path) -> bool:
    """Stale when unreadable, aged out, or same-host holder PID is dead."""
    if not path.is_file():
        return True
    host, pid, ts = _parse_lock(path)
    now = time.time()
    if ts is not None and (now - ts) >= LOCK_STALE_AGE_SEC:
        return True
    if pid is None:
        return True
    # Other host: only age (above) decides staleness — never os.kill.
    if host is not None and host != _hostname():
        return False
    # Same host, or legacy lock without host: local PID check.
    return not _pid_alive(pid)


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
) -> Generator[None, None, None]:
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
    my_host = _hostname()
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, f"{my_host} {my_pid} {time.time()}\n".encode())
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
                host, pid, _ts = _parse_lock(path)
                mine = pid == my_pid and (host is None or host == my_host)
                if mine:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        if fd is not None:
            os.close(fd)
