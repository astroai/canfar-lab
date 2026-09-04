from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.setup_state import (
    agent_setup_lock,
    build_agent_report,
    read_setup_state,
    record_setup_failed,
    record_setup_ok,
)
from astroai_lab.cli.main import app
from astroai_lab.errors import LabError

runner = CliRunner()


def test_setup_state_ok_and_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    record_setup_ok(home, mode="install")
    state = read_setup_state(home)
    assert state.ok
    assert state.stamp
    assert state.failed is None
    record_setup_failed(home, exit_code=2, detail="partial")
    state = read_setup_state(home)
    assert state.needs_retry
    assert state.failed is not None
    assert "exit=2" in state.failed


def test_agent_setup_lock_contention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASTROAI_LAB_AGENT_LOCK_TIMEOUT", "1")
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with agent_setup_lock(home, timeout=5):
            held.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(timeout=2)
    with pytest.raises(LabError, match="already running"), agent_setup_lock(home, timeout=0.5):
        pass
    release.set()
    t.join(timeout=2)


def test_agent_setup_lock_dead_holder_is_stolen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent.setup_state import lock_path

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASTROAI_LAB_AGENT_LOCK_TIMEOUT", "1")
    path = lock_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("999999999 0\n", encoding="utf-8")  # almost-certainly dead PID
    with agent_setup_lock(home, timeout=1):
        pass
    assert not path.exists()


def test_agent_setup_lock_same_pid_reentrant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    with agent_setup_lock(home, timeout=1), agent_setup_lock(home, timeout=1):
        pass


def test_agent_list_json_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--json", "agent", "list"])
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    assert "ok" in data
    assert "agents" in data
    assert "issues" in data
    assert "setup" in data
    if not data["ok"]:
        assert result.exit_code == 1


def test_agent_verify_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh home with no agents installed → verify passes (day-one gate)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        lambda *a, **k: {
            "binary": "x",
            "path": None,
            "source": "missing",
            "managed": False,
            "home_install": False,
            "home_path": None,
        },
    )
    result = runner.invoke(app, ["--json", "agent", "verify"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["issues"] == []


def test_agent_setup_json_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--json", "--dry-run", "agent", "setup", "cli"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert "actions" in data
    assert "errors" in data


def test_agent_list_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--json", "agent", "list"])
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    assert "agents" in data
    assert build_agent_report(home)["ok"] is False
    assert result.exit_code == 1


def test_agent_list_includes_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--json", "agent", "list"])
    data = json.loads(result.stdout)
    assert "resources" in data
    assert "mem_pct" in data["resources"]


def test_install_timeout_default_raised_for_self_bootstrapping_installers() -> None:
    """Self-bootstrapping installers (hermes: uv/python/node + repo clone) need
    far more than the old 300s default — pin the floor (the value verified in
    the container E2E) so it doesn't regress."""
    import astroai_lab.agent.setup_state as ss

    assert ss.INSTALL_TIMEOUT_SEC >= 1500


def test_install_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """ASTROAI_LAB_AGENT_INSTALL_TIMEOUT still overrides the (raised) default."""
    import importlib

    import astroai_lab.agent.setup_state as ss

    monkeypatch.setenv("ASTROAI_LAB_AGENT_INSTALL_TIMEOUT", "45")
    importlib.reload(ss)
    try:
        assert ss.INSTALL_TIMEOUT_SEC == 45
    finally:
        monkeypatch.delenv("ASTROAI_LAB_AGENT_INSTALL_TIMEOUT")
        importlib.reload(ss)
        assert ss.INSTALL_TIMEOUT_SEC >= 1500
