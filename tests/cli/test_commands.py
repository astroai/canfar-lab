from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from astroai_lab.cli.main import app
from astroai_lab.config.settings import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def lab_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    work = home / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WORK", str(work))
    return home


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "astroai" in result.stdout
    assert "astroai-lab" not in result.stdout
    from astroai_lab.version import PACKAGE_VERSION

    assert PACKAGE_VERSION in result.stdout


def test_help_command() -> None:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "save" in result.output


def test_help_includes_cluster_jobs_run() -> None:
    from typer.main import get_command

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("run", "cluster", "jobs"):
        assert name in result.output
    root = get_command(app)
    for name in ("mcp", "autoscaler"):
        assert name in root.commands
        assert root.commands[name].hidden
    assert "dashboard" not in root.commands  # lives under `cluster dashboard`


def test_cluster_help_start_status_stop() -> None:
    result = runner.invoke(app, ["cluster", "--help"])
    assert result.exit_code == 0
    for name in ("start", "status", "stop", "dashboard"):
        assert name in result.output
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "ensure" not in out
    assert "scale" not in out
    assert "--autoscaling" not in out


def test_help_single_command() -> None:
    result = runner.invoke(app, ["help", "--command", "agent"])
    assert result.exit_code == 0
    assert "Usage: astroai agent" in result.output
    # Scoped: agent group help shows agent subcommands, not save/resume ones.
    assert "plugins" in result.output
    assert "Usage: astroai save" not in result.output


def test_help_single_nested_command() -> None:
    result = runner.invoke(app, ["help", "-c", "agent list"])
    assert result.exit_code == 0
    assert "Usage: astroai agent list" in result.output


def test_help_unknown_command() -> None:
    result = runner.invoke(app, ["help", "-c", "nope"])
    assert result.exit_code == 1
    assert "nope" in result.output


def test_guide_alias_removed() -> None:
    """The `guide` alias was removed in the 0.3 simplification (use `help`)."""
    result = runner.invoke(app, ["guide"])
    assert result.exit_code != 0
    assert "No such command" in (result.stdout + result.stderr)


def test_help_json_inventory() -> None:
    result = runner.invoke(app, ["help", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "commands" in data
    paths = {c["path"] for c in data["commands"]}
    assert "status" in paths
    assert "clean" in paths
    assert "agent list" in paths
    assert "save" in paths
    assert "resume" in paths
    assert "saves" not in paths
    assert "guide" not in paths


def test_help_json_single_command() -> None:
    result = runner.invoke(app, ["help", "-c", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["path"] == "status"
    assert "help" in data
    assert "options" in data
    assert any("--json" in o["opts"] for o in data["options"])
    assert any("--verbose" in o["opts"] for o in data["options"])
    assert any("--all" in o["opts"] for o in data["options"])


def test_help_json_unknown_command() -> None:
    result = runner.invoke(app, ["help", "-c", "nope", "--json"])
    assert result.exit_code == 1
    assert "nope" in result.output


def test_default_banner(lab_home: Path) -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "astroai" in result.output.lower() or "work" in result.output.lower()


@patch("astroai_lab.cli.banner.cwd_arc_project")
def test_banner_with_active_team(mock_cwd, lab_home: Path) -> None:
    active = MagicMock()
    active.name = "demo"
    active.path = Path("/arc/projects/demo")
    active.access = "read-write"
    active.quota.free = "10GB"
    active.quota.total = "100GB"
    active.quota.pct = 10

    mock_cwd.return_value = active
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "team:    /arc/projects/demo" in result.output
    assert "10GB free" in result.output


def test_config_path(lab_home: Path) -> None:
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert str(lab_home / ".astroai" / "lab" / "config.yaml") in result.stdout


def test_config_show_json(lab_home: Path) -> None:
    result = runner.invoke(app, ["--json", "config", "show"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["default_pm"] == "pixi"


def test_config_show_json_local(lab_home: Path) -> None:
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["default_pm"] == "pixi"


def test_config_root(lab_home: Path) -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "config show" in result.output
    assert "config --help" in result.output


def test_config_root_json(lab_home: Path) -> None:
    result = runner.invoke(app, ["--json", "config"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["help"] == "astroai config --help"
    assert "show" in data["try"]


def test_save_list_empty_json(lab_home: Path) -> None:
    result = runner.invoke(app, ["save", "--list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_saves_command_removed() -> None:
    result = runner.invoke(app, ["saves"])
    assert result.exit_code != 0
    assert "No such command" in (result.stdout + result.stderr)


def test_save_requires_project(lab_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = lab_home / "work"
    monkeypatch.chdir(work)
    result = runner.invoke(app, ["save", "mylab"])
    assert result.exit_code == 1
    assert "Error" in result.output or "error" in result.output.lower()


def test_init_creates_pixi_project(lab_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = lab_home / "work"
    monkeypatch.chdir(work)
    result = runner.invoke(
        app,
        ["init", "demo", "--no-git", "--no-gh"],
        catch_exceptions=False,
    )
    if result.exit_code != 0:
        pytest.skip("pixi/uv not available in test environment")
    target = work / "demo"
    assert target.is_dir()
    assert (target / "pixi.toml").is_file() or (target / "pyproject.toml").is_file()


def test_clean_dry_run_keeps_caches(lab_home: Path) -> None:
    pip = lab_home / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "wheel").write_text("x", encoding="utf-8")
    mystery = lab_home / ".cache" / "some-new-tool"
    mystery.mkdir()
    (mystery / "blob").write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["--json", "clean", "--dry-run"])
    assert result.exit_code == 0
    assert pip.is_dir()
    data = json.loads(result.stdout)
    removed = [a for a in data["actions"] if a["status"] == "would_remove"]
    assert any(a["path"].endswith(".cache/pip") for a in removed)
    assert any(a["path"].endswith(".cache/some-new-tool") for a in removed)


def test_clean_yes_deletes_caches_keeps_saves(lab_home: Path) -> None:
    from tests.helpers import write_manifest

    pip = lab_home / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "wheel").write_text("x", encoding="utf-8")
    save = lab_home / ".astroai" / "lab" / "saves" / "mylab"
    write_manifest(save, "mylab")
    cfg = lab_home / ".astroai" / "lab" / "config.yaml"
    cfg.write_text("default_pm: pixi\n", encoding="utf-8")
    result = runner.invoke(app, ["clean", "--yes"])
    assert result.exit_code == 0
    assert not pip.exists()
    assert save.is_dir()
    assert cfg.is_file()


def test_clean_yes_saves_and_config(lab_home: Path) -> None:
    from tests.helpers import write_manifest

    save = lab_home / ".astroai" / "lab" / "saves" / "mylab"
    write_manifest(save, "mylab")
    cfg = lab_home / ".astroai" / "lab" / "config.yaml"
    cfg.write_text("default_pm: pixi\n", encoding="utf-8")
    result = runner.invoke(app, ["clean", "--yes", "--saves", "--config"])
    assert result.exit_code == 0
    assert not save.exists()
    assert not cfg.exists()
