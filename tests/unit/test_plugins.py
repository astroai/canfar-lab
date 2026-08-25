"""Unit tests for the plugin system.

Covers the plugins/*.yaml loader + schema validation, per-agent installed
status, install/update/remove/configure for the skill / mcp kinds,
recursive agent removal (remove_agent_plugin_files), and the CLI surface.
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
from tests.conftest import CANFAR_SKILLS_SRC, mock_canfar_skills_upstream

runner = CliRunner()

_mock_canfar_skills_upstream = mock_canfar_skills_upstream


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


def test_load_plugins_includes_canfar_ray() -> None:
    plugins = load_plugins()
    ids = [p["id"] for p in plugins]
    assert "astroai-ray" in ids
    assert ids == sorted(ids)
    plugin = get_plugin("astroai-ray")
    assert plugin is not None
    assert plugin["kind"] == "skill"
    from astroai_lab.agent.agent_targets import skill_hosts

    assert set(plugin["agents"]) == set(skill_hosts())
    assert "cursor" in plugin["agents"]
    assert plugin["install"]["type"] == "github-skill"
    assert plugin["install"]["repo"] == "astroai/canfar-skills"
    assert plugin["install"]["path"] == "skills/astroai-ray"


def test_load_plugins_includes_canfar_platform() -> None:
    plugin = get_plugin("canfar-platform")
    assert plugin is not None
    assert plugin.get("default") is True
    assert plugin["install"]["type"] == "github-bundle"
    assert plugin["install"]["repo"] == "astroai/canfar-skills"
    assert len(plugin["install"]["skills"]) == 23


def test_canfar_ray_skill_points_at_workload_run() -> None:
    skill = CANFAR_SKILLS_SRC / "skills/astroai-ray/SKILL.md"
    if not skill.is_file():
        pytest.skip("canfar-skills fixture missing astroai-ray skill")
    text = skill.read_text(encoding="utf-8")
    assert "astroai run" in text
    assert "Do not call `ray job submit`" in text
    assert "cluster start" in text


def test_load_plugins_empty_dir(tmp_path: Path) -> None:
    assert load_plugins(tmp_path) == []


def test_plugin_ids_and_get(tmp_path: Path) -> None:
    assert "astroai-ray" in plugin_ids()
    assert get_plugin("not-a-plugin") is None


def test_validation_missing_required_key(tmp_path: Path) -> None:
    body = "kind: skill\nsummary: x\nagents: [a]\ninstall:\n  source: x\n"
    _write_plugin_yaml(tmp_path, "broken", body)
    with pytest.raises(LabError, match="missing required key"):
        load_plugins(tmp_path)


def test_validation_bad_kind(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: widget\nsummary: x\nagents: [a]\ninstall:\n  source: x\n",
    )
    with pytest.raises(LabError, match="invalid kind"):
        load_plugins(tmp_path)


def test_validation_empty_agents(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: skill\nsummary: x\nagents: []\ninstall:\n  source: x\n",
    )
    with pytest.raises(LabError, match="non-empty agents"):
        load_plugins(tmp_path)


def test_validation_skill_missing_source(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: skill\nsummary: x\nagents: [a]\ninstall:\n  targets:\n    a: .x\n",
    )
    with pytest.raises(LabError, match="install.source"):
        load_plugins(tmp_path)


def test_validation_mcp_missing_entry(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: mcp\nsummary: x\nagents: [cursor]\ninstall:\n  server: s\n",
    )
    with pytest.raises(LabError, match="install.server and install.entry"):
        load_plugins(tmp_path)


def test_validation_bad_yaml(tmp_path: Path) -> None:
    _write_plugin_yaml(tmp_path, "broken", "id: [unclosed")
    with pytest.raises(LabError, match="Invalid YAML"):
        load_plugins(tmp_path)


# ---------------------------------------------------------------------------
# Installed status
# ---------------------------------------------------------------------------


def _skill_plugin_dict() -> dict:
    return {
        "id": "astroai-ray",
        "kind": "skill",
        "tags": ["science", "ray"],
        "summary": "Drive CANFAR Ray clusters",
        "agents": ["hermes", "openclaw"],
        "install": {
            "source": "astroai-ray",
            "targets": {
                "hermes": ".hermes/skills/astroai-ray",
                "openclaw": ".openclaw/skills/astroai-ray",
            },
        },
    }


def test_plugin_status_not_installed(tmp_path: Path) -> None:
    status = plugin_status(_skill_plugin_dict(), tmp_path)
    assert status["any_installed"] is False
    assert status["installed"] == {"hermes": False, "openclaw": False}


def test_plugin_status_installed_one_agent(tmp_path: Path) -> None:
    dst = tmp_path / ".hermes" / "skills" / "astroai-ray"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("# astroai-ray\n", encoding="utf-8")
    status = plugin_status(_skill_plugin_dict(), tmp_path)
    assert status["installed"]["hermes"] is True
    assert status["installed"]["openclaw"] is False
    assert status["any_installed"] is True


# ---------------------------------------------------------------------------
# Install / update / remove (skill kind)
# ---------------------------------------------------------------------------


def test_install_plugin_unknown() -> None:
    with pytest.raises(LabError, match="Unknown plugin"):
        install_plugin("not-a-plugin")


def test_install_plugin_skill_no_installed_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: False)
    results = install_plugin("astroai-ray", home=tmp_path)
    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "no installed agent" in results[0].detail


def test_install_plugin_skill_copies_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    results = install_plugin("astroai-ray", home=tmp_path)
    assert results
    assert all(r.status == "installed" for r in results)
    for agent in ("hermes", "openclaw"):
        dst = tmp_path / f".{agent}" / "skills" / "astroai-ray" / "SKILL.md"
        assert dst.is_file()


def test_install_plugin_skill_scope_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    results = install_plugin("astroai-ray", home=tmp_path, agent="hermes")
    assert len(results) == 1
    assert results[0].agent == "hermes"
    assert results[0].status == "installed"
    assert not (tmp_path / ".openclaw").exists()


def test_install_plugin_skill_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    results = install_plugin("astroai-ray", home=tmp_path, dry_run=True)
    assert all(r.status == "would_install" for r in results)
    assert not (tmp_path / ".hermes").exists()


def test_install_plugin_skill_skip_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("astroai-ray", home=tmp_path)
    results = install_plugin("astroai-ray", home=tmp_path)
    assert all(r.status == "skipped" for r in results)
    # force re-applies
    results = install_plugin("astroai-ray", home=tmp_path, force=True)
    assert all(r.status == "installed" for r in results)


def test_update_plugin_forces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("astroai-ray", home=tmp_path)
    results = update_plugin("astroai-ray", home=tmp_path)
    assert all(r.status == "installed" for r in results)  # force re-apply


def test_remove_plugin_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("astroai-ray", home=tmp_path)
    results = remove_plugin("astroai-ray", home=tmp_path)
    assert all(r.status == "no-op" for r in results)
    assert (tmp_path / ".hermes" / "skills" / "astroai-ray" / "SKILL.md").is_file()
    assert (tmp_path / ".openclaw" / "skills" / "astroai-ray" / "SKILL.md").is_file()


def test_remove_plugin_skill_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("astroai-ray", home=tmp_path)
    results = remove_plugin("astroai-ray", home=tmp_path, dry_run=True)
    assert all(r.status == "no-op" for r in results)
    assert (tmp_path / ".hermes" / "skills" / "astroai-ray" / "SKILL.md").is_file()


def test_remove_plugin_scope_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("astroai-ray", home=tmp_path)
    results = remove_plugin("astroai-ray", home=tmp_path, agent="hermes")
    assert len(results) == 1
    assert results[0].agent == "hermes"
    assert results[0].status == "no-op"
    assert (tmp_path / ".hermes" / "skills" / "astroai-ray" / "SKILL.md").is_file()
    assert (tmp_path / ".openclaw" / "skills" / "astroai-ray" / "SKILL.md").is_file()


def test_remove_plugin_unknown_agent() -> None:
    with pytest.raises(LabError, match="does not support agent"):
        remove_plugin("astroai-ray", agent="not-an-agent")


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


def test_configure_mcp_merges_cursor_config(tmp_path: Path) -> None:
    results = configure_plugin_from_dict(_mcp_plugin_dict(), tmp_path, agent="cursor")
    assert results[0].status == "installed"
    mcp_file = tmp_path / ".cursor" / "mcp.json"
    data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "ray-manager" in data["mcpServers"]
    assert data["mcpServers"]["ray-manager"]["command"] == "astroai"
    # Dynamic URL only — env reference, never a hardcoded manager URL.
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
    # force re-merges
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


def test_configure_skill_is_noop(tmp_path: Path) -> None:
    results = configure_plugin_from_dict(_skill_plugin_dict(), tmp_path, agent="hermes")
    assert results[0].status == "no-op"


def test_plugin_installed_mcp_present(tmp_path: Path) -> None:
    plugin = _mcp_plugin_dict()
    assert plugin_installed(plugin, tmp_path, "cursor") is False
    configure_plugin_from_dict(plugin, tmp_path, agent="cursor")
    assert plugin_installed(plugin, tmp_path, "cursor") is True


# ---------------------------------------------------------------------------
# Recursive agent removal
# ---------------------------------------------------------------------------


def test_remove_agent_plugin_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canfar_skills_upstream(monkeypatch)
    monkeypatch.setattr("astroai_lab.agent.plugins._agent_installed", lambda a, h=None: True)
    install_plugin("astroai-ray", home=tmp_path)
    rows = remove_agent_plugin_files("hermes", home=tmp_path)
    assert rows == []  # github-skill: no automated plugin removal
    assert (tmp_path / ".hermes" / "skills" / "astroai-ray" / "SKILL.md").is_file()
    # openclaw untouched
    assert (tmp_path / ".openclaw" / "skills" / "astroai-ray" / "SKILL.md").is_file()


def test_remove_agent_plugin_files_unknown_agent(tmp_path: Path) -> None:
    assert remove_agent_plugin_files("not-an-agent", home=tmp_path) == []


# ---------------------------------------------------------------------------
# Helpers that inject a plugin dict (root-less) by monkeypatching get_plugin
# ---------------------------------------------------------------------------


def _plugin_ctx(plugin: dict):
    """Context manager swapping plugins.get_plugin for the injected plugin."""
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


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_plugins_list_json() -> None:
    result = runner.invoke(app, ["--json", "agent", "plugins", "list"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    ids = {row["id"] for row in data}
    assert "astroai-ray" in ids
    row = next(r for r in data if r["id"] == "astroai-ray")
    assert row["kind"] == "skill"


def test_cli_plugins_list_matches_agent_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "plugins", "list"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "Plugin" in out
    assert "Kind" in out
    assert "On" in out
    assert "Def" in out
    assert "Agents" in out
    assert "ponytail" in out
    assert "matplotlib-data-visualization" in out
    assert "agent plugins list --description" in out
    assert "skill-hosts" in out
    assert "mcp-hosts" in out
    assert "YAGNI ladder" not in out


def test_cli_plugins_list_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "plugins", "list", "--description"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "YAGNI ladder" in out
    assert "Matplotlib plotting" in out


def test_cli_plugins_install_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    argv = ["--json", "--dry-run", "agent", "plugins", "install", "astroai-ray"]
    result = runner.invoke(app, argv)
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["plugin"] == "astroai-ray"
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


def test_cli_agent_list_json_is_status_shaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["--json", "agent", "list"])
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    assert "agents" in data
    # Plugins live under `agent plugins list`.
    plugins = runner.invoke(app, ["--json", "agent", "plugins", "list"])
    assert plugins.exit_code == 0
    ids = {row["id"] for row in json.loads(plugins.stdout)}
    assert "astroai-ray" in ids


# ---------------------------------------------------------------------------
# Migrated addons (Phase 3 seed-catalog: addons.json -> plugins/*.yaml)
# ---------------------------------------------------------------------------


def test_load_plugins_includes_migrated_addons() -> None:
    """Legacy addons now load as plugins with an `addon` marker + transport."""
    plugins = load_plugins()
    by_id = {p["id"]: p for p in plugins}
    for addon_id in ("ponytail", "polars", "git-mcp", "token-efficient", "ast-grep-cli"):
        assert addon_id in by_id, f"missing migrated addon {addon_id}"
        assert by_id[addon_id]["addon"] is True
        assert by_id[addon_id]["install"]["type"]
    # Natural kind mapping preserved.
    assert by_id["ponytail"]["kind"] == "bundle"
    assert by_id["polars"]["kind"] == "skill"
    assert by_id["git-mcp"]["kind"] == "mcp"
    assert by_id["ast-grep-cli"]["kind"] == "tool"
    assert by_id["token-efficient"]["kind"] == "rule"
    assert "hyperfine" not in by_id
    assert "gws-cli" not in by_id
    # Defaults carried over.
    assert by_id["token-efficient"]["default"] is True
    assert by_id["ponytail"].get("default") is None
    assert {Path(p).name for p in by_id["ponytail"]["install"]["skills"]} == {
        "ponytail",
        "ponytail-review",
        "ponytail-audit",
        "ponytail-debt",
        "ponytail-gain",
        "ponytail-help",
    }
    # Opt-in skills point at real SKILL.md paths (not skill-forge recipes/ packaging dirs).
    assert by_id["polars"]["install"]["path"] == "skills/polars"
    assert by_id["librarian"]["install"]["repo"] == "mitsuhiko/agent-stuff"


def test_validation_addon_transport_requires_fields(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: skill\nsummary: x\nagents: [cursor]\n"
        "install:\n  type: github-skill\n  repo: org/repo\n",
    )
    with pytest.raises(LabError, match="requires repo and path"):
        load_plugins(tmp_path)
    # The first broken entry still fails on reload — drop it before the next case.
    (tmp_path / "plugins" / "broken.yaml").unlink()
    _write_plugin_yaml(
        tmp_path,
        "broken2",
        "id: broken2\nkind: mcp\nsummary: x\nagents: [cursor]\ninstall:\n  type: mcp-snippet\n",
    )
    with pytest.raises(LabError, match="requires server"):
        load_plugins(tmp_path)


def test_validation_addon_transport_bad_type(tmp_path: Path) -> None:
    _write_plugin_yaml(
        tmp_path,
        "broken",
        "id: broken\nkind: skill\nsummary: x\nagents: [cursor]\ninstall:\n  type: mystery\n",
    )
    with pytest.raises(LabError, match="invalid install.type"):
        load_plugins(tmp_path)


def test_plugin_status_includes_default_field() -> None:
    plugin = _skill_plugin_dict()
    plugin["default"] = True
    status = plugin_status(plugin, Path("/tmp"))
    assert status["default"] is True
    assert plugin_status(_skill_plugin_dict(), Path("/tmp"))["default"] is False


def test_cursor_agent_always_installed() -> None:
    from astroai_lab.agent.plugins import _agent_installed

    assert _agent_installed("cursor", home=Path("/tmp")) is True


def test_install_addon_transport_dry_run(tmp_path: Path) -> None:
    """`plugins install` on a migrated mcp-snippet addon -> would_install."""
    results = install_plugin("git-mcp", home=tmp_path, installed_only=False, dry_run=True)
    assert results
    assert all(r.status == "would_install" for r in results)
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_install_addon_transport_skips_when_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migrated mcp-snippet addon: skipped once the server is in the config."""
    from astroai_lab.agent.agent_targets import merge_mcp_server

    (tmp_path / ".cursor").mkdir(parents=True)
    (tmp_path / ".cursor" / "mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
    merge_mcp_server(tmp_path, "cursor", "git", {"command": "uvx"}, force=True)
    results = install_plugin("git-mcp", home=tmp_path, installed_only=False)
    by_agent = {r.agent: r.status for r in results}
    assert by_agent["cursor"] == "skipped"
    assert any(status == "installed" for agent, status in by_agent.items() if agent != "cursor")


def test_configure_addon_transport_uses_dispatcher(tmp_path: Path) -> None:
    """configure_plugin(git-mcp) must not KeyError on a missing `entry`."""
    results = configure_plugin("git-mcp", home=tmp_path, dry_run=True)
    assert results
    assert all(r.status == "would_install" for r in results)


def test_load_plugins_includes_ray_manager_mcp() -> None:
    """ray-manager-mcp loads from the registry with the mcp kind schema."""
    plugin = get_plugin("ray-manager-mcp")
    assert plugin is not None
    assert plugin["kind"] == "mcp"
    from astroai_lab.agent.agent_targets import mcp_hosts

    assert set(plugin["agents"]) == set(mcp_hosts())
    assert plugin["install"]["server"] == "ray-manager"
    entry = plugin["install"]["entry"]
    assert entry["command"] == "astroai"
    assert entry["args"] == ["mcp", "serve"]
    # Dynamic URL only — env reference, never a hardcoded manager URL.
    env_ref = entry["env"]["ASTROAI_RAY_JOBS_ADDRESS"]
    assert env_ref == "$ASTROAI_RAY_JOBS_ADDRESS"


def test_configure_ray_manager_mcp_writes_dynamic_env(tmp_path: Path) -> None:
    """configure ray-manager-mcp merges an entry whose env stays a $-ref."""
    results = configure_plugin("ray-manager-mcp", home=tmp_path, agent="cursor")
    assert results
    assert results[0].status == "installed"
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    entry = data["mcpServers"]["ray-manager"]
    assert entry["command"] == "astroai"
    assert entry["env"]["ASTROAI_RAY_JOBS_ADDRESS"] == "$ASTROAI_RAY_JOBS_ADDRESS"
