"""Single-flight job runner with a line log the page streams over SSE.

Lab actions (install an agent, save a snapshot, start the cluster) run one at
a time on a worker thread. Their output comes from Python (``ui`` prints) and
from installer subprocesses that inherit file descriptors 1 and 2, so the
runner points those descriptors at a pipe for the length of the job and
collects whatever arrives as log lines. The HTTP server must therefore log
through :attr:`JobRunner.log_fd` (a copy of the original stderr), never
``sys.stderr``.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from canfar_lab.errors import LabError

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
MAX_LINES = 5000
KEEP_JOBS = 20


class Busy(LabError):
    """Another job is still running."""


@dataclass
class Job:
    id: int
    kind: str
    label: str
    state: str = "running"  # running | ok | failed
    started: float = field(default_factory=time.time)
    ended: float | None = None
    lines: list[str] = field(default_factory=list)
    dropped: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    hint: str | None = None

    def to_dict(self, *, tail: int = 0) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "started": self.started,
            "ended": self.ended,
            "line_count": self.dropped + len(self.lines),
            "tail": self.lines[-tail:] if tail else [],
            "result": self.result,
            "error": self.error,
            "hint": self.hint,
        }


class JobRunner:
    def __init__(self, *, capture: bool = True) -> None:
        self._lock = threading.Condition()
        self._ids = itertools.count(1)
        self._jobs: dict[int, Job] = {}
        self._current: Job | None = None
        self._capture = capture
        # Where the server logs while a job owns fds 1/2.
        self.log_fd = os.dup(2) if capture else 2

    # -- queries ---------------------------------------------------------

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def current(self) -> Job | None:
        with self._lock:
            return self._current

    def latest(self) -> Job | None:
        with self._lock:
            if self._current is not None:
                return self._current
            return self._jobs[max(self._jobs)] if self._jobs else None

    def recent(self) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in sorted(self._jobs, reverse=True)]

    # -- run -------------------------------------------------------------

    def start(self, kind: str, label: str, fn: Callable[[Job], dict[str, Any]]) -> Job:
        """Start *fn* on a worker thread; raise :class:`Busy` if a job is running."""
        with self._lock:
            if self._current is not None:
                raise Busy(
                    f"Busy: {self._current.label} is still running.",
                    hint="Wait for it to finish, then try again.",
                )
            job = Job(id=next(self._ids), kind=kind, label=label)
            self._jobs[job.id] = job
            self._current = job
            for old in sorted(self._jobs)[:-KEEP_JOBS]:
                del self._jobs[old]
        threading.Thread(
            target=self._run, args=(job, fn), name=f"lab-job-{job.id}", daemon=True
        ).start()
        return job

    def wait(self, job: Job, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            while job.state == "running":
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    return False
                self._lock.wait(left)
        return True

    def log(self, job: Job, text: str) -> None:
        """Append text (may hold several lines) to *job*'s log."""
        lines = [ANSI_RE.sub("", ln).rstrip() for ln in re.split(r"\r\n|\r|\n", text)]
        with self._lock:
            for line in lines:
                if not line:
                    continue
                job.lines.append(line)
            overflow = len(job.lines) - MAX_LINES
            if overflow > 0:
                del job.lines[:overflow]
                job.dropped += overflow
            self._lock.notify_all()

    def lines_since(
        self, job: Job, index: int, *, timeout: float
    ) -> tuple[list[tuple[int, str]], bool]:
        """Lines numbered >= *index* (waiting up to *timeout* for some), and whether done."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                total = job.dropped + len(job.lines)
                done = job.state != "running"
                if index < total or done:
                    start = max(index, job.dropped)
                    rows = [(i, job.lines[i - job.dropped]) for i in range(start, total)]
                    return rows, done
                left = deadline - time.monotonic()
                if left <= 0:
                    return [], False
                self._lock.wait(left)

    def _run(self, job: Job, fn: Callable[[Job], dict[str, Any]]) -> None:
        state, result, error, hint = "failed", None, None, None
        try:
            with self._captured(job):
                result = fn(job)
            ok = not isinstance(result, dict) or result.get("ok", True) is not False
            state = "ok" if ok else "failed"
            if not ok and isinstance(result, dict):
                errors = result.get("errors") or []
                error = "; ".join(str(e) for e in errors) or "Finished with errors."
        except LabError as exc:
            error, hint = exc.message, exc.hint
        except Exception as exc:  # noqa: BLE001 — surface anything to the page
            error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            job.state, job.result, job.error, job.hint = state, result, error, hint
            job.ended = time.time()
            self._current = None
            self._lock.notify_all()

    @contextlib.contextmanager
    def _captured(self, job: Job) -> Generator[None, None, None]:
        if not self._capture:
            yield
            return
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        read_fd, write_fd = os.pipe()
        saved = (os.dup(1), os.dup(2))
        os.dup2(write_fd, 1)
        os.dup2(write_fd, 2)
        os.close(write_fd)

        def _pump() -> None:
            buf = b""
            while True:
                try:
                    chunk = os.read(read_fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                *complete, buf = re.split(rb"\r\n|\r|\n", buf)
                if complete:
                    self.log(job, "\n".join(c.decode("utf-8", "replace") for c in complete))
            if buf:
                self.log(job, buf.decode("utf-8", "replace"))
            os.close(read_fd)

        reader = threading.Thread(target=_pump, name=f"lab-job-{job.id}-log", daemon=True)
        reader.start()
        # sys.stdout may not be fd 1 (pytest, a wrapped console): point the
        # Python streams at the redirected fds too.
        streams = (sys.stdout, sys.stderr)
        sys.stdout = io.TextIOWrapper(
            io.FileIO(1, "w", closefd=False),
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )
        sys.stderr = io.TextIOWrapper(
            io.FileIO(2, "w", closefd=False),
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )
        try:
            yield
        finally:
            for stream in (sys.stdout, sys.stderr):
                with contextlib.suppress(Exception):
                    stream.flush()
            sys.stdout, sys.stderr = streams
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
            # Children that outlive the job (a daemon) keep the pipe open; do
            # not block the job on them.
            reader.join(timeout=5)
