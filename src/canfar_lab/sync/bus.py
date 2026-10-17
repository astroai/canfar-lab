"""Mailbox between desk and session: requests, events, state, readiness.

Layout (docs/desk-sync.md §6.4)::

    <mailbox>/                # $HOME/.astroai/lab/sync — shared /arc home in session
      ready.json              # written by the session watcher at boot
      requests/<ts>-<id>.json # desk → session (TTL'd)
      processing/<ts>-<id>.json
      events/<ts>-<id>.json   # session → desk (pruned, never deleted by desk)
      state/<side>.json       # last event consumed, last absorb report

The files are tiny JSON documents (references only, never secrets); writes are
atomic (tmp + rename) and readers tolerate malformed/partial files. GitHub
remains the protocol — this is only the doorbell.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from canfar_lab.sync import clock

DESK = "desk"
SESSION = "session"

REQUEST_TTL_SECONDS = 1800
READY_MAX_AGE_SECONDS = 7200
EVENTS_KEEP = 200
MAILBOX_ENV = "CANFAR_LAB_SYNC_MAILBOX"


def mailbox_dir(*, home: Path | None = None, override: Path | str | None = None) -> Path:
    """Resolve the mailbox: explicit override → env → ``~/.astroai/lab/sync``.

    On the desk the ``$HOME`` here must be the *shared* home (an SSHFS mount
    of ``arc:/home/$USER`` or a synced copy) for the session to see requests;
    ``docs/desk-sync.md`` §6.4 covers the fallbacks.
    """
    if override:
        return Path(override).expanduser()
    env = os.environ.get(MAILBOX_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return (home or Path.home()) / ".astroai" / "lab" / "sync"


def ensure(mailbox: Path) -> Path:
    for name in ("requests", "processing", "events", "state"):
        (mailbox / name).mkdir(parents=True, exist_ok=True)
    return mailbox


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path | str) -> dict[str, Any] | None:
    """Tolerant read: None for missing, partial, or malformed files."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def enqueue(kind: str, payload: dict[str, Any], *, mailbox: Path) -> Path:
    """Write one document into ``requests/`` or ``events/``."""
    ensure(mailbox)
    stamped = {
        "v": 1,
        **payload,
        "uuid": payload.get("uuid") or uuid.uuid4().hex,
        "ts": payload.get("ts") or clock.utc_iso(),
    }
    path = mailbox / kind / f"{clock.stamp()}-{str(stamped['uuid'])[:12]}.json"
    _atomic_write_json(path, stamped)
    return path


def request_payload(
    *,
    side: str,
    root: Path | str,
    repos: list[dict[str, str]] | None = None,
    action: str = "exchange",
    bootstrap: bool = True,
    ttl_seconds: int = REQUEST_TTL_SECONDS,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "request",
        "action": action,
        "from": {"side": side, "root": str(root)},
        "repos": repos or [],
        "bootstrap": "auto" if bootstrap else "off",
        "ttl_seconds": ttl_seconds,
        "note": note,
    }


def event_payload(
    *,
    side: str,
    root: Path | str,
    report: dict[str, Any],
    in_reply_to: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "event",
        "from": {"side": side, "root": str(root)},
        "in_reply_to": in_reply_to,
        "report": report,
        "note": note,
    }


def pending_requests(mailbox: Path) -> list[Path]:
    return sorted((mailbox / "requests").glob("*.json"))


def claim_request(path: Path, *, mailbox: Path) -> Path | None:
    """Atomically move a request to ``processing/``; None when raced."""
    dest = mailbox / "processing" / path.name
    try:
        os.replace(path, dest)
    except OSError:
        return None
    return dest


def expire_requests(*, mailbox: Path, now=None) -> int:  # noqa: ANN001 — datetime
    """Drop requests past their TTL (and unparseable ones). Returns removed count."""
    moment = now or clock.now()
    removed = 0
    for path in (mailbox / "requests").glob("*.json"):
        payload = read_json(path) or {}
        created = clock.parse_iso(str(payload.get("ts") or ""))
        try:
            ttl = int(payload.get("ttl_seconds") or REQUEST_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl = REQUEST_TTL_SECONDS
        if created is None or (moment - created).total_seconds() > ttl:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def prune_events(*, mailbox: Path, keep: int = EVENTS_KEEP) -> int:
    """Keep the newest ``keep`` events; returns removed count."""
    files = sorted((mailbox / "events").glob("*.json"))
    doomed = files[:-keep] if keep > 0 else files
    removed = 0
    for path in doomed:
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def write_ready(
    *,
    mailbox: Path,
    side: str = SESSION,
    extra: dict[str, Any] | None = None,
) -> Path:
    payload: dict[str, Any] = {"v": 1, "side": side, "ts": clock.utc_iso(), "pid": os.getpid()}
    if extra:
        payload.update(extra)
    path = mailbox / "ready.json"
    _atomic_write_json(path, payload)
    return path


def read_ready(
    *, mailbox: Path, max_age_seconds: int = READY_MAX_AGE_SECONDS
) -> dict[str, Any] | None:
    """The session watcher's boot marker, when fresh enough to trust."""
    payload = read_json(mailbox / "ready.json")
    if payload is None:
        return None
    created = clock.parse_iso(str(payload.get("ts") or ""))
    if created is None:
        return None
    if (clock.now() - created).total_seconds() > max_age_seconds:
        return None
    return payload


def read_state(*, mailbox: Path, side: str) -> dict[str, Any]:
    return read_json(mailbox / "state" / f"{side}.json") or {}


def write_state(*, mailbox: Path, side: str, payload: dict[str, Any]) -> Path:
    path = mailbox / "state" / f"{side}.json"
    _atomic_write_json(path, {"v": 1, "side": side, "updated": clock.utc_iso(), **payload})
    return path


def counts(*, mailbox: Path) -> dict[str, int]:
    return {
        "requests": len(list((mailbox / "requests").glob("*.json"))),
        "processing": len(list((mailbox / "processing").glob("*.json"))),
        "events": len(list((mailbox / "events").glob("*.json"))),
    }


__all__ = [
    "DESK",
    "EVENTS_KEEP",
    "MAILBOX_ENV",
    "READY_MAX_AGE_SECONDS",
    "REQUEST_TTL_SECONDS",
    "SESSION",
    "claim_request",
    "counts",
    "ensure",
    "enqueue",
    "event_payload",
    "expire_requests",
    "mailbox_dir",
    "pending_requests",
    "prune_events",
    "read_json",
    "read_ready",
    "read_state",
    "request_payload",
    "write_ready",
    "write_state",
]
