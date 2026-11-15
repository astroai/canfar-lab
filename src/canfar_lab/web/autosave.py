"""Automatic snapshots of a code directory that lives on ``$SCRATCH``.

Runs inside ``canfar lab web`` (the one long-lived process in the session):

* every minute, :meth:`AutoSaver.tick` starts a snapshot job when automatic
  saves are on, the code dir is on scratch and has projects, the interval has
  passed since the last save (manual or automatic), no other job is running,
  and something changed since the newest snapshot (cheap fingerprint);
* at shutdown, :meth:`AutoSaver.final` waits briefly for a running job, then
  takes one last snapshot if anything changed. Kubernetes gives a pod ~30 s
  after SIGTERM, so this is best effort and bounded by *budget*.

Old automatic snapshots beyond ``keep`` are pruned; manual ones never are.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from canfar_lab.web.jobs import Busy, Job, JobRunner

TICK_SEC = 60.0


class AutoSaver:
    def __init__(self, runner: JobRunner) -> None:
        self.runner = runner
        self.last_save = time.time()  # the session start counts as "just saved"
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- policy ----------------------------------------------------------

    @staticmethod
    def _context() -> tuple[Any, Any] | None:
        """(settings, code dir) when automatic saves apply to this session, else None."""
        from canfar_lab.session import workspace

        settings = workspace.load()
        if not settings.autosave.enabled:
            return None
        src = workspace.code_dir()
        if not workspace.is_ephemeral(src) or not src.is_dir():
            return None
        if not any(not p.name.startswith(".") for p in src.iterdir()):
            return None
        return settings, src

    def changed(self, src: Any) -> bool:
        from canfar_lab.session import snapshots

        return snapshots.fingerprint(src) != snapshots.latest_fingerprint()

    def due(self, now: float | None = None) -> bool:
        ctx = self._context()
        if ctx is None:
            return False
        from canfar_lab.session import snapshots

        settings, src = ctx
        now = time.time() if now is None else now
        # A save this session made by hand counts too (other sessions' do not).
        last = max(self.last_save, snapshots.latest_time())
        if now - last < settings.autosave.interval_min * 60:
            return False
        if self.runner.current() is not None:
            return False
        if not self.changed(src):
            self.last_save = now  # nothing new: wait a full interval again
            return False
        return True

    def note_saved(self) -> None:
        """A manual snapshot also resets the interval."""
        self.last_save = time.time()

    # -- actions ---------------------------------------------------------

    def _save(self, _job: Job | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        from canfar_lab.session import snapshots, workspace

        keep = workspace.load().autosave.keep
        result = snapshots.save(auto=True, include_dsh=True, timeout=timeout)
        result["pruned"] = snapshots.prune_auto(keep)
        print(f"Saved “{result['name']}” to /arc.", flush=True)
        if result["pruned"]:
            print(f"Removed older automatic saves: {', '.join(result['pruned'])}", flush=True)
        self.last_save = time.time()
        self.last_result = result
        return result

    def tick(self) -> Job | None:
        try:
            if not self.due():
                return None
            return self.runner.start("snapshot_auto", "Automatic save", self._save)
        except Busy:
            return None
        except Exception as exc:  # noqa: BLE001 — a failed check must not kill the loop
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def final(self, budget: float = 25.0) -> dict[str, Any] | None:
        """Last snapshot at shutdown, if automatic saves apply and anything changed.

        Never runs beside another job (a half-finished restore must not become
        the newest save), and the tar is stopped when *budget* runs out, so a
        partial snapshot is never published.
        """
        deadline = time.monotonic() + budget
        current = self.runner.current()
        if current is not None and not self.runner.wait(current, timeout=budget / 3):
            self.last_error = f"skipped the shutdown save: {current.label} was still running"
            return None
        try:
            ctx = self._context()
            if ctx is None or not self.changed(ctx[1]):
                return None
            left = deadline - time.monotonic()
            if left < 2:
                return None
            return self._save(timeout=left)
        except Exception as exc:  # noqa: BLE001 — shutdown must still finish
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    # -- loop ------------------------------------------------------------

    def start(self) -> None:
        def _loop() -> None:
            while not self._stop.wait(TICK_SEC):
                self.tick()

        self._thread = threading.Thread(target=_loop, name="lab-autosave", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "last_save": self.last_save,
            "last_auto": (self.last_result or {}).get("name"),
            "error": self.last_error,
        }
