"""canfar_lab.web.jobs: single-flight runner with fd-level log capture."""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from canfar_lab.errors import LabError
from canfar_lab.web.jobs import Busy, JobRunner


def test_captures_python_and_subprocess_output() -> None:
    runner = JobRunner(capture=True)

    def _work(_job):  # noqa: ANN001, ANN202
        print("from python", flush=True)
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; print('child out'); print('child err', file=sys.stderr)",
            ],
            check=True,
        )
        return {"ok": True, "n": 1}

    job = runner.start("t", "Test", _work)
    assert runner.wait(job, timeout=20)
    assert job.state == "ok" and job.result == {"ok": True, "n": 1}
    assert {"from python", "child out", "child err"} <= set(job.lines)


def test_single_flight() -> None:
    runner = JobRunner(capture=False)
    gate = threading.Event()
    job = runner.start("t", "Slow", lambda _j: gate.wait(10) and {"ok": True})
    with pytest.raises(Busy, match="Slow"):
        runner.start("t", "Other", lambda _j: {})
    gate.set()
    assert runner.wait(job, timeout=10)
    assert runner.current() is None
    runner.wait(runner.start("t", "Next", lambda _j: {}), timeout=10)


def test_failures_are_reported() -> None:
    runner = JobRunner(capture=False)

    def _lab_error(_j):  # noqa: ANN001, ANN202
        raise LabError("no network", hint="try later")

    job = runner.start("t", "Err", _lab_error)
    runner.wait(job, timeout=10)
    assert (job.state, job.error, job.hint) == ("failed", "no network", "try later")

    job = runner.start("t", "Partial", lambda _j: {"ok": False, "errors": ["a", "b"]})
    runner.wait(job, timeout=10)
    assert job.state == "failed" and job.error == "a; b"

    job = runner.start("t", "Crash", lambda _j: 1 / 0)
    runner.wait(job, timeout=10)
    assert job.state == "failed" and "ZeroDivisionError" in str(job.error)


def test_lines_since_and_ansi_stripping() -> None:
    runner = JobRunner(capture=False)
    gate = threading.Event()

    def _work(job):  # noqa: ANN001, ANN202
        runner.log(job, "\x1b[32mgreen\x1b[0m\nsecond\r\n")
        gate.wait(10)
        return {}

    job = runner.start("t", "Log", _work)
    rows, done = runner.lines_since(job, 0, timeout=5)
    assert rows == [(0, "green"), (1, "second")] and not done
    rows, done = runner.lines_since(job, 2, timeout=0.05)
    assert rows == [] and not done
    gate.set()
    runner.wait(job, timeout=10)
    assert runner.lines_since(job, 2, timeout=1) == ([], True)
