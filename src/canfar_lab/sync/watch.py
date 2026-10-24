"""Watchers on both sides of the mailbox.

* **session** — write ``ready.json``, expire TTL'd requests, and for each
  claimed request: bootstrap missing repos into the resolved code root, absorb
  (fast-forward only), and reply with an event. Runs in the Studio image.
* **desk** — consume unseen events, absorb locally (rebase mode: the desk is
  human-attended), and record the last event seen in ``state/desk.json``.

Both sides poll: ``/arc`` is a network filesystem, so inotify assumptions are
out. Pass ``once=True`` for scripted/test runs.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

from canfar_lab.sync import arc_data, bus, clock
from canfar_lab.sync.engine import ExchangeReport, SyncEngine

DEFAULT_INTERVAL_SECONDS = 10.0


def _session_id() -> str:
    for key in os.environ:
        if key.upper() == "SKAHA_SESSIONID":
            return os.environ[key]
    return ""


def _repos_from(payload: dict[str, Any]) -> list[str] | None:
    repos = payload.get("repos")
    if not isinstance(repos, list):
        return None
    slugs: list[str] = []
    for item in repos:
        slug = str(item.get("slug") or "").strip() if isinstance(item, dict) else str(item).strip()
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs or None


def session_once(
    *,
    engine: SyncEngine,
    mailbox: Path,
    root: Path,
    say=None,  # noqa: ANN001 — Callable[[str], None]
) -> int:
    """One session-side pass. Returns the number of requests processed."""
    speak = say or (lambda message: None)
    bus.ensure(mailbox)
    bus.expire_requests(mailbox=mailbox)
    bus.write_ready(
        mailbox=mailbox,
        side=bus.SESSION,
        extra={
            "root": str(root),
            "session": _session_id(),
        },
    )
    processed = 0
    for path in bus.pending_requests(mailbox):
        claimed = bus.claim_request(path, mailbox=mailbox)
        if claimed is None:
            continue
        payload = bus.read_json(claimed) or {}
        repos = _repos_from(payload)
        report = engine.absorb(repos=repos, mode="ff", bootstrap=True)
        reply = bus.event_payload(
            side=bus.SESSION,
            root=root,
            report=report.to_dict(),
            in_reply_to=claimed.name,
            note=payload.get("note"),
        )
        event_path = bus.enqueue("events", reply, mailbox=mailbox)
        bus.write_state(
            mailbox=mailbox,
            side=bus.SESSION,
            payload={
                "last_event": event_path.name,
                "absorbed_at": clock.utc_iso(),
                "from": payload.get("from"),
                "absorb": report.to_dict(),
            },
        )
        with contextlib.suppress(OSError):
            claimed.unlink()
        bus.prune_events(mailbox=mailbox)
        speak(f"processed {claimed.name} → {event_path.name}")
        processed += 1
    return processed


def desk_once(
    *,
    engine: SyncEngine,
    mailbox: Path,
    root: Path,
    say=None,  # noqa: ANN001 — Callable[[str], None]
) -> int:
    """One desk-side pass. Returns the number of events absorbed."""
    speak = say or (lambda message: None)
    arc_data.bridge(mailbox, direction="pull")
    bus.ensure(mailbox)
    state = bus.read_state(mailbox=mailbox, side=bus.DESK)
    last = str(state.get("last_event") or "")
    absorbed = 0
    for path in sorted((mailbox / "events").glob("*.json")):
        name = path.name
        if last and name <= last:
            continue
        payload = bus.read_json(path)
        if payload is None:
            continue
        report = payload.get("report")
        report_map: dict[str, Any] = report if isinstance(report, dict) else {}
        repos = _repos_from(report_map)
        result: ExchangeReport = engine.absorb(repos=repos, mode="rebase")
        bus.write_state(
            mailbox=mailbox,
            side=bus.DESK,
            payload={
                "last_event": name,
                "absorbed_at": clock.utc_iso(),
                "from": payload.get("from"),
                "absorb": result.to_dict(),
            },
        )
        speak(f"absorbed {name} ({result.summary()})")
        absorbed += 1
    return absorbed


def watch(
    *,
    side: str,
    engine: SyncEngine,
    mailbox: Path,
    root: Path,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    once: bool = False,
    say=None,  # noqa: ANN001 — Callable[[str], None]
) -> int:
    """Run the watcher loop (or a single pass with ``once=True``)."""
    speak = say or (lambda message: None)
    if side not in (bus.DESK, bus.SESSION):
        raise ValueError(f"unknown watch side: {side!r}")
    total = 0
    while True:
        try:
            if side == bus.SESSION:
                total += session_once(engine=engine, mailbox=mailbox, root=root, say=speak)
            else:
                total += desk_once(engine=engine, mailbox=mailbox, root=root, say=speak)
        except Exception as exc:  # noqa: BLE001 — a watcher must survive one bad pass
            speak(f"watch error: {exc}")
        if once:
            return total
        time.sleep(max(1.0, interval))


__all__ = ["DEFAULT_INTERVAL_SECONDS", "desk_once", "session_once", "watch"]
