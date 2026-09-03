from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from astroai_lab.cli.main import app


def test_env_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORK", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["env", "export", "--no-ensure"])
    assert result.exit_code == 0
    assert "ASTROAI_LAB_BIN_DIR" in result.stdout
    assert "export WORK=" in result.stdout
    assert "export SRCDIR=" in result.stdout


def test_env_export_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORK", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["env", "export", "--no-ensure", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    assert data["WORK"] == str(tmp_path)
    assert data["SRCDIR"] == str(tmp_path)
    assert data["ASTROAI_LAB_BIN_DIR"]


def test_env_export_json_global_flag(tmp_path: Path, monkeypatch) -> None:
    """The root `--json` flag must also produce JSON output (merge_opts)."""
    monkeypatch.setenv("WORK", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["--json", "env", "export", "--no-ensure"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["WORK"] == str(tmp_path)


def test_env_export_json_no_shell_syntax(tmp_path: Path, monkeypatch) -> None:
    """JSON mode must not emit `export KEY=...` lines."""
    monkeypatch.setenv("WORK", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["env", "export", "--no-ensure", "--json"])
    assert result.exit_code == 0
    assert "export " not in result.stdout


def test_env_export_json_matches_shell_values(tmp_path: Path, monkeypatch) -> None:
    """JSON and shell exports must carry identical resolved values."""
    monkeypatch.setenv("WORK", str(tmp_path))
    runner = CliRunner()
    shell = runner.invoke(app, ["env", "export", "--no-ensure"])
    js = runner.invoke(app, ["env", "export", "--no-ensure", "--json"])
    assert shell.exit_code == 0 and js.exit_code == 0
    data = json.loads(js.stdout)
    # Parse `export KEY=VALUE` lines and compare a few resolved keys.
    shell_env = {}
    for line in shell.stdout.splitlines():
        if line.startswith("export "):
            _, assignment = line.split("export ", 1)
            key, _, val = assignment.partition("=")
            shell_env[key] = val.strip("'\"")
    # Same key set in both modes, and identical values for the resolved paths.
    assert set(data) == set(shell_env)
    for key in (
        "SRCDIR",
        "WORK",
        "ASTROAI_LAB_BIN_DIR",
        "ASTROAI_LAB_RUNTIME_ROOT",
        "XDG_CACHE_HOME",
    ):
        assert data[key] == shell_env[key], f"{key} differs between JSON and shell export"


def test_env_install_shell_removed(tmp_path: Path) -> None:
    """install-shell moved to image builds — not an in-session command anymore."""
    runner = CliRunner()
    result = runner.invoke(app, ["env", "install-shell", str(tmp_path / "shell")])
    assert result.exit_code == 2  # usage error: unknown command


def test_env_export_includes_persisted_ray_address(tmp_path, monkeypatch) -> None:
    """A persisted connect-url shows up as ASTROAI_RAY_JOBS_ADDRESS."""
    monkeypatch.setenv("WORK", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ASTROAI_RAY_JOBS_ADDRESS", raising=False)
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    monkeypatch.setattr(
        "astroai_workload.dashboard._live_manager_connect",
        lambda: (None, False),
    )
    url = tmp_path / ".astroai" / "ray" / "clusters" / "default" / "connect-url"
    url.parent.mkdir(parents=True)
    url.write_text("https://mgr.example/", encoding="utf-8")
    runner = CliRunner()
    shell = runner.invoke(app, ["env", "export", "--no-ensure"])
    assert shell.exit_code == 0
    assert "ASTROAI_RAY_JOBS_ADDRESS=https://mgr.example/dashboard" in shell.stdout
    as_json = runner.invoke(app, ["env", "export", "--no-ensure", "--json"])
    data = json.loads(as_json.stdout)
    assert data["ASTROAI_RAY_JOBS_ADDRESS"] == "https://mgr.example/dashboard"


def test_env_export_discovers_live_manager(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORK", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ASTROAI_RAY_JOBS_ADDRESS", raising=False)
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    monkeypatch.setattr(
        "astroai_workload.dashboard._live_manager_connect",
        lambda: ("https://canfar.net/session/contrib/live", True),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["env", "export", "--no-ensure", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ASTROAI_RAY_JOBS_ADDRESS"] == ("https://canfar.net/session/contrib/live/dashboard")


def test_env_export_without_ray_state_has_no_ray_address(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WORK", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ASTROAI_RAY_JOBS_ADDRESS", raising=False)
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    monkeypatch.setattr(
        "astroai_workload.dashboard._live_manager_connect",
        lambda: (None, False),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["env", "export", "--no-ensure", "--json"])
    assert result.exit_code == 0
    assert "ASTROAI_RAY_JOBS_ADDRESS" not in json.loads(result.stdout)
