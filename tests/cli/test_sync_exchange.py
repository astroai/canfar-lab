from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from canfar_lab import studio as studio_mod
from canfar_lab.cli.main import app
from canfar_lab.sync import bus
from tests.helpers import head_sha, make_worktrees

runner = CliRunner()


def _home_mailbox() -> Path:
    return Path.home() / ".astroai" / "lab" / "sync"


# ------------------------------------------------------------- exchange


def test_exchange_to_canfar_dry_run_plans_nothing(tmp_path):
    result = runner.invoke(
        app,
        [
            "--json",
            "--dry-run",
            "sync",
            "exchange",
            "--to",
            "canfar",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["to"] == "canfar"
    assert payload["published"]["dry_run"] is True
    assert payload["published"]["op"] == "publish"
    assert payload["published"]["repos"]  # defaults are reported as missing
    assert all(rec["status"] == "missing" for rec in payload["published"]["repos"])
    assert not _home_mailbox().exists()


def test_exchange_to_desktop_writes_event(tmp_path):
    _origin, desk, _session = make_worktrees(tmp_path)
    (desk / "from_desk.py").write_text("x = 1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--json",
            "sync",
            "exchange",
            "--to",
            "desktop",
            "--root",
            str(tmp_path / "desk"),
            "--repos",
            "demo/one",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["to"] == "desktop"
    assert payload["event"].endswith(".json")
    rec = payload["published"]["repos"][0]
    assert rec["slug"] == "demo/one"
    assert rec["status"] == "ok"
    assert rec["action"] == "pushed"

    events = sorted((_home_mailbox() / "events").glob("*.json"))
    assert len(events) == 1
    event = bus.read_json(events[0])
    assert event is not None
    assert event["from"]["side"] == "session"
    assert event["report"]["repos"][0]["slug"] == "demo/one"


def test_exchange_to_canfar_queues_request(tmp_path):
    _origin, desk, _session = make_worktrees(tmp_path)
    (desk / "from_desk.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "--json",
            "sync",
            "exchange",
            "--to",
            "canfar",
            "--root",
            str(tmp_path / "desk"),
            "--repos",
            "demo/one",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["request"].endswith(".json")
    assert payload["session_ready"] is False
    requests = sorted((_home_mailbox() / "requests").glob("*.json"))
    assert len(requests) == 1
    request = bus.read_json(requests[0])
    assert request is not None
    assert request["from"]["side"] == "desk"
    assert [entry["slug"] for entry in request["repos"]] == ["demo/one"]
    assert head_sha(desk).startswith(request["repos"][0]["head"])


def test_exchange_rejects_bad_direction(tmp_path):
    result = runner.invoke(app, ["sync", "exchange", "--to", "sideways", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "canfar" in result.output


def test_exchange_requires_direction(tmp_path):
    result = runner.invoke(app, ["sync", "exchange", "--root", str(tmp_path)])
    assert result.exit_code != 0


# ---------------------------------------------------------------- watch


def test_watch_session_once_processes_request(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    (desk / "pulled.py").write_text("y = 2\n", encoding="utf-8")
    # Push the desk commit directly through git (no engine needed for the desk side).
    import subprocess

    subprocess.run(["git", "add", "--", "pulled.py"], cwd=desk, check=True)
    subprocess.run(["git", "commit", "-m", "desk work", "--quiet"], cwd=desk, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=desk, check=True)

    mailbox = bus.ensure(_home_mailbox())
    bus.enqueue(
        "requests",
        bus.request_payload(side=bus.DESK, root=tmp_path / "desk", repos=[{"slug": "demo/one"}]),
        mailbox=mailbox,
    )

    result = runner.invoke(
        app,
        [
            "--json",
            "sync",
            "watch",
            "--session",
            "--once",
            "--root",
            str(tmp_path / "session"),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["side"] == "session"
    assert payload["processed"] == 1
    assert (session / "pulled.py").is_file()
    assert sorted((mailbox / "events").glob("*.json"))


def test_watch_desk_once_absorbs_event(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    import subprocess

    (session / "from_session.py").write_text("z = 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "from_session.py"], cwd=session, check=True)
    subprocess.run(["git", "commit", "-m", "session work", "--quiet"], cwd=session, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=session, check=True)

    mailbox = bus.ensure(_home_mailbox())
    bus.enqueue(
        "events",
        bus.event_payload(
            side=bus.SESSION,
            root=tmp_path / "session",
            report={"op": "publish", "repos": [{"slug": "demo/one"}]},
        ),
        mailbox=mailbox,
    )
    result = runner.invoke(
        app,
        ["--json", "sync", "watch", "--desk", "--once", "--root", str(tmp_path / "desk")],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["side"] == "desk"
    assert payload["processed"] == 1
    assert (desk / "from_session.py").is_file()


def test_watch_rejects_both_sides(tmp_path):
    result = runner.invoke(
        app,
        ["sync", "watch", "--session", "--desk", "--root", str(tmp_path)],
    )
    assert result.exit_code != 0


# ----------------------------------------------------------------- desk


def test_desk_dry_run_plans_latest_dsh(tmp_path, monkeypatch):
    monkeypatch.setattr("canfar_lab.cli.desk.dsh_binary", lambda: None)
    result = runner.invoke(
        app,
        [
            "--json",
            "desk",
            "--dsh-version",
            "pin",
            "--port",
            "5123",
            "--dsh-port",
            "3999",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["root"] == str(tmp_path)
    assert plan["desk_port"] == 5123
    assert plan["dsh_port"] == 3999
    assert plan["dsh"]["spec"] == "pin"
    assert plan["dsh"]["release"]["version"] == studio_mod.DSH_VERSION
    assert plan["dsh"]["release"]["source"] == "pin"
    assert plan["dsh"]["install"] is True  # no dsh on the test host
    assert plan["mailbox"] == str(_home_mailbox())
    assert plan["prepare"]["ok"] is True


def test_desk_doctor_json_reports_checks(tmp_path, monkeypatch):
    monkeypatch.setattr("canfar_lab.cli.desk.dsh_binary", lambda: None)
    result = runner.invoke(
        app, ["--json", "desk", "--doctor", "--dsh-version", "pin", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    names = {check["name"] for check in payload["checks"]}
    assert {"workspace", "dsh", "npm", "editor", "terminal", "mailbox", "auth"} <= names
    dsh_check = next(check for check in payload["checks"] if check["name"] == "dsh")
    assert dsh_check["fatal"] == "True"
    assert dsh_check["ok"] == "no"
    assert payload["dsh"]["spec"] == "pin"
    assert payload["ok"] is False


# ------------------------------------------------------- studio dsh flags


def test_studio_dry_run_reports_dsh_release(tmp_path, monkeypatch):
    monkeypatch.setattr(studio_mod, "dsh_binary", lambda: "/usr/local/bin/dsh")
    result = runner.invoke(
        app,
        ["--json", "--dry-run", "studio", "--dsh-version", "pin", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    dsh = payload["dsh"]
    assert dsh["spec"] == "pin"
    assert dsh["release"]["source"] == "pin"
    assert dsh["release"]["version"] == studio_mod.DSH_VERSION
    assert dsh["installed"] is None
