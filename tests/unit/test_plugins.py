"""Unit tests for the plugin system (mcp / tool / rule only).

Skills are owned by ``npx skills`` — AstroAI plugins do not install them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.plugins import (
    configure_plugin,
    get_plugin,
    install_plugin,
    load_plugins,
    plugin_ids,
    plugin_installed,
    plugin_status,
    remove_agent_plugin_files,
    remove_plugin,
    update_plugin,
)
from astroai_lab.cli.main import app
from astroai_lab.errors import LabError

runner = CliRunner()


def _write_plugin_yaml(root: Path, name: str, body: str) -> Path:
    plugins = root / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    path = plugins / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Loader + schema validation
# ---------------------------------------------------------------------------


def test_expand_agent_matrix_aliases() -> None:
    from astroai_lab.agent.agent_targets import mcp_hosts, skill_hosts
    from astroai_lab.agent.plugins import expand_agent_matrix

    assert expand_agent_matrix(["skill-hosts"]) == list(skill_hosts())
    assert expand_agent_matrix(["mcp-hosts"]) == list(mcp_hosts())
    assert expand_agent_matrix(["cursor"]) == ["cursor"]
    assert "hermes" in expand_agent_matrix(["skill-hosts"])
    assert "claude" in expand_agent_matrix(["mcp-hosts"])


def test_load_plugins_mcp_tool_rule_only() -> None:
    plugins = load_plugins()
    ids = [p["id"] for p in plugins]
    assert ids == sorted(ids)
    kinds = {p["kind"] for p in plugins}
    assert kinds <= {"mcp", "tool", "rule"}
    assert "ray-manager-mcp" in ids
    assert "token-efficient" in ids
    assert "skore-cli" in ids
    assert "ponytail-rule" in ids
    assert "astroai-ray" not in ids
    assert "canfar-platform" not in ids
    assert "ponytail" not in ids


def test_load_plugins_ray_manager_mcp() -> None:
    plugin = get_plugin("ray-manager-mcp")
    assert plugin is not None
    assert plugin["kind"] == "mcp"
    from astroai_lab.agent.agent_targets import mcp_hosts

    assert set(plugin["agents"]) == set(mcp_hosts())
    assert plugin["install"]["server"] == "ray-manager"


def test_canfar_ray_skill_lives_in_canfar_skills() -> None:
    from tests.conftest import CANFAR_SKILLS_SRC

    skill = CANFAR_SKILLS_SRC / "skills/astroai-ray/SKILL.md"
    if not skill.is_file():
        pytest.skip("canfar-skills fixture missing astroai-ray skill")
    text = skill.read_text(encoding="utf-8")
    assert "astroai run" in text
    assert "Do not call `ray job submit`" in text
    assert "cluster start" in text


def test_load_plugins_empty_dir(tmp_path: Path) -> None:
    assert load_plugins(tmp_path) == []


def test_plugin_ids_and_get() -> None:
    assert "ray-manager-mcp" in plugin_ids()
    assert get_plugin("not-a-plugin") is None


def test_validation_missing_required_key(tmp_path: Path) -> None:
    body = "kind: mcp\nsummary: x\nagents: [cursor]\ninstall:\n  server: s\n  entry: {}\n"
    _write_plugin_yaml(tmp_path, "broken", body)
    with pytest.raises(LabError, match="missing required key"):
        load_plugins(tmp_path)


def test_validation_bad_kind(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: skill\nsummary: x\nagents: [a]\ninstall:\n  source: x\n",
    )
    with pytest.raises(LabError, match="invalid kind"):
        load_plugins(tmp_path)


def test_validation_empty_agents(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: mcp\nsummary: x\nagents: []\ninstall:\n  server: s\n  entry: {}\n",
    )
    with pytest.raises(LabError, match="non-empty agents"):
        load_plugins(tmp_path)


def test_validation_mcp_missing_entry(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: mcp\nsummary: x\nagents: [cursor]\ninstall:\n  server: s\n",
    )
    with pytest.raises(LabError, match="install.server and install.entry"):
        load_plugins(tmp_path)


def test_validation_rule_requires_type(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: rule\nsummary: x\nagents: [cursor]\ninstall: {}\n",
    )
    with pytest.raises(LabError, match="requires install.type"):
        load_plugins(tmp_path)


def test_validation_bad_transport(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: rule\nsummary: x\nagents: [cursor]\n"
        "install:\n  type: github-skill\n  repo: org/repo\n  path: x\n",
    )
    with pytest.raises(LabError, match="invalid install.type"):
        load_plugins(tmp_path)


def test_validation_bad_yaml(tmp_path: Path) -> None:
    _write_plugin_yaml(tmp_path, "broken", "id: [unclosed")
    with pytest.raises(LabError, match="Invalid YAML"):
        load_plugins(tmp_path)


# ---------------------------------------------------------------------------
# configure (mcp kind)
# ---------------------------------------------------------------------------


def _mcp_plugin_dict() -> dict:
    return {
        "id": "ray-manager",
        "kind": "mcp",
        "tags": ["ray"],
        "summary": "Ray manager MCP tools",
        "agents": ["cursor", "openclaw"],
        "install": {
            "server": "ray-manager",
            "entry": {
                "command": "astroai",
                "args": ["mcp", "serve"],
                "env": {"ASTROAI_RAY_JOBS_ADDRESS": "$ASTROAI_RAY_JOBS_ADDRESS"},
            },
        },
    }


def _plugin_ctx(plugin: dict):
    from astroai_lab.agent import plugins as plugins_mod

    original = plugins_mod.get_plugin

    def fake_get(plugin_id, root=None):
        return plugin if plugin_id == plugin["id"] else None

    return plugins_mod, original, fake_get


def configure_plugin_from_dict(plugin: dict, home: Path, *, agent=None, force=False, dry_run=False):
    plugins_mod, original, fake_get = _plugin_ctx(plugin)
    plugins_mod.get_plugin = fake_get
    try:
        return configure_plugin(plugin["id"], agent=agent, home=home, force=force, dry_run=dry_run)
    finally:
        plugins_mod.get_plugin = original


def remove_plugin_from_dict(plugin: dict, home: Path, *, agent=None):
    plugins_mod, original, fake_get = _plugin_ctx(plugin)
    plugins_mod.get_plugin = fake_get
    try:
        return remove_plugin(plugin["id"], agent=agent, home=home)
    finally:
        plugins_mod.get_plugin = original


def test_configure_mcp_merges_cursor_config(tmp_path: Path) -> None:
    results = configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor")
    assert results[0].status == "installed"
    mcp_file = tmp_path / ".cursor" / "mcp.json"
    data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "ray-manager" in data["mcpServers"]
    assert data["mcpServers"]["ray-manager"]["command"] == "astroai"
    assert data["mcpServers"]["ray-manager"]["env"]["ASTROAI_RAY_JOBS_ADDRESS"].startswith("$")


def test_configure_mcp_merges_openclaw_config(tmp_path: Path) -> None:
    results = configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="openclaw")
    assert results[0].status == "installed"
    data = json.loads((tmp_path / ".openclaw" / "openclaw.json").read_text(encoding="utf-8"))
    assert "ray-manager" in data["mcpServers"]


def test_configure_mcp_skip_when_present(tmp_path: Path) -> None:
    results = configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor")
    assert results[0].status == "installed"
    results = configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor")
    assert results[0].status == "skipped"
    results = configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor", force=True)
    assert results[0].status == "installed"


def test_configure_mcp_dry_run(tmp_path: Path) -> None:
    results = configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor", dry_run=True)
    assert results[0].status == "would_install"
    assert not (tmp_path / ".cursor").exists()


def test_configure_mcp_remove(tmp_path: Path) -> None:
    configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor")
    results = remove_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor")
    assert results[0].status == "removed"
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "ray-manager" not in data["mcpServers"]


def test_plugin_installed_mcp_present(tmp_path: Path) -> None:
    plugin = _mcp_plugin_dict()
    assert plugin_installed(plugin, tmp_path, "cursor") is False
    configure_plugin_from_dict(plugin, tmp_path, agent="cursor")
    assert plugin_installed(plugin, tmp_path, "cursor") is True


def test_plugin_status_mcp(tmp_path: Path) -> None:
    status = plugin_status(_mcp_plugin_dict(), tmp_path)
    assert status["any_installed"] is False
    configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor")
    status = plugin_status(_mcp_plugin_dict(), tmp_path)
    assert status["installed"]["cursor"] is True
    assert status["any_installed"] is True


def test_install_plugin_unknown() -> None:
    with pytest.raises(LabError, match="Unknown plugin"):
        install_plugin("not-a-plugin")


def test_install_plugin_mcp_no_installed_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: False)
    results = install_plugin("ray-manager-mcp", home=tmp_path)
    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "no installed agent" in results[0].detail


def test_install_plugin_mcp_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    results = install_plugin("ray-manager-mcp", home=tmp_path, agent="cursor")
    assert len(results) == 1
    assert results[0].status == "installed"
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "ray-manager" in data["mcpServers"]


def test_update_plugin_forces_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("ray-manager-mcp", home=tmp_path, agent="cursor")
    results = update_plugin("ray-manager-mcp", home=tmp_path, agent="cursor")
    assert all(r.status == "installed" for r in results)


def test_remove_plugin_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("ray-manager-mcp", home=tmp_path, agent="cursor")
    results = remove_plugin("ray-manager-mcp", home=tmp_path, agent="cursor")
    assert results[0].status == "removed"


def test_remove_plugin_unknown_agent() -> None:
    with pytest.raises(LabError, match="does not support agent"):
        remove_plugin("ray-manager-mcp", agent="not-an-agent")


def test_remove_agent_plugin_files_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("ray-manager-mcp", home=tmp_path, agent="cursor")
    rows = remove_agent_plugin_files("cursor", home=tmp_path)
    assert any(r["status"] == "removed" for r in rows)
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "ray-manager" not in data.get("mcpServers", {})


def test_remove_agent_plugin_files_unknown_agent(tmp_path: Path) -> None:
    assert remove_agent_plugin_files("not-an-agent", home=tmp_path) == []


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_plugins_list_json() -> None:
    result = runner.invoke(app, ["--json", "agent", "plugins", "list"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    ids = {row["id"] for row in data}
    assert "ray-manager-mcp" in ids
    row = next(r for r in data if r["id"] == "ray-manager-mcp")
    assert row["kind"] == "mcp"
    assert "astroai-ray" not in ids


def test_cli_plugins_list_matches_agent_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "plugins", "list"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "Plugin" in out
    assert "Kind" in out
    assert "ray-manager-mcp" in out
    assert "token-efficient" in out
    assert "npx skills" in out
    assert "mcp-hosts" in out


def test_cli_plugins_list_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "plugins", "list", "--description"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "Ray cluster" in out or "ray" in out.lower()


def test_cli_plugins_install_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    argv = ["--json", "--dry-run", "agent", "plugins", "install", "ray-manager-mcp"]
    result = runner.invoke(app, argv)
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["plugin"] == "ray-manager-mcp"
    assert data["dry_run"] is True
    assert data["actions"]


def test_cli_plugins_install_unknown() -> None:
    result = runner.invoke(app, ["--json", "agent", "plugins", "install", "not-a-plugin"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "Unknown plugin" in data["errors"][0]


def test_cli_plugins_help_mentions_verb() -> None:
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "plugins" in (result.stdout + result.stderr)


def test_seed_catalog_kinds() -> None:
    by_id = {p["id"]: p for p in load_plugins()}
    assert by_id["ray-manager-mcp"]["kind"] == "mcp"
    assert by_id["token-efficient"]["kind"] == "rule"
    assert by_id["skore-cli"]["kind"] == "tool"
    assert by_id["ponytail-rule"]["kind"] == "rule"
    assert by_id["ast-grep-cli"]["kind"] == "tool"
    assert by_id["git-mcp"]["kind"] == "mcp"
    assert by_id["token-efficient"].get("default") is True
