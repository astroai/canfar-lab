"""Unit tests for `agent remove`.

Covers install.uninstall_tool (binary/config/plugin/stamp removal, dry-run,
--purge), registry.remove_registry_agent dispatch (TOOLS delegation +
method-based removal), and the CLI surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.install import uninstall_tool
from astroai_lab.agent.registry import remove_registry_agent
from astroai_lab.cli.main import app
from astroai_lab.errors import LabError

runner = CliRunner()


def _fake_session_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Point session bin/npm dirs at tmp dirs; disable real npm subprocesses.

    Both ``install.uninstall_tool`` and ``registry._remove_registry_method``
    call the module-level ``install._bin_dir``/``_npm_prefix`` (the latter via
    a lazy ``from astroai_lab.agent.install import _bin_dir, _npm_prefix``),
    so patching the install namespace is sufficient. ``shutil.which`` is
    patched so the best-effort npm-uninstall guard never fires.
    """
    bin_dir = tmp_path / "bin"
    npm_prefix = tmp_path / "npm"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (npm_prefix / "bin").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("astroai_lab.agent.install._bin_dir", lambda: bin_dir)
    monkeypatch.setattr("astroai_lab.agent.install._npm_prefix", lambda: npm_prefix)
    monkeypatch.setattr("astroai_lab.agent.install.shutil.which", lambda _: None)
    # Registry-only removal runs `npm uninstall` via install.run — no-op it.
    monkeypatch.setattr("astroai_lab.agent.install.run", lambda *a, **k: None)
    return bin_dir, npm_prefix


def _make_installed(bin_dir: Path, binary: str, home: Path, config_rel: str) -> None:
    (bin_dir / binary).write_text("#!/bin/sh\n", encoding="utf-8")
    cfg = home / config_rel
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")
    stamp = home / ".astroai" / "lab" / "agent-setup-stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("2026-08-02T00:00:00Z bundle=test mode=install\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# install.uninstall_tool
# ---------------------------------------------------------------------------


def test_uninstall_tool_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    _make_installed(bin_dir, "copilot", home, ".copilot/mcp-config.json")

    results = uninstall_tool("copilot", home=home, dry_run=True)
    assert results
    assert all(r.status == "would_remove" for r in results)
    # Nothing was actually removed.
    assert (bin_dir / "copilot").is_file()
    assert (home / ".copilot" / "mcp-config.json").is_file()
    targets = {r.target for r in results}
    assert "binary:copilot" in targets
    assert "config:.copilot/mcp-config.json" in targets
    assert "state:stamp" in targets


def test_uninstall_tool_removes_binary_config_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    _make_installed(bin_dir, "copilot", home, ".copilot/mcp-config.json")

    results = uninstall_tool("copilot", home=home)
    assert results
    assert all(r.status == "removed" for r in results)
    assert not (bin_dir / "copilot").exists()
    assert not (home / ".copilot" / "mcp-config.json").exists()
    assert not (home / ".astroai" / "lab" / "agent-setup-stamp").exists()


def test_uninstall_tool_purge_removes_home_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    _make_installed(bin_dir, "hermes", home, ".hermes/config.yaml")
    (home / ".hermes" / "skills" / "astroai-ray" / "SKILL.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (home / ".hermes" / "skills" / "astroai-ray" / "SKILL.md").write_text(
        "# astroai-ray\n", encoding="utf-8"
    )

    results = uninstall_tool("hermes", home=home, purge=True)
    assert not (bin_dir / "hermes").exists()
    assert not (home / ".hermes").exists()  # purge wipes the whole dir
    assert any(r.target.startswith("purge:") for r in results)


def test_uninstall_tool_purge_dry_run_leaves_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    _make_installed(bin_dir, "hermes", home, ".hermes/config.yaml")

    results = uninstall_tool("hermes", home=home, purge=True, dry_run=True)
    assert any(r.status == "would_remove" for r in results)
    assert (home / ".hermes" / "config.yaml").is_file()


def test_uninstall_tool_unknown() -> None:
    with pytest.raises(LabError, match="Unknown tool"):
        uninstall_tool("not-a-tool")


def test_uninstall_tool_not_installed_reports_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    assert uninstall_tool("copilot", home=tmp_path / "home") == []


def test_uninstall_tool_removes_cursor_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    payload = bin_dir.parent / "share" / "cursor-agent" / "2026.08.11-e8db854"
    payload.mkdir(parents=True)
    wrapper = payload / "cursor-agent"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    (payload / "node").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "agent").symlink_to(wrapper)

    results = uninstall_tool("cursor", home=home)
    assert not (bin_dir / "agent").exists()
    assert not payload.exists()
    assert any(r.target.startswith("payload:") for r in results)


