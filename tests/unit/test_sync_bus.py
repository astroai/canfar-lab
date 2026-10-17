from __future__ import annotations

import json

from canfar_lab.sync import bus, clock


def test_mailbox_dir_precedence(tmp_path, monkeypatch):
    override = tmp_path / "override"
    assert bus.mailbox_dir(override=override) == override

    monkeypatch.setenv(bus.MAILBOX_ENV, str(tmp_path / "envbox"))
    assert bus.mailbox_dir() == tmp_path / "envbox"

    monkeypatch.delenv(bus.MAILBOX_ENV)
    assert bus.mailbox_dir(home=tmp_path) == tmp_path / ".astroai" / "lab" / "sync"


def test_enqueue_and_read_roundtrip(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    path = bus.enqueue("requests", {"kind": "request", "action": "exchange"}, mailbox=mailbox)
    assert path.parent.name == "requests"
    payload = bus.read_json(path)
    assert payload is not None
    assert payload["action"] == "exchange"
    assert payload["uuid"]
    assert payload["ts"].endswith("Z")


def test_read_json_tolerates_garbage(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert bus.read_json(bad) is None
    assert bus.read_json(tmp_path / "missing.json") is None


def test_claim_request_is_atomic(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    path = bus.enqueue("requests", {"kind": "request"}, mailbox=mailbox)
    claimed = bus.claim_request(path, mailbox=mailbox)
    assert claimed is not None
    assert claimed.parent.name == "processing"
    assert bus.claim_request(path, mailbox=mailbox) is None


def test_expire_requests_honors_ttl(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    stale = bus.enqueue(
        "requests",
        {"kind": "request", "ts": "2020-01-01T00:00:00Z", "ttl_seconds": 60},
        mailbox=mailbox,
    )
    fresh = bus.enqueue("requests", {"kind": "request", "ttl_seconds": 3600}, mailbox=mailbox)
    removed = bus.expire_requests(mailbox=mailbox)
    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_expire_requests_unparseable_timestamp(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    path = mailbox / "requests" / "x.json"
    path.write_text(json.dumps({"ttl_seconds": 60}), encoding="utf-8")
    assert bus.expire_requests(mailbox=mailbox) == 1
    assert not path.exists()


def test_prune_events_keeps_newest(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    for i in range(3):
        target = mailbox / "events" / f"2026010{i}T000000Z-abc.json"
        target.write_text("{}", encoding="utf-8")
    assert bus.prune_events(mailbox=mailbox, keep=2) == 1
    names = sorted(p.name for p in (mailbox / "events").glob("*.json"))
    assert names == ["20260101T000000Z-abc.json", "20260102T000000Z-abc.json"]


def test_ready_marker_roundtrip_and_staleness(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    bus.write_ready(mailbox=mailbox, side="session", extra={"root": "/scratch/src"})
    ready = bus.read_ready(mailbox=mailbox)
    assert ready is not None
    assert ready["side"] == "session"
    assert ready["root"] == "/scratch/src"
    assert bus.read_ready(mailbox=mailbox, max_age_seconds=-1) is None


def test_state_roundtrip(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    assert bus.read_state(mailbox=mailbox, side="desk") == {}
    bus.write_state(mailbox=mailbox, side="desk", payload={"last_event": "x.json"})
    state = bus.read_state(mailbox=mailbox, side="desk")
    assert state["last_event"] == "x.json"
    assert state["updated"]


def test_payloads_and_counts(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    request = bus.request_payload(
        side=bus.DESK, root=tmp_path, repos=[{"slug": "a/b", "head": "abc"}]
    )
    assert request["bootstrap"] == "auto"
    assert request["ttl_seconds"] == bus.REQUEST_TTL_SECONDS
    assert request["from"]["side"] == "desk"

    event = bus.event_payload(side=bus.SESSION, root=tmp_path, report={"summary": {}})
    assert event["kind"] == "event"
    assert event["in_reply_to"] is None

    bus.enqueue("requests", request, mailbox=mailbox)
    bus.enqueue("events", event, mailbox=mailbox)
    assert bus.counts(mailbox=mailbox) == {"requests": 1, "processing": 0, "events": 1}


def test_clock_parse_iso_roundtrip():
    stamp = clock.utc_iso()
    assert clock.parse_iso(stamp) is not None
    assert clock.parse_iso("nonsense") is None
    assert isinstance(clock.stamp(), str)
