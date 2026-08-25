"""Tests for the Phase 6 factory-reset wipe (`agent wipe`).

Covers ``wipe_agent_state`` (dry-run + real, per-agent removal delegation,
state-dir / Cursor / shared-config removal, error capture) and the CLI surface
(confirmation gate, --yes, --json requires --yes, --dry-run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.wipe import wipe_agent_state
from astroai_lab.cli.main import app

runner = CliRunner()


def _fake_session_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Point session bin/npm dirs at tmp dirs; disable real npm subprocesses."""
    bin_dir = tmp_path / "bin"
    npm_prefix = tmp_path / "npm"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (npm_prefix / "bin").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("astroai_lab.agent.install._bin_dir", lambda: bin_dir)
    monkeypatch.setattr("astroai_lab.agent.install._npm_prefix", lambda: npm_prefix)
    monkeypatch.setattr("astroai_lab.agent.install.shutil.which", lambda _: None)
    monkeypatch.setattr("astroai_lab.agent.install.run", lambda *a, **k: None)
    return bin_dir, npm_prefix


def _make_installed_hermes(bin_dir: Path, home: Path) -> None:
    """Simulate an installed hermes: binary + config + skills + stamp."""
    (bin_dir / "hermes").write_text("#!/bin/sh\n", encoding="utf-8")
    cfg = home / ".hermes" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("model: test\n", encoding="utf-8")
    skill = home / ".hermes" / "skills" / "astroai-ray" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# astroai-ray\n", encoding="utf-8")
    stamp = home / ".astroai" / "lab" / "agent-setup-stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("2026-08-02T00:00:00Z bundle=test mode=install\n", encoding="utf-8")


def _make_cursor_state(home: Path) -> None:
    mcp = home / ".cursor" / "mcp.json"
    mcp.parent.mkdir(parents=True, exist_ok=True)
    mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    rule = home / ".cursor" / "rules" / "token-efficient.mdc"
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text("# be concise\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# wipe_agent_state
# ---------------------------------------------------------------------------


def test_wipe_dry_run_reports_and_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _make_installed_hermes(tmp_path / "bin", home)
    _make_cursor_state(home)

    results = wipe_agent_state(home=home, dry_run=True)
    assert results
    assert all(r["status"] == "would_remove" for r in results)
    # nothing actually removed
    assert (home / ".hermes" / "config.yaml").is_file()
    assert (home / ".cursor" / "mcp.json").is_file()
    assert (home / ".astroai" / "lab" / "agent-setup-stamp").is_file()
    targets = {r["target"] for r in results}
    assert "state:stamp" in targets  # agent setup stamp is wiped
    assert "cursor" in targets  # whole ~/.cursor dir is removed (factory reset)


def test_wipe_removes_agents_state_and_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _make_installed_hermes(tmp_path / "bin", home)
    _make_cursor_state(home)

    results = wipe_agent_state(home=home)
    assert results
    assert all(r["status"] in ("removed",) for r in results)
    # agent binary + config + skills gone
    assert not (tmp_path / "bin" / "hermes").exists()
    assert not (home / ".hermes").exists()  # purge removes the whole home dir
    # state + cursor gone
    assert not (home / ".astroai" / "lab").exists()
    assert not (home / ".cursor").exists()


def test_wipe_empty_home_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert wipe_agent_state(home=home) == []


def test_wipe_leaves_canfar_lab_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CANFAR config (~/.config/canfar/lab) holds ray-manager.env — wipe must not touch it."""
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    canfar = home / ".config" / "canfar" / "lab"
    canfar.mkdir(parents=True)
    envfile = canfar / "ray-manager.env"
    envfile.write_text("RAY_ADDRESS=http://example\n", encoding="utf-8")
    _make_installed_hermes(tmp_path / "bin", home)

    wipe_agent_state(home=home)
    assert envfile.is_file()
    assert envfile.read_text(encoding="utf-8") == "RAY_ADDRESS=http://example\n"


def test_wipe_keeps_saved_environments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env saves (~/.astroai/lab/saves/) and prefs (config.yaml) survive a wipe."""
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    saved = home / ".astroai" / "lab" / "saves" / "mylab"
    saved.mkdir(parents=True)
    (saved / "pixi.toml").write_text("[project]\n", encoding="utf-8")
    prefs = home / ".astroai" / "lab" / "config.yaml"
    prefs.write_text("default_pm: uv\n", encoding="utf-8")
    _make_installed_hermes(tmp_path / "bin", home)

    wipe_agent_state(home=home)
    # saves + preferences preserved; the agent stamp is gone
    assert (home / ".astroai" / "lab" / "saves" / "mylab" / "pixi.toml").is_file()
    assert prefs.is_file()
    assert not (home / ".astroai" / "lab" / "agent-setup-stamp").exists()
    assert (home / ".astroai" / "lab").is_dir()  # lab dir survives while it holds data


def test_wipe_unknown_agent_error_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A registry entry that fails to remove is captured, not fatal."""
    from astroai_lab.agent import registry as registry_mod

    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        registry_mod,
        "registry_ids",
        lambda: {"hermes", "boom"},
    )

    def boom(agent_id, *, home=None, purge=False, clean_home=False, dry_run=False):
        from astroai_lab.errors import LabError

        if agent_id == "boom":
            raise LabError("boom failed")
        return [{"target": "x", "status": "removed", "detail": "y"}]

    monkeypatch.setattr(registry_mod, "remove_registry_agent", boom)
    results = wipe_agent_state(home=home)
    errs = [r for r in results if r["status"] == "error"]
    assert any("boom" in r["detail"] for r in errs)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_wipe_dry_run_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _make_installed_hermes(tmp_path / "bin", home)
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["--json", "agent", "wipe", "--dry-run"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["dry_run"] is True
    assert data["counts"]["would_remove"] > 0
    assert (home / ".hermes" / "config.yaml").is_file()  # untouched


def test_cli_wipe_json_requires_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["--json", "agent", "wipe"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "requires --yes" in data["errors"][0]


def test_cli_wipe_yes_removes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _make_installed_hermes(tmp_path / "bin", home)
    _make_cursor_state(home)
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["--json", "agent", "wipe", "--yes"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["counts"]["removed"] > 0
    assert not (home / ".hermes").exists()
    assert not (home / ".cursor").exists()


def test_cli_wipe_confirmation_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _make_installed_hermes(tmp_path / "bin", home)
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["agent", "wipe"], input="n\n")
    assert result.exit_code == 0
    assert "cancelled" in (result.stdout + result.stderr).lower()
    assert (home / ".hermes" / "config.yaml").is_file()  # untouched


def test_cli_wipe_confirmation_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _make_installed_hermes(tmp_path / "bin", home)
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["agent", "wipe"], input="y\n")
    assert result.exit_code == 0
    assert "wiped" in (result.stdout + result.stderr).lower()
    assert not (home / ".hermes").exists()


def test_cli_wipe_help_mentions_verb() -> None:
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "wipe" in (result.stdout + result.stderr)