def test_uninstall_tool_npm_run_is_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`npm uninstall` runs with quiet=True so `--json` stdout stays pure.

    Regression: `agent wipe --yes` (and `agent remove` for npm-installed
    agents) leaked npm's ``up to date in …ms`` lines into stdout, corrupting
    the JSON payload for machine consumers.
    """
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    _make_installed(bin_dir, "openclaw", home, ".openclaw/openclaw.json")
    calls: list[dict] = []
    monkeypatch.setattr("astroai_lab.agent.install.run", lambda *a, **k: calls.append(k))
    monkeypatch.setattr("astroai_lab.agent.install.shutil.which", lambda _: "/usr/bin/npm")

    uninstall_tool("openclaw", home=home)
    assert calls, "npm uninstall should fire for npm-installed tools"
    assert all(c.get("quiet") is True for c in calls)


# ---------------------------------------------------------------------------
# registry.remove_registry_agent
# ---------------------------------------------------------------------------


def test_remove_registry_agent_tools_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    _make_installed(bin_dir, "openclaw", home, ".openclaw/openclaw.json")

    results = remove_registry_agent("openclaw", home=home)
    assert results
    assert not (bin_dir / "openclaw").exists()
    assert not (home / ".openclaw" / "openclaw.json").exists()


def test_remove_registry_agent_unknown() -> None:
    with pytest.raises(LabError, match="Unknown agent"):
        remove_registry_agent("not-an-agent")


def test_remove_registry_agent_method_npm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry-only (non-TOOLS) agent with install.method=npm is removed."""
    from astroai_lab.agent import registry as registry_mod

    bin_dir, npm_prefix = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    (bin_dir / "regonly").write_text("#!/bin/sh\n", encoding="utf-8")
    cfg = home / ".regonly" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")
    (home / ".regonly" / "skills" / "s1" / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
    (home / ".regonly" / "skills" / "s1" / "SKILL.md").write_text("# s1\n", encoding="utf-8")

    agent = {
        "id": "regonly",
        "name": "Reg Only",
        "homepage": "https://x",
        "binary": "regonly",
        "install": {"method": "npm", "source": "regonly@latest"},
        "config": {"path": "~/.regonly/config.json"},
    }
    monkeypatch.setattr(registry_mod, "get_registry_agent", lambda _: agent)
    results = remove_registry_agent("regonly", home=home)
    assert not (bin_dir / "regonly").exists()
    assert not (home / ".regonly" / "config.json").exists()
    assert not (home / ".regonly" / "skills").exists()
    assert any(r["target"] == f"config:{cfg}" for r in results)
    assert any(r["target"].startswith("plugins:") for r in results)


def test_remove_registry_agent_method_npm_run_is_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registry npm-method removal also runs `npm uninstall` with quiet=True."""
    from astroai_lab.agent import registry as registry_mod

    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    (bin_dir / "regonly").write_text("#!/bin/sh\n", encoding="utf-8")
    agent = {
        "id": "regonly",
        "name": "Reg Only",
        "homepage": "https://x",
        "binary": "regonly",
        "install": {"method": "npm", "source": "regonly@latest"},
        "config": {"path": "~/.regonly/config.json"},
    }
    calls: list[dict] = []
    monkeypatch.setattr("astroai_lab.agent.install.run", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(registry_mod, "get_registry_agent", lambda _: agent)
    # registry._remove_registry_method imports stdlib shutil directly.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npm")

    remove_registry_agent("regonly", home=home)
    assert calls, "npm uninstall should fire for npm-method registry agents"
    assert all(c.get("quiet") is True for c in calls)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_agent_remove_dry_run_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir, _ = _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    _make_installed(bin_dir, "copilot", home, ".copilot/mcp-config.json")
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["--json", "agent", "remove", "copilot", "--dry-run"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["tool"] == "copilot"
    assert data["dry_run"] is True
    assert data["actions"]
    assert (bin_dir / "copilot").is_file()  # dry-run: still present


def test_cli_agent_remove_unknown() -> None:
    result = runner.invoke(app, ["--json", "agent", "remove", "not-an-agent"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "Unknown tool" in data["errors"][0] or "Unknown agent" in data["errors"][0]


def test_cli_agent_remove_help_mentions_verb() -> None:
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "remove" in (result.stdout + result.stderr)


def test_cli_agent_remove_nothing_to_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_session_paths(monkeypatch, tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--json", "agent", "remove", "copilot"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["actions"] == []
