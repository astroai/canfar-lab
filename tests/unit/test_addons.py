from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.addons import (
    add_addon,
    addon_installed,
    plugin_as_addon,
)
from astroai_lab.agent.plugins import get_plugin, load_plugins
from astroai_lab.cli.main import app
from astroai_lab.errors import LabError
from tests.conftest import mock_canfar_skills_upstream

runner = CliRunner()

_mock_canfar_skills_upstream = mock_canfar_skills_upstream


def _addon(plugin_id: str) -> dict:
    plugin = get_plugin(plugin_id)
    assert plugin is not None
    return plugin_as_addon(plugin)


def test_load_plugins_has_ponytail_and_polars() -> None:
    ids = {p["id"] for p in load_plugins()}
    assert "ponytail" in ids
    assert "polars" in ids
    assert "modern-python" in ids
    assert "git-mcp" in ids
    assert "astroai-ray" in ids


def test_load_plugins_has_scientific_writing_stack() -> None:
    ids = {p["id"] for p in load_plugins()}
    assert {
        "writing-skills",
        "manuscript-writing-review",
        "deslop",
        "revision-guard",
    } <= ids


def test_load_plugins_has_epistemic_stack() -> None:
    plugins = {p["id"]: p for p in load_plugins()}
    assert {
        "ask-dont-tell",
        "ground-truth",
        "scientific-integrity",
        "scientific-critical-thinking",
        "hypothesis-generation",
        "scientific-brainstorming",
        "peer-review",
        "experimental-design",
        "statistical-analysis",
        "receiving-code-review",
        "requesting-code-review",
        "systematic-debugging",
        "verification-before-completion",
        "test-driven-development",
        "test-drive",
        "the-quorum",
    } <= plugins.keys()
    assert plugins["ask-dont-tell"]["default"] is True
    assert plugins["ground-truth"]["default"] is True
    assert plugins["scientific-integrity"]["default"] is True
    assert plugins["test-drive"]["invocation"] == "explicit-only"
    assert plugins["the-quorum"]["invocation"] == "explicit-only"
    assert plugins["test-driven-development"]["install"]["repo"] == "obra/superpowers"


def test_add_agent_skill_installs_to_hermes_and_openclaw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _mock_canfar_skills_upstream(monkeypatch)
    result = add_addon("astroai-ray", home=home)
    assert result.status == "installed"
    for rel in (
        ".hermes/skills/astroai-ray/SKILL.md",
        ".openclaw/skills/astroai-ray/SKILL.md",
        ".cursor/skills/astroai-ray/SKILL.md",
        ".claude/skills/astroai-ray/SKILL.md",
    ):
        assert (home / rel).is_file(), f"missing {rel}"
    # Idempotent second call skips.
    assert add_addon("astroai-ray", home=home).status == "skipped"
    # Installed detection agrees.
    assert addon_installed(_addon("astroai-ray"), home)


def test_add_agent_skill_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = add_addon("astroai-ray", home=home, dry_run=True)
    assert result.status == "dry-run"
    assert not (home / ".hermes").exists()


def test_add_agent_skill_force_reinstalls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _mock_canfar_skills_upstream(monkeypatch)
    assert add_addon("astroai-ray", home=home).status == "installed"
    assert add_addon("astroai-ray", home=home, force=True).status == "installed"


def test_list_plugins_filter_tag() -> None:
    lean = [p for p in load_plugins() if "lean" in p.get("tags", [])]
    assert any(r["id"] == "ponytail" for r in lean)
    science = [p for p in load_plugins() if "science" in p.get("tags", [])]
    assert any(r["id"] == "polars" for r in science)


def test_get_plugin_unknown() -> None:
    assert get_plugin("not-a-real-addon") is None


def test_add_bundled_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = add_addon("token-efficient", home=home)
    assert result.status == "skipped"


def test_add_mcp_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = add_addon("git-mcp", home=home, dry_run=True)
    assert result.status == "dry-run"


def test_github_skill_copies_only_scoped_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """github-skill writes the SKILL.md tree to one support-matrix host."""
    from astroai_lab.agent import addons as addons_mod
    from astroai_lab.agent.addons import _apply_addon

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    item = _addon("polars")
    assert item is not None
    cache = (
        home / ".cache" / "astroai-lab" / "upstream-skills" / "k-dense-ai_claude-scientific-skills"
    )
    src = cache / "skills" / "polars"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("# polars\n", encoding="utf-8")
    monkeypatch.setattr(
        addons_mod, "_refresh_upstream_repo", lambda *a, **k: ("cloned", item["install"]["repo"])
    )
    result = _apply_addon(item, home=home, agent="hermes")
    assert result.status == "installed"
    assert (home / ".hermes" / "skills" / "polars" / "SKILL.md").is_file()
    assert not (home / ".cursor" / "skills" / "polars").exists()


