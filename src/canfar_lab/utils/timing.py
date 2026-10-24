"""Bounded waits and optional phase timings for status probes."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TypeVar

T = TypeVar("T")

PhaseCallback = Callable[[str, float, float], None]


class PhaseTimer:
    """Record named probe durations. ``callback(name, dt, total)`` is optional."""

    def __init__(self, callback: PhaseCallback | None = None) -> None:
        self.callback = callback
        self.origin = time.perf_counter()

    @contextmanager
    def phase(self, name: str) -> Generator[None, None, None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            if self.callback is not None:
                now = time.perf_counter()
                self.callback(name, now - t0, now - self.origin)


def call_with_timeout(
    fn: Callable[[], T],
    timeout_sec: float,
    default: T,
) -> T:
    """Run ``fn`` in a daemon thread; return ``default`` if it exceeds ``timeout_sec``.

    ThreadPoolExecutor workers are non-daemon and are joined at interpreter
    shutdown. A hung CADC/VOSpace call would then freeze ``canfar lab status``
    after the timeout had already fired. Daemon threads do not block exit.
    """
    box: list[T] = []
    err: list[BaseException] = []

    def _run() -> None:
        try:
            box.append(fn())
        except Exception as exc:  # noqa: BLE001 - re-raise on the caller thread
            err.append(exc)

    thread = threading.Thread(target=_run, name="lab-timeout", daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)
    if thread.is_alive() or not (box or err):
        return default
    if err:
        raise err[0]
    return box[0]
