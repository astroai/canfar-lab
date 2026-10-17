from __future__ import annotations

from pathlib import Path

from canfar_lab.sync import bus
from canfar_lab.sync.engine import SyncEngine
from canfar_lab.sync.watch import desk_once, session_once, watch
from tests.helpers import commit_file, head_sha, make_worktrees

SLUG = "demo/one"


def _engine(root: Path, side: str) -> SyncEngine:
    return SyncEngine(root, [SLUG], side=side, github_user="tester")


def test_session_once_processes_request_and_writes_event(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    (desk / "shared.py").write_text("x = 1\n", encoding="utf-8")
    _engine(tmp_path / "desk", "desk").publish()

    mailbox = bus.ensure(tmp_path / "box")
    request = bus.request_payload(
        side=bus.DESK, root=tmp_path / "desk", repos=[{"slug": SLUG, "head": head_sha(desk)}]
    )
    bus.enqueue("requests", request, mailbox=mailbox)

    processed = session_once(
        engine=_engine(tmp_path / "session", "session"), mailbox=mailbox, root=tmp_path / "session"
    )
    assert processed == 1
    assert (session / "shared.py").is_file()

    events = sorted((mailbox / "events").glob("*.json"))
    assert len(events) == 1
    payload = bus.read_json(events[0])
    assert payload is not None
    assert payload["from"]["side"] == "session"
    assert payload["in_reply_to"]
    assert payload["report"]["op"] == "absorb"

    ready = bus.read_ready(mailbox=mailbox)
    assert ready is not None
    # Request moved out of the queue, nothing left to claim.
    assert bus.pending_requests(mailbox) == []
    assert list((mailbox / "processing").glob("*.json")) == []


def test_session_once_expires_stale_requests(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    bus.enqueue(
        "requests",
        {"kind": "request", "ts": "2020-01-01T00:00:00Z", "ttl_seconds": 30},
        mailbox=mailbox,
    )
    (tmp_path / "session").mkdir()
    processed = session_once(
        engine=_engine(tmp_path / "session", "session"), mailbox=mailbox, root=tmp_path / "session"
    )
    assert processed == 0
    assert bus.pending_requests(mailbox) == []
    assert sorted((mailbox / "events").glob("*.json")) == []


def test_desk_once_absorbs_event_and_dedupes(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    # Session makes and pushes a commit; the desk is behind.
    commit_file(session, "from_session.py", "y = 2\n", "session work")
    from tests.helpers import git_run

    git_run(["push", "origin", "main"], session)

    mailbox = bus.ensure(tmp_path / "box")
    event = bus.event_payload(
        side=bus.SESSION,
        root=tmp_path / "session",
        report={
            "side": "session",
            "op": "publish",
            "repos": [{"slug": SLUG, "head": head_sha(session)}],
        },
    )
    bus.enqueue("events", event, mailbox=mailbox)

    engine = _engine(tmp_path / "desk", "desk")
    assert desk_once(engine=engine, mailbox=mailbox, root=tmp_path / "desk") == 1
    assert (desk / "from_session.py").is_file()

    state = bus.read_state(mailbox=mailbox, side="desk")
    assert state["last_event"]
    assert state["absorb"]["op"] == "absorb"

    # Second pass: event already consumed.
    assert desk_once(engine=engine, mailbox=mailbox, root=tmp_path / "desk") == 0


def test_watch_once_runs_single_pass(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    (desk / "one.txt").write_text("1\n", encoding="utf-8")
    _engine(tmp_path / "desk", "desk").publish()
    mailbox = bus.ensure(tmp_path / "box")
    bus.enqueue(
        "requests",
        bus.request_payload(side=bus.DESK, root=tmp_path, repos=[{"slug": SLUG}]),
        mailbox=mailbox,
    )
    lines: list[str] = []
    processed = watch(
        side="session",
        engine=_engine(tmp_path / "session", "session"),
        mailbox=mailbox,
        root=tmp_path / "session",
        once=True,
        say=lines.append,
    )
    assert processed == 1
    assert (session / "one.txt").is_file()
    assert any("processed" in line for line in lines)


def test_watch_rejects_unknown_side(tmp_path):
    import pytest

    mailbox = bus.ensure(tmp_path / "box")
    with pytest.raises(ValueError):
        watch(
            side="nonsense",
            engine=_engine(tmp_path, "desk"),
            mailbox=mailbox,
            root=tmp_path,
            once=True,
        )


def test_session_once_skips_request_claims_raced(tmp_path, monkeypatch):
    """A request claimed between listing and claiming is skipped, not retried."""
    _origin, desk, session = make_worktrees(tmp_path)
    (desk / "x.txt").write_text("x\n", encoding="utf-8")
    _engine(tmp_path / "desk", "desk").publish()
    mailbox = bus.ensure(tmp_path / "box")
    bus.enqueue(
        "requests",
        bus.request_payload(side=bus.DESK, root=tmp_path, repos=[{"slug": SLUG}]),
        mailbox=mailbox,
    )

    original = bus.claim_request
    calls = {"n": 0}

    def flaky_claim(path, *, mailbox):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original(path, mailbox=mailbox)

    monkeypatch.setattr(bus, "claim_request", flaky_claim)
    processed = session_once(
        engine=_engine(tmp_path / "session", "session"), mailbox=mailbox, root=tmp_path / "session"
    )
    assert processed == 0
    assert bus.pending_requests(mailbox)
