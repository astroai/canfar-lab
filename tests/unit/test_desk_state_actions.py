from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from canfar_lab.desk import actions as actions_mod
from canfar_lab.desk import state as state_mod
from canfar_lab.errors import LabError
from canfar_lab.sync import bus
from canfar_lab.sync.engine import ExchangeReport, RepoAction


def _settings(tmp_path: Path, **over):
    values = {
        "sync_mailbox": None,
        "sync_publish_mode": "commit",
        "sync_exclude": [],
        "apps_editor": "vscode",
        "apps_terminal": "auto",
        "session_name": "astroai-studio",
        "dsh_version": "pin",
    }
    values.update(over)
    return SimpleNamespace(**values)


class FakeOps:
    def __init__(self, *, rows=None, manager=None, auth=None, error=None):
        self._rows = rows
        self._manager = manager
        self._auth = auth
        self._error = error

    def list_sessions(self):
        if self._error:
            raise self._error
        return self._rows

    def find_manager(self):
        if self._error:
            raise self._error
        return self._manager

    def auth_status(self):
        if self._error:
            raise self._error
        return self._auth


# ------------------------------------------------------------------- state.py


def test_auth_state_maps_status():
    auth = SimpleNamespace(authenticated=True, idp="CADC", server="canfar.net", message="ok")
    out = state_mod.auth_state(ops=FakeOps(auth=auth))
    assert out == {
        "authenticated": True,
        "idp": "CADC",
        "server": "canfar.net",
        "message": "ok",
    }


def test_auth_state_never_raises():
    out = state_mod.auth_state(ops=FakeOps(error=RuntimeError("no client")))
    assert out["authenticated"] is None
    assert "no client" in out["message"]


def test_studio_session_prefers_running(monkeypatch):
    monkeypatch.setenv("CANFAR_LAB_SESSION_BASE_URL", "https://workloads.example")
    rows = [
        {"id": "2", "name": "astroai-studio", "status": "Pending", "image": "x/y:z"},
        {"id": "1", "name": "astroai-studio", "status": "Running", "image": "x/y:z"},
    ]
    session = state_mod.studio_session(ops=FakeOps(rows=rows), name="astroai-studio")
    assert session is not None
    assert session["id"] == "1"
    assert session["status"] == "Running"
    assert session["url"] == "https://workloads.example/session/contrib/1"


def test_studio_session_matches_image_and_handles_empty(monkeypatch):
    monkeypatch.setenv("CANFAR_LAB_SESSION_BASE_URL", "https://workloads.example")
    rows = [{"id": "9", "name": "other", "status": "Running", "imageName": "images/x/studio:1"}]
    session = state_mod.studio_session(ops=FakeOps(rows=rows), name="astroai-studio")
    assert session is not None and session["id"] == "9"
    assert state_mod.studio_session(ops=FakeOps(rows=[])) is None
    assert state_mod.studio_session(ops=FakeOps(error=RuntimeError("nope"))) is None


def test_mailbox_state_empty(tmp_path):
    state = state_mod.mailbox_state(tmp_path / "box")
    assert state["exists"] is False
    assert state["counts"] == {"requests": 0, "processing": 0, "events": 0}


def test_mailbox_state_with_traffic(tmp_path):
    mailbox = bus.ensure(tmp_path / "box")
    bus.enqueue("requests", {"kind": "request"}, mailbox=mailbox)
    bus.write_ready(mailbox=mailbox, side="session", extra={})
    bus.write_state(mailbox=mailbox, side="desk", payload={"last_event": "e.json"})
    state = state_mod.mailbox_state(mailbox, configured=True)
    assert state["exists"] is True
    assert state["shared"] is True
    assert state["session_ready"] is True
    assert state["counts"]["requests"] == 1
    assert state["last_exchange"]["last_event"] == "e.json"


def test_collect_without_probe(tmp_path):
    state = state_mod.collect(
        root=tmp_path,
        mailbox=tmp_path / "box",
        settings=_settings(tmp_path),
        probe=False,
    )
    assert state["ok"] is True
    assert state["mode"] == "desk"
    assert state["dsh"]["spec"] == "pin"
    assert state["dsh"]["installed"] is None
    assert state["lab"]["version"]  # canfar-lab is importable in tests
    assert state["auth"]["authenticated"] is None
    assert state["session"] is None


def test_collect_probes_and_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(
        state_mod,
        "auth_state",
        lambda ops=None: {"authenticated": True, "idp": "CADC", "server": "s", "message": "ok"},
    )
    monkeypatch.setattr(state_mod, "studio_session", lambda ops=None, name="x": None)
    state = state_mod.collect(
        root=tmp_path,
        mailbox=tmp_path / "box",
        settings=_settings(tmp_path, sync_mailbox=tmp_path / "shared"),
        probe=True,
    )
    assert state["auth"]["authenticated"] is True
    assert state["mailbox"]["shared"] is True


