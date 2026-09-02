"""Unit tests for plugin install transports (mcp / rule / tool)."""

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

runner = CliRunner()


def _addon(plugin_id: str) -> dict:
    plugin = get_plugin(plugin_id)
    assert plugin is not None
    return plugin_as_addon(plugin)


def test_load_plugins_has_mcp_tool_rule() -> None:
    ids = {p["id"] for p in load_plugins()}
    assert "ponytail-rule" in ids
    assert "git-mcp" in ids
    assert "skore-cli" in ids
    assert "token-efficient" in ids
    assert "ray-manager-mcp" in ids
    assert "ponytail" not in ids
    assert "polars" not in ids
    assert "astroai-ray" not in ids


def test_list_plugins_filter_tag() -> None:
    lean = [p for p in load_plugins() if "lean" in p.get("tags", [])]
    assert any(r["id"] == "ponytail-rule" for r in lean)
    assert any(r["id"] == "token-efficient" for r in lean)


def test_plugin_as_addon_preserves_rule_transport() -> None:
    item = _addon("ponytail-rule")
    assert item["kind"] == "rule"
    assert item["install"]["type"] == "github-rule"
    assert item["install"]["repo"] == "DietrichGebert/ponytail"
    assert item["install"]["path"] == ".cursor/rules/ponytail.mdc"


def test_addon_installed_github_rule(tmp_path: Path) -> None:
    item = _addon("ponytail-rule")
    assert addon_installed(item, tmp_path) is False
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / ".cursor" / "rules" / "ponytail.mdc").write_text("rule\n")
    assert addon_installed(item, tmp_path) is True


def test_addon_installed_token_efficient(tmp_path: Path) -> None:
    item = _addon("token-efficient")
    assert addon_installed(item, tmp_path) is False
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / ".cursor" / "rules" / "token-efficient.mdc").write_text("rule\n")
    assert addon_installed(item, tmp_path) is True


def test_addon_installed_bundled_mcp(tmp_path: Path) -> None:
    item = _addon("mcp-context7")
    assert addon_installed(item, tmp_path) is False


def test_add_addon_unknown() -> None:
    with pytest.raises(LabError, match="Unknown addon"):
        add_addon("not-a-real-addon")


def test_add_addon_bundled_skips(tmp_path: Path) -> None:
    result = add_addon("token-efficient", home=tmp_path)
    assert result.status == "skipped"
    assert "bundled" in result.detail.lower() or "setup" in result.detail.lower()


def test_add_cli_tool_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = add_addon("skore-cli", home=tmp_path, dry_run=True)
    assert result.status == "dry-run"
    assert "skore" in result.detail


def test_add_mcp_snippet_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = add_addon("git-mcp", home=tmp_path, dry_run=True)
    assert result.status == "dry-run"
    assert "mcp:" in result.detail


def test_cli_plugins_list_shows_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "plugins", "list"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "ponytail-rule" in out
    assert "git-mcp" in out
    assert "skore-cli" in out


def test_cli_plugins_install_unknown() -> None:
    result = runner.invoke(app, ["agent", "plugins", "install", "not-a-plugin"])
    assert result.exit_code != 0
