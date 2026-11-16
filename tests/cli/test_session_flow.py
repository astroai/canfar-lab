from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from canfar_lab.cli.main import app
from canfar_lab.config.settings import get_settings
from canfar_lab.core.git import git_init_and_commit, git_status
from canfar_lab.core.project import save_env, save_rows
from canfar_lab.models.manifest import ProjectKind

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def lab_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.chdir(work)
    return work


def _pixi_project(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pixi.toml").write_text('[project]\nname = "demo"\n')
    (path / "pixi.lock").write_text("lock-content")


def test_git_status_in_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    status = git_status(tmp_path)
    assert status.in_repo is True


def test_git_init_and_commit(tmp_path: Path) -> None:
    repo = tmp_path / "newrepo"
    repo.mkdir()
    (repo / "README.md").write_text("hi")
    with patch("canfar_lab.utils.subprocess.run") as mock_run:
        git_init_and_commit(repo)
    assert mock_run.call_count >= 3


def test_save_env_creates_manifest(lab_env: Path) -> None:
    project = lab_env / "mylab"
    _pixi_project(project)
    save_root = lab_env / "saves"
    save_root.mkdir()
    save_env("mylab", save_root / "mylab", project)
    manifest = save_root / "mylab" / "manifest.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text())
    assert data["kind"] == ProjectKind.PIXI.value
    rows = save_rows(save_root)
    assert len(rows) == 1
    assert rows[0]["name"] == "mylab"


def test_status_command(lab_env: Path) -> None:
    with (
        patch("canfar_lab.cli.status.collect_status_quotas", return_value=[]),
        patch("canfar_lab.cli.status.arc_project_statuses", return_value=(None, [], None, None)),
        patch("canfar_lab.cli.status.home_breakdown", return_value=[]),
        patch("canfar_lab.cli.status.top_cpu_processes", return_value=[]),
    ):
        for argv in (["status"], ["status", "--json"], ["--json", "status"], ["status", "-v"]):
            result = runner.invoke(app, argv)
            assert result.exit_code == 0, result.output


def test_status_verbose_timings_on_stderr(lab_env: Path) -> None:
    with (
        patch("canfar_lab.cli.status.collect_status_quotas", return_value=[]),
        patch("canfar_lab.cli.status.arc_project_statuses", return_value=(None, [], None, None)),
        patch("canfar_lab.cli.status.home_breakdown", return_value=[]),
        patch("canfar_lab.cli.status.top_cpu_processes", return_value=[]),
    ):
        result = runner.invoke(app, ["status", "-v", "--json"])
    assert result.exit_code == 0, result.output
    err = result.stderr or ""
    assert "status:" in err
    assert "home" in err
    data = json.loads(result.stdout)
    assert "quotas" in data


def test_status_canfar_timeout_graceful(lab_env: Path) -> None:
    from canfar_lab.cli.status import CANFAR_CMD_TIMEOUT_SEC
    from canfar_lab.errors import LabError

    calls: list[tuple[list[str], dict[str, object]]] = []

    def _slow_canfar(cmd: list[str], **kwargs: object) -> str:
        calls.append((cmd, kwargs))
        raise LabError(f"Command timed out: {cmd[0]}")

    with (
        patch("canfar_lab.cli.status.shutil.which", return_value="/usr/bin/canfar"),
        patch("canfar_lab.cli.status.run_capture", side_effect=_slow_canfar),
        patch("canfar_lab.cli.status.collect_status_quotas", return_value=[]),
        patch("canfar_lab.cli.status.arc_project_statuses", return_value=(None, [], None, None)),
        patch("canfar_lab.cli.status.home_breakdown", return_value=[]),
        patch("canfar_lab.cli.status.top_cpu_processes", return_value=[]),
    ):
        result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["canfar_auth"].startswith("Unknown")
    assert data["canfar_sessions"] is None
    called = {tuple(cmd) for cmd, _ in calls}
    assert called == {("canfar", "auth", "show"), ("canfar", "ps")}
    assert all(kwargs.get("timeout") == CANFAR_CMD_TIMEOUT_SEC for _, kwargs in calls)


def test_status_json_includes_arc_projects(lab_env: Path) -> None:
    from canfar_lab.core.storage import ArcProjectInfo, QuotaLine

    active = ArcProjectInfo(
        name="mygroup",
        path=Path("/arc/projects/mygroup"),
        quota=QuotaLine(
            label="mygroup",
            path="/arc/projects/mygroup",
            used="1G",
            total="10G",
            free="9G",
            pct=10,
            current=True,
        ),
        is_cwd=True,
    )
    with (
        patch("canfar_lab.cli.status.collect_status_quotas", return_value=[active.quota]),
        patch(
            "canfar_lab.cli.status.arc_project_statuses",
            return_value=(active, [active], None, None),
        ),
        patch("canfar_lab.cli.status.home_breakdown", return_value=[]),
        patch("canfar_lab.cli.status.top_cpu_processes", return_value=[]),
    ):
        result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["arc_project"]["name"] == "mygroup"
    assert data["arc_project"]["access"] == "ro"
    assert data["arc_project"]["quota"]["free"] == "9G"
    assert len(data["arc_projects"]) == 1
    assert data["gms_groups"] is None
    assert data["vault"] is None
    assert "version" in data
    assert "display" in data["version"]


def test_banner_json(lab_env: Path) -> None:
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "work_dir" in data
    assert "version" in data
    assert "display" in data["version"]
