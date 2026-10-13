from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from typer.testing import CliRunner

from canfar_lab.cli.sync import (
    DEFAULT_SCIENCE_REPOSITORIES,
    detect_github_user,
    inspect_repo,
    resolve_workspace_root,
    sync_app,
)

runner = CliRunner()


def test_default_repositories_list():
    assert "astroai/torchsky" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/torchregress" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/cfhtcast" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/cosmodist" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/uspm" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/torchz" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/torchfits" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/xmatcher" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/zensus" in DEFAULT_SCIENCE_REPOSITORIES
    assert "astroai/weightmask" in DEFAULT_SCIENCE_REPOSITORIES
    assert len(DEFAULT_SCIENCE_REPOSITORIES) == 10


def test_detect_github_user_env(monkeypatch):
    monkeypatch.setenv("GH_USER", "testuser_env")
    assert detect_github_user() == "testuser_env"

    monkeypatch.delenv("GH_USER", raising=False)
    monkeypatch.setenv("GITHUB_USER", "testuser_gh")
    assert detect_github_user() == "testuser_gh"


def test_detect_github_user_gh_cli(monkeypatch):
    monkeypatch.delenv("GH_USER", raising=False)
    monkeypatch.delenv("GITHUB_USER", raising=False)

    mock_run = MagicMock()
    mock_run.return_value = subprocess.CompletedProcess(
        args=["gh", "api", "user", "-q", ".login"], returncode=0, stdout="gh_cli_user\n", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/gh" if cmd == "gh" else None)

    assert detect_github_user() == "gh_cli_user"


def test_resolve_workspace_root(monkeypatch, tmp_path):
    # Explicit argument
    assert resolve_workspace_root(tmp_path) == tmp_path.resolve()

    # WORK env var
    work_dir = tmp_path / "custom_work"
    work_dir.mkdir()
    monkeypatch.setenv("WORK", str(work_dir))
    assert resolve_workspace_root() == work_dir.resolve()
    monkeypatch.delenv("WORK")

    # Parent directory with workspace.toml
    project_dir = tmp_path / "workspace"
    project_dir.mkdir()
    (project_dir / "workspace.toml").touch()
    sub_dir = project_dir / "astroai" / "test_repo"
    sub_dir.mkdir(parents=True)
    monkeypatch.chdir(sub_dir)
    assert resolve_workspace_root() == project_dir.resolve()


def test_inspect_repo_missing(tmp_path):
    st = inspect_repo("astroai/torchsky", tmp_path)
    assert not st.exists
    assert st.name == "astroai/torchsky"


def test_inspect_repo_git(tmp_path):
    repo_dir = tmp_path / "astroai" / "torchsky"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.org"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True
    )
    (repo_dir / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    st = inspect_repo("astroai/torchsky", tmp_path)
    assert st.exists
    assert st.branch == "main"
    assert not st.dirty


def test_cmd_status_empty_workspace(tmp_path):
    result = runner.invoke(sync_app, ["status", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "Repository Synchronization Status" in result.output
    assert "Not Cloned" in result.output


def test_cmd_dirty(tmp_path):
    repo_dir = tmp_path / "astroai" / "torchsky"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.org"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True
    )
    (repo_dir / "README.md").write_text("# Test\n")

    # Untracked file -> dirty
    result = runner.invoke(sync_app, ["dirty", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Unsaved work detected" in result.output