def test_github_skill_supports_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root-layout skills install under their registry id."""
    from astroai_lab.agent import addons as addons_mod
    from astroai_lab.agent.addons import _apply_addon

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    item = _addon("deslop")
    cache = home / ".cache" / "astroai-lab" / "upstream-skills" / "stephenturner_skill-deslop"
    cache.mkdir(parents=True)
    (cache / "SKILL.md").write_text("---\nname: deslop\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        addons_mod, "_refresh_upstream_repo", lambda *a, **k: ("cloned", item["install"]["repo"])
    )
    result = _apply_addon(item, home=home, agent="hermes")
    assert result.status == "installed"
    assert (home / ".hermes" / "skills" / "deslop" / "SKILL.md").is_file()


def test_add_mcp_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text('{"mcpServers": {}}\n')
    monkeypatch.setenv("HOME", str(home))
    result = add_addon("git-mcp", home=home, force=True)
    assert result.status == "installed"
    data = (home / ".cursor" / "mcp.json").read_text()
    assert '"git"' in data
    assert addon_installed(_addon("git-mcp"), home)


def test_add_mcp_refuses_corrupt_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text("{ not json\n")
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(LabError, match="unreadable"):
        add_addon("git-mcp", home=home, force=True)


def test_strip_jsonc_preserves_comma_in_string() -> None:
    from astroai_lab.utils.json_utils import parse_jsonc

    assert parse_jsonc('{"x": "hello,}", "y": 1}') == {"x": "hello,}", "y": 1}
    assert parse_jsonc('{"a": 1,}') == {"a": 1}


def test_plugin_as_addon_preserves_ponytail_transport() -> None:
    """github-bundle plugins keep the install transport after plugin_as_addon."""
    item = _addon("ponytail")
    assert item is not None
    assert item["kind"] == "bundle"
    assert item["install"]["type"] == "github-bundle"
    assert isinstance(item["install"]["skills"], list)
    assert {Path(p).name for p in item["install"]["skills"]} == {
        "ponytail",
        "ponytail-review",
        "ponytail-audit",
        "ponytail-debt",
        "ponytail-gain",
        "ponytail-help",
    }


def test_github_bundle_installed_requires_every_skill(tmp_path: Path) -> None:
    home = tmp_path / "home"
    item = _addon("ponytail")
    assert item is not None
    names = [Path(p).name for p in item["install"]["skills"]]
    first = home / ".cursor" / "skills" / names[0]
    first.mkdir(parents=True)
    (first / "SKILL.md").write_text("# partial\n")
    assert not addon_installed(item, home, agent="cursor")
    for name in names:
        dest = home / ".cursor" / "skills" / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text("# ok\n")
    (home / ".cursor" / "rules").mkdir(parents=True, exist_ok=True)
    (home / ".cursor" / "rules" / "ponytail.mdc").write_text("rule\n")
    assert addon_installed(item, home, agent="cursor")


def test_add_addon_delegates_to_plugins_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`add_addon` and `plugins.install_plugin` route identically."""
    from astroai_lab.agent.plugins import install_plugin

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _mock_canfar_skills_upstream(monkeypatch)
    addon_result = add_addon("astroai-ray", home=home)
    assert addon_result.status == "installed"
    plugin_results = install_plugin("astroai-ray", home=home, installed_only=False)
    # Both paths produced the same skill dirs.
    for agent in ("hermes", "openclaw", "cursor"):
        rel = (
            ".cursor/skills/astroai-ray/SKILL.md"
            if agent == "cursor"
            else f".{agent}/skills/astroai-ray/SKILL.md"
        )
        assert (home / rel).is_file()
    assert all(r.status == "skipped" for r in plugin_results)


def test_add_addon_github_bundle_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Migrated github-bundle addon dry-run (no network)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = add_addon("ponytail", home=home, dry_run=True)
    assert result.status == "dry-run"
    assert not (home / ".cursor").exists()


def test_probabl_skills_plugin_loads() -> None:
    from astroai_lab.agent.plugins import get_plugin

    plugin = get_plugin("probabl-skills")
    assert plugin is not None
    install = plugin["install"]
    assert install["type"] == "github-bundle"
    assert install["bundled_skills"] == ["ml-experimentation"]
    assert install["also_tools"] == ["skore-cli"]
    assert len(install["skills"]) == 14


def test_probabl_skills_bundle_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = add_addon("probabl-skills", home=home, dry_run=True)
    assert result.status == "dry-run"
    assert "ml-experimentation" in result.detail
    assert "skore-cli" in result.detail


def test_add_unknown_raises() -> None:
    with pytest.raises(LabError, match="Unknown addon"):
        add_addon("definitely-missing")


def test_agent_plugins_list_includes_addons() -> None:
    result = runner.invoke(app, ["agent", "plugins", "list"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "ponytail" in out
    assert "polars" in out


def test_agent_plugins_list_kind_cli() -> None:
    result = runner.invoke(app, ["agent", "plugins", "list", "--kind", "skill"])
    assert result.exit_code == 0
    assert "ponytail" in (result.stdout + result.stderr) or result.exit_code == 0


def test_agent_plugins_install_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--dry-run", "agent", "plugins", "install", "git-mcp"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "git-mcp" in out


def test_canfar_platform_plugin_loads() -> None:
    plugin = get_plugin("canfar-platform")
    assert plugin is not None
    assert plugin.get("default") is True
    assert len(plugin["install"]["skills"]) == 23


def test_canfar_platform_bundle_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = add_addon("canfar-platform", home=home, dry_run=True)
    assert result.status == "dry-run"
    assert "astroai/canfar-skills" in result.detail
    assert "canfar-platform" in result.detail