# --------------------------------------------------------------- DeskActions


def _actions(tmp_path, **over):
    kwargs = {"root": tmp_path, "home": tmp_path, "settings": _settings(tmp_path, **over)}
    return actions_mod.DeskActions(**kwargs)


def test_dispatch_unknown_action_raises(tmp_path):
    with pytest.raises(LabError):
        _actions(tmp_path).dispatch("nope")


def test_state_is_cached_until_action(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_collect(**kwargs):
        calls["n"] += 1
        return {"ok": True, "n": calls["n"]}

    monkeypatch.setattr(actions_mod.state_mod, "collect", fake_collect)
    acts = _actions(tmp_path)
    first = acts.state()
    second = acts.state()
    assert first == second
    assert calls["n"] == 1
    acts.dispatch("state")
    assert calls["n"] == 2


def test_state_survives_collect_crash(tmp_path, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("collect exploded")

    monkeypatch.setattr(actions_mod.state_mod, "collect", boom)
    out = _actions(tmp_path).state()
    assert out["ok"] is False
    assert "collect exploded" in out["error"]


def test_open_tool_unknown_raises(tmp_path):
    with pytest.raises(LabError):
        _actions(tmp_path).open_tool({"tool": "emacs"})


def test_launch_studio_opens_a_running_match(tmp_path):
    calls: list[list[str]] = []
    image = "images.canfar.net/astroai/studio:latest"

    def run(args: list[str]) -> tuple[int, str, str]:
        calls.append(args)
        if args[0] == "ps":
            body = json.dumps(
                [
                    {"id": "abc", "image": image, "status": "Running"},
                    {"id": "no", "image": "other", "status": "Running"},
                ]
            )
            return 0, body, ""
        return 0, "", ""

    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path, session_image=image),
        canfar_run=run,
    )
    out = acts.launch_studio({})
    assert out["ok"] is True
    assert out["id"] == "abc"
    assert out["created"] is False
    assert calls == [
        ["ps", "--kind", "contributed", "--output", "json"],
        ["open", "abc"],
    ]


def test_launch_studio_creates_when_none_match(tmp_path):
    calls: list[list[str]] = []
    image = "images.canfar.net/astroai/studio:latest"

    def run(args: list[str]) -> tuple[int, str, str]:
        calls.append(args)
        if args[0] == "ps":
            return 0, "[]", ""
        if args[0] == "create":
            return 0, '["new1"]', ""
        return 0, "", ""

    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path, session_image=image, session_name="studio"),
        canfar_run=run,
    )
    out = acts.launch_studio({})
    assert out["ok"] is True
    assert out["id"] == "new1"
    assert out["created"] is True
    assert calls[1] == ["create", "--name", "studio", "--output", "json", "contributed", image]


def test_open_tool_jupyter_is_local(tmp_path, monkeypatch):
    opened: list[str] = []

    def launch(tool, root, *, home, opener, popen):
        opener("http://127.0.0.1:8888/lab")
        return {"url": "http://127.0.0.1:8888/lab", "opened": True, "started": True}

    monkeypatch.setattr(actions_mod.apps, "ensure_local_app", launch)
    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path),
        opener=lambda url: opened.append(url) or True,
    )
    out = acts.open_tool({"tool": "jupyter"})
    assert out["ok"] is True
    assert out["local"] is True
    assert out["url"] == "http://127.0.0.1:8888/lab"
    assert opened == ["http://127.0.0.1:8888/lab"]


def test_agents_setup_rejects_unknown_id(tmp_path):
    out = _actions(tmp_path).agents_setup({"id": "emacs"})
    assert out["ok"] is False
    assert "Unknown agent" in out["error"]


def test_agents_key_clears_without_echoing(tmp_path, monkeypatch):
    seen: dict[str, str | None] = {}

    def unset(home, name):
        seen["name"] = name
        return {"key": name, "present": False, "changed": True}

    monkeypatch.setattr(actions_mod.agents_page, "unset_key", unset)
    out = _actions(tmp_path).agents_key({"key": "OPENROUTER_API_KEY", "value": ""})
    assert out["ok"] is True
    assert out["present"] is False
    assert seen["name"] == "OPENROUTER_API_KEY"
    assert "value" not in out


def test_open_tool_hub_still_needs_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(actions_mod.state_mod, "studio_session", lambda ops=None, name="x": None)
    out = _actions(tmp_path, ops_factory=None).open_tool({"tool": "hub"})
    assert out["ok"] is False
    assert "No running Studio session" in out["error"]


