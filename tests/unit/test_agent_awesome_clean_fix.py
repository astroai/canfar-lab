"""Unit tests for agent list coverage, clean state, auto-repair, and UI endpoints."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.clean_agent import clean_agent_state
from astroai_lab.agent.fix import fix_agent_setup
from astroai_lab.agent.install import TOOLS
from astroai_lab.agent.interact import inspect_interact_endpoints
from astroai_lab.agent.registry import list_registry_agents
from astroai_lab.cli.main import app

runner = CliRunner()


def test_list_covers_all_installable_agents() -> None:
    # Utilities in TOOLS (node / ast-grep) are not agents; hyperfine is image-baked.
    from astroai_lab.agent.install import TOOL_UTILITIES

    ids = {a["id"] for a in list_registry_agents()}
    assert set(TOOLS) - TOOL_UTILITIES <= ids
    assert ids.isdisjoint(TOOL_UTILITIES)
    assert "hyperfine" not in TOOLS
    assert any(a.get("summary") for a in list_registry_agents())


def test_clean_agent_state(tmp_path: Path) -> None:
    state_dir = tmp_path / ".astroai" / "lab"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_file = state_dir / "agent-setup.lock"
    failed_file = state_dir / "agent-setup-failed"
    log_file = state_dir / "agent-setup.log"
    lock_file.write_text("1234 1000", encoding="utf-8")
    failed_file.write_text("exit=1", encoding="utf-8")
    log_file.write_text("log content", encoding="utf-8")

    # Empty config file
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    empty_cfg = cursor_dir / "mcp.json"
    empty_cfg.write_text("{}", encoding="utf-8")

    # Test dry-run clean
    dry_results = clean_agent_state(home=tmp_path, logs=True, dry_run=True)
    assert any(r.status == "would_remove" for r in dry_results)

    # Test actual clean
    results = clean_agent_state(home=tmp_path, logs=True, dry_run=False)
    assert not lock_file.exists()
    assert not failed_file.exists()
    assert not log_file.exists()
    assert not empty_cfg.exists()
    assert len(results) >= 4


def test_fix_agent_setup(tmp_path: Path) -> None:
    # Create corrupted JSON file and missing dirs
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = cursor_dir / "mcp.json"
    mcp_file.write_text("{ broken json: ", encoding="utf-8")

    # Failed marker
    state_dir = tmp_path / ".astroai" / "lab"
    state_dir.mkdir(parents=True, exist_ok=True)
    failed_file = state_dir / "agent-setup-failed"
    failed_file.write_text("error", encoding="utf-8")

    # Test dry-run fix
    dry_results = fix_agent_setup(home=tmp_path, dry_run=True)
    assert len(dry_results) > 0

    # Test actual fix
    results = fix_agent_setup(home=tmp_path, dry_run=False)
    assert mcp_file.is_file()
    assert not failed_file.exists()
    # Content should be repaired to valid JSON
    data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    assert any(r.fixed for r in results)


def test_inspect_interact_endpoints() -> None:
    info = inspect_interact_endpoints()
    assert "session_kind" in info
    assert "endpoints" in info
    assert isinstance(info["endpoints"], list)


def test_cli_agent_plugins_list() -> None:
    result = runner.invoke(app, ["--json", "agent", "plugins", "list"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert any(row.get("id") == "ponytail-rule" for row in data)

    res_kind = runner.invoke(app, ["agent", "plugins", "list", "--kind", "skill"])
    assert res_kind.exit_code == 0


def test_cli_agent_awesome_alias_removed() -> None:
    """The `agent awesome` alias was removed (use `agent list` / `agent plugins list`)."""
    result = runner.invoke(app, ["--json", "agent", "awesome"])
    assert result.exit_code != 0
    assert "No such command" in (result.stdout + result.stderr)


def test_cli_agent_verify_clean() -> None:
    result = runner.invoke(app, ["--json", "agent", "verify", "--clean"])
    assert result.exit_code == 0

    res_dry = runner.invoke(app, ["agent", "verify", "--clean", "--dry-run"])
    assert res_dry.exit_code == 0


def test_cli_agent_verify_fix_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
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
    result = runner.invoke(app, ["--json", "agent", "verify", "--fix"])
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    assert "ok" in data
    assert "issues" in data

    res_dry = runner.invoke(app, ["agent", "verify", "--fix", "--dry-run"])
    assert res_dry.exit_code in (0, 1)


def test_cli_agent_list_hides_description_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    # Registry summaries mention "agent" tooling; without --description they stay hidden.
    assert "agent list --description" in out
    # A known long summary fragment from hermes.yaml should not appear by default.
    from astroai_lab.agent.registry import get_registry_agent

    summary = (get_registry_agent("hermes") or {}).get("summary") or ""
    assert summary
    assert summary not in out


def test_cli_agent_list_description_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    from astroai_lab.agent.registry import get_registry_agent

    summary = (get_registry_agent("hermes") or {}).get("summary") or ""
    for flag in ("--description",):
        result = runner.invoke(app, ["agent", "list", flag])
        assert result.exit_code == 0
        assert summary in (result.stdout + result.stderr)


def test_cli_agent_list_ui() -> None:
    result = runner.invoke(app, ["--json", "agent", "list", "--ui"])
    assert result.exit_code == 0
    assert "endpoints" in result.output

    res_plain = runner.invoke(app, ["agent", "list", "--ui"])
    assert res_plain.exit_code == 0


def test_cli_agent_verify_fix() -> None:
    result = runner.invoke(app, ["agent", "verify", "--fix"])
    assert result.exit_code == 0 or result.exit_code == 1