def test_open_editor_and_terminal_delegate(tmp_path, monkeypatch):
    monkeypatch.setattr(actions_mod.apps, "open_editor", lambda path, editor: f"{editor}:{path}")
    monkeypatch.setattr(actions_mod.apps, "open_terminal", lambda path, term: f"{term}:{path}")
    acts = _actions(tmp_path)
    editor = acts.open_editor({})
    terminal = acts.open_terminal({})
    assert editor == {"ok": True, "path": str(tmp_path), "opened": f"vscode:{tmp_path}"}
    assert terminal == {"ok": True, "path": str(tmp_path), "opened": f"auto:{tmp_path}"}


def test_open_editor_rejects_missing_dir(tmp_path):
    out = _actions(tmp_path).open_editor({"path": str(tmp_path / "nope")})
    assert out["ok"] is False


def test_login_suggests_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(actions_mod.apps, "open_terminal", lambda path, term: "open -a Terminal")
    out = _actions(tmp_path).login({})
    assert out["command"] == "canfar login"
    assert out["terminal"] == "open -a Terminal"


def test_cluster_status(tmp_path):
    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path),
        ops_factory=lambda: FakeOps(manager={"id": "m1", "status": "Running"}),
    )
    out = acts.cluster({"mode": "status"})
    assert out == {"ok": True, "mode": "status", "manager": {"id": "m1", "status": "Running"}}


def test_cluster_status_error_is_reported(tmp_path):
    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path),
        ops_factory=lambda: FakeOps(error=RuntimeError("not logged in")),
    )
    out = acts.cluster({"mode": "status"})
    assert out["ok"] is False
    assert "not logged in" in out["error"]


def test_cluster_start_spawns_detached(tmp_path):
    spawned: list[list[str]] = []

    def popen(cmd, **kwargs):
        spawned.append(list(cmd))
        return object()

    acts = actions_mod.DeskActions(
        root=tmp_path, home=tmp_path, settings=_settings(tmp_path), popen=popen
    )
    out = acts.cluster({"mode": "start"})
    assert out["ok"] is True
    assert spawned and spawned[0][1:4] == ["-m", "canfar_lab.cli.main", "cluster"]
    assert spawned[0][4] == "start"
    assert Path(out["log"]).exists()


def test_cluster_unknown_mode_raises(tmp_path):
    with pytest.raises(LabError):
        _actions(tmp_path).cluster({"mode": "restart"})


class FakeEngine:
    def __init__(self, report: ExchangeReport):
        self._report = report
        self.calls: list[dict] = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        return self._report


def _report(tmp_path: Path) -> ExchangeReport:
    return ExchangeReport(
        side="desk",
        op="publish",
        root=str(tmp_path),
        repos=[
            RepoAction(slug="a/b", path="a/b", action="pushed", status="ok", head="abc"),
            RepoAction(slug="a/missing", path="a/missing", action="none", status="missing"),
        ],
    )


def test_sync_to_canfar_dry_run_touches_nothing(tmp_path):
    report = _report(tmp_path)
    engine = FakeEngine(report)
    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path),
        engine_factory=lambda side: engine,
    )
    out = acts.sync_to_canfar({"dry_run": True})
    assert out["ok"] is True
    assert engine.calls[0]["dry_run"] is True
    assert out["report"]["summary"] == {"ok": 1, "missing": 1}
    assert not (tmp_path / ".astroai").exists()


def test_sync_to_canfar_drops_request_in_mailbox(tmp_path):
    engine = FakeEngine(_report(tmp_path))
    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path),
        engine_factory=lambda side: engine,
    )
    out = acts.sync_to_canfar({})
    mailbox = tmp_path / ".astroai" / "lab" / "sync"
    assert out["mailbox"] == str(mailbox)
    assert out["request"].endswith(".json")
    assert out["shared_mailbox"] is False
    assert "laptop-local" in out["hint"]
    requests = sorted((mailbox / "requests").glob("*.json"))
    assert len(requests) == 1
    payload = bus.read_json(requests[0])
    assert payload is not None
    assert payload["repos"] == [{"slug": "a/b", "head": "abc"}]  # missing skipped
    assert payload["from"]["side"] == "desk"


def test_sync_to_canfar_marks_shared_mailbox(tmp_path):
    engine = FakeEngine(_report(tmp_path))
    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path, sync_mailbox=tmp_path / "shared"),
        engine_factory=lambda side: engine,
    )
    out = acts.sync_to_canfar({})
    assert out["shared_mailbox"] is True
    assert out["session_ready"] is False
    assert "picked up at session boot" in out["hint"]


def test_sync_to_canfar_notes_ready_session(tmp_path):
    mailbox = bus.ensure(tmp_path / "shared")
    bus.write_ready(mailbox=mailbox, side="session", extra={})
    engine = FakeEngine(_report(tmp_path))
    acts = actions_mod.DeskActions(
        root=tmp_path,
        home=tmp_path,
        settings=_settings(tmp_path, sync_mailbox=mailbox),
        engine_factory=lambda side: engine,
    )
    out = acts.sync_to_canfar({})
    assert out["session_ready"] is True
    assert "hint" not in out
