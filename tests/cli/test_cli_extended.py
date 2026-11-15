from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from canfar_lab.cli.main import app
from canfar_lab.config.settings import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def lab_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    work = tmp_path / "work"
    scratch = tmp_path / "scratch"
    home.mkdir()
    work.mkdir()
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.chdir(work)
    return work


def _pixi(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pixi.toml").write_text('[project]\nname="p"\n')
    (path / "pixi.lock").write_text("lock")


def test_clone_requires_gh(lab_env: Path) -> None:
    with patch("canfar_lab.cli.init_clone_env.shutil.which", return_value=None):
        result = runner.invoke(app, ["clone", "org/repo"])
    assert result.exit_code == 1
    assert "gh" in result.output.lower()


def test_clone_from_without_env(lab_env: Path) -> None:
    with patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"):
        result = runner.invoke(app, ["clone", "--from", "/tmp/save", "org/repo"])
    assert result.exit_code == 1


def test_clone_update_dry_run(lab_env: Path) -> None:
    dest = lab_env / "repo"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    with patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"):
        result = runner.invoke(
            app,
            ["--dry-run", "clone", "org/repo", "--update", "--to", str(dest)],
        )
    assert result.exit_code == 0, result.output
    assert "would update" in result.output


def test_clone_exists_hints_update(lab_env: Path) -> None:
    dest = lab_env / "repo"
    dest.mkdir(parents=True)
    with (
        patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"),
        patch("canfar_lab.cli.init_clone_env.resolve_clone_spec", return_value="org/repo"),
    ):
        result = runner.invoke(app, ["clone", "org/repo"])
    assert result.exit_code == 1
    assert "--update" in result.output


def test_clone_force_requires_update(lab_env: Path) -> None:
    with patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"):
        result = runner.invoke(app, ["clone", "org/repo", "--force"])
    assert result.exit_code == 1
    assert "--force requires --update" in result.output


def test_clone_success(lab_env: Path) -> None:
    with (
        patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"),
        patch("canfar_lab.utils.subprocess.run") as mock_run,
        patch("canfar_lab.core.project.detect_project", return_value=None),
        patch("canfar_lab.cli.init_clone_env._finalize_clone", return_value="abc1234"),
    ):
        result = runner.invoke(app, ["clone", "org/repo"])
    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    assert "abc1234" in result.output


def test_clone_two_short_names_are_both_repos(lab_env: Path) -> None:
    """Bare names are repos, not a hidden --to (weightmask into cfhtcast/)."""

    def fake_spec(spec: str) -> str:
        return f"user/{spec}"

    with (
        patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"),
        patch("canfar_lab.cli.init_clone_env.resolve_clone_spec", side_effect=fake_spec),
        patch("canfar_lab.utils.subprocess.run") as mock_run,
    ):
        result = runner.invoke(app, ["--dry-run", "clone", "weightmask", "cfhtcast"])
    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert result.output.count("would clone") == 2
    assert "user/weightmask" in result.output
    assert "user/cfhtcast" in result.output


def test_clone_multiple_dry_run(lab_env: Path) -> None:
    with (
        patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"),
        patch("canfar_lab.utils.subprocess.run") as mock_run,
    ):
        result = runner.invoke(app, ["--dry-run", "clone", "org/alpha", "org/beta"])
    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "alpha" in result.output
    assert "beta" in result.output


def test_clone_to_rejects_multiple_repos(lab_env: Path) -> None:
    with patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"):
        result = runner.invoke(app, ["clone", "org/a", "org/b", "--to", "/tmp/x"])
    assert result.exit_code == 1
    assert "--to" in result.output


def test_clone_dir_dry_run(lab_env: Path, tmp_path: Path) -> None:
    parent = tmp_path / "home" / "src"
    with patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"):
        result = runner.invoke(
            app, ["--dry-run", "clone", "org/alpha", "org/beta", "--dir", str(parent)]
        )
    assert result.exit_code == 0, result.output
    # Rich may wrap long paths; compare without newlines.
    flat = result.output.replace("\n", "")
    assert str(parent.resolve() / "alpha") in flat
    assert str(parent.resolve() / "beta") in flat
    assert parent.is_dir()


def test_clone_dir_rejects_to(lab_env: Path, tmp_path: Path) -> None:
    with patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"):
        result = runner.invoke(
            app,
            ["clone", "org/repo", "--dir", str(tmp_path / "src"), "--to", str(tmp_path / "x")],
        )
    assert result.exit_code == 1
    assert "--dir" in result.output and "--to" in result.output


def test_init_dir(lab_env: Path, tmp_path: Path) -> None:
    from canfar_lab.models.manifest import ProjectKind

    parent = tmp_path / "home" / "src"
    with (
        patch("canfar_lab.core.project.init_project", return_value=ProjectKind.PIXI) as init,
        patch("canfar_lab.cli.init_clone_env.git_init_and_commit"),
    ):
        result = runner.invoke(app, ["init", "newlab", "--no-gh", "--dir", str(parent)])
    assert result.exit_code == 0, result.output
    init.assert_called_once()
    assert init.call_args.args[0] == parent.resolve() / "newlab"


def test_clone_with_from_env(
    lab_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home2"
    home.mkdir()
    save_dir = home / ".canfar" / "lab" / "saves" / "ml-base"
    save_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    get_settings.cache_clear()
    (save_dir / "pixi.toml").write_text('[project]\nname="b"\n')
    manifest = {
        "name": "ml-base",
        "kind": "pixi",
        "saved_at": "t",
        "saved_from": "/x",
        "user": "u",
        "full": False,
    }
    (save_dir / "manifest.json").write_text(json.dumps(manifest))
    (save_dir / "pixi.lock").write_text("lock")

    from canfar_lab.models.manifest import ProjectKind

    with (
        patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"),
        patch("canfar_lab.utils.subprocess.run"),
        patch("canfar_lab.core.project.warm_cache"),
        patch("canfar_lab.core.project.detect_project", return_value=ProjectKind.PIXI),
        patch("canfar_lab.core.project.bootstrap_lock", return_value=True),
        patch("canfar_lab.core.project.install_project"),
    ):
        result = runner.invoke(app, ["clone", "--from-env", "ml-base", "org/repo"])
    assert result.exit_code == 0, result.output


def test_init_existing_dir(lab_env: Path) -> None:
    existing = lab_env / "taken"
    existing.mkdir()
    (existing / "file").write_text("x")
    result = runner.invoke(app, ["init", "taken"])
    assert result.exit_code == 1


def test_init_success(lab_env: Path) -> None:
    from canfar_lab.models.manifest import ProjectKind

    with (
        patch("canfar_lab.core.project.init_project", return_value=ProjectKind.PIXI),
        patch("canfar_lab.cli.init_clone_env.git_init_and_commit"),
    ):
        result = runner.invoke(app, ["init", "newlab", "--no-gh"])
    assert result.exit_code == 0


def test_resume_success(lab_env: Path, tmp_path: Path) -> None:
    save_dir = tmp_path / "saves" / "mylab"
    save_dir.mkdir(parents=True)
    (save_dir / "pixi.toml").write_text('[project]\nname="p"\n')
    manifest = {
        "name": "mylab",
        "kind": "pixi",
        "saved_at": "t",
        "saved_from": "/x",
        "user": "u",
        "full": False,
    }
    (save_dir / "manifest.json").write_text(json.dumps(manifest))

    with patch("canfar_lab.core.project.restore_env"):
        result = runner.invoke(app, ["resume", "mylab", "--from", str(save_dir)])
    assert result.exit_code == 0


def test_save_and_list_flat(lab_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = lab_env / "demo"
    _pixi(project)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["save", "demo"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["save", "--list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)


def test_save_list_rejects_write_flags(lab_env: Path) -> None:
    result = runner.invoke(app, ["save", "--list", "--full"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["save", "demo", "--list"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["save", "--from", "/tmp"])
    assert result.exit_code == 1


def test_resume_rejects_positional_target(lab_env: Path) -> None:
    result = runner.invoke(app, ["resume", "mylab", str(lab_env / "elsewhere")])
    assert result.exit_code != 0


def test_save_list_from_directory(lab_env: Path, tmp_path: Path) -> None:
    root = tmp_path / "team-saves"
    named = root / "stack"
    named.mkdir(parents=True)
    (named / "pixi.toml").write_text('[project]\nname="p"\n')
    (named / "manifest.json").write_text(
        json.dumps(
            {
                "name": "stack",
                "kind": "pixi",
                "saved_at": "t",
                "saved_from": "/x",
                "user": "u",
                "full": False,
            }
        )
    )
    result = runner.invoke(app, ["save", "--list", "--json", "--from", str(root)])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["name"] == "stack"


def test_resume_flat(lab_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saves = Path.home() / ".canfar" / "lab" / "saves" / "mylab"
    saves.mkdir(parents=True, exist_ok=True)
    (saves / "pixi.toml").write_text('[project]\nname="p"\n')
    manifest = {
        "name": "mylab",
        "kind": "pixi",
        "saved_at": "t",
        "saved_from": "/x",
        "user": "u",
        "full": False,
    }
    (saves / "manifest.json").write_text(json.dumps(manifest))
    get_settings.cache_clear()
    with patch("canfar_lab.core.project.restore_env"):
        result = runner.invoke(app, ["resume", "mylab"])
    assert result.exit_code == 0


def test_kernel_register_cli(lab_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = lab_env / "nb"
    project.mkdir()
    (project / "pixi.toml").write_text('[project]\nname="p"\n')
    py = project / ".pixi" / "envs" / "default" / "bin"
    py.mkdir(parents=True)
    (py / "python").write_text("#!/bin/sh")
    monkeypatch.chdir(project)
    with (
        patch("canfar_lab.core.kernel.shutil.which", return_value="/usr/bin/jupyter"),
        patch("canfar_lab.core.kernel.run"),
    ):
        result = runner.invoke(app, ["kernel", "register"])
    assert result.exit_code == 0


def test_kernel_list_json(lab_env: Path) -> None:
    with patch("canfar_lab.cli.kernel.list_kernels", return_value=[{"name": "k", "path": "/p"}]):
        for argv in (["--json", "kernel", "list"], ["kernel", "list", "--json"]):
            result = runner.invoke(app, argv)
            assert result.exit_code == 0, result.output
            data = json.loads(result.stdout)
            assert data[0]["name"] == "k"


def test_config_show_human(lab_env: Path) -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "default_pm" in result.output


def test_agent_list_human(lab_env: Path) -> None:
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    out = result.output.lower()
    assert "bin" in out and "cfg" in out


def test_agent_list_json(lab_env: Path) -> None:
    result = runner.invoke(app, ["--json", "agent", "list"])
    # Fresh / incomplete setup → ok:false → exit 1; JSON still emitted.
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    assert "agents" in data
    assert isinstance(data["agents"], list)
    assert len(data["agents"]) > 0
    assert "agent" in data["agents"][0]
    assert "ok" in data
    assert "issues" in data


def test_resume_name_yes_accepted(lab_env: Path) -> None:
    saves = Path.home() / ".canfar" / "lab" / "saves" / "mylab"
    saves.mkdir(parents=True, exist_ok=True)
    (saves / "pixi.toml").write_text('[project]\nname="p"\n')
    (saves / "manifest.json").write_text(
        json.dumps(
            {
                "name": "mylab",
                "kind": "pixi",
                "saved_at": "t",
                "saved_from": "/x",
                "user": "u",
                "full": False,
            }
        )
    )
    get_settings.cache_clear()
    dest = lab_env / "mylab"
    dest.mkdir()
    (dest / "stale.txt").write_text("x")
    with patch("canfar_lab.core.project.restore_env"):
        result = runner.invoke(app, ["resume", "mylab", "--yes"])
    assert result.exit_code == 0, result.output
    assert not dest.exists()  # --yes rmtree before restore; mock does not recreate


def test_resume_yes_trailing_flag(lab_env: Path, tmp_path: Path) -> None:
    save_dir = tmp_path / "saves" / "mylab"
    save_dir.mkdir(parents=True)
    (save_dir / "pixi.toml").write_text('[project]\nname="p"\n')
    (save_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "mylab",
                "kind": "pixi",
                "saved_at": "t",
                "saved_from": "/x",
                "user": "u",
                "full": False,
            }
        )
    )
    dest = lab_env / "mylab"
    dest.mkdir()
    leftover = dest / "stale.txt"
    leftover.write_text("keep-me-not")
    with patch("canfar_lab.core.project.install_project"):
        result = runner.invoke(app, ["resume", "mylab", "--from", str(save_dir), "--yes"])
    assert result.exit_code == 0, result.output
    assert not leftover.exists()
    assert (dest / "pixi.toml").is_file()


def test_resume_nonempty_without_yes_exits(lab_env: Path, tmp_path: Path) -> None:
    dest = lab_env / "mylab"
    dest.mkdir()
    leftover = dest / "stale.txt"
    leftover.write_text("keep")
    result = runner.invoke(app, ["resume", "mylab", "--from", str(tmp_path / "unused")])
    assert result.exit_code == 1
    assert leftover.is_file()


def test_resume_yes_replaces_not_merges(lab_env: Path, tmp_path: Path) -> None:
    save_dir = tmp_path / "saves" / "mylab"
    save_dir.mkdir(parents=True)
    (save_dir / "pixi.toml").write_text('[project]\nname="p"\n')
    (save_dir / "pixi.lock").write_text("lock")
    (save_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "mylab",
                "kind": "pixi",
                "saved_at": "t",
                "saved_from": "/x",
                "user": "u",
                "full": False,
            }
        )
    )
    dest = lab_env / "mylab"
    dest.mkdir()
    (dest / "old-pixi.toml").write_text("stale-kind")
    (dest / "keep-me-not").write_text("x")
    with patch("canfar_lab.core.project.install_project"):
        result = runner.invoke(app, ["resume", "mylab", "--from", str(save_dir), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (dest / "old-pixi.toml").exists()
    assert not (dest / "keep-me-not").exists()
    assert (dest / "pixi.toml").is_file()


def test_dry_run_save_writes_nothing(lab_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = lab_env / "demo"
    _pixi(project)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--dry-run", "save", "demo"])
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output.lower()
    saves = Path.home() / ".canfar" / "lab" / "saves" / "demo"
    assert not saves.exists()


def test_resume_dry_run_does_not_rmtree(lab_env: Path, tmp_path: Path) -> None:
    save_dir = tmp_path / "saves" / "mylab"
    save_dir.mkdir(parents=True)
    (save_dir / "pixi.toml").write_text('[project]\nname="p"\n')
    (save_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "mylab",
                "kind": "pixi",
                "saved_at": "t",
                "saved_from": "/x",
                "user": "u",
                "full": False,
            }
        )
    )
    dest = lab_env / "mylab"
    dest.mkdir()
    leftover = dest / "stale.txt"
    leftover.write_text("keep")
    result = runner.invoke(app, ["--dry-run", "resume", "mylab", "--from", str(save_dir)])
    assert result.exit_code == 0, result.output
    assert leftover.is_file()


def test_clone_dry_run_writes_nothing(lab_env: Path) -> None:
    with (
        patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"),
        patch("canfar_lab.utils.subprocess.run") as mock_run,
    ):
        result = runner.invoke(app, ["--dry-run", "clone", "org/repo"])
    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert not (lab_env / "repo").exists()


def test_resolve_clone_spec_passthrough() -> None:
    from canfar_lab.cli.init_clone_env import resolve_clone_spec

    assert resolve_clone_spec("owner/repo") == "owner/repo"


def test_resolve_clone_spec_user_first() -> None:
    from canfar_lab.cli.init_clone_env import resolve_clone_spec

    with (
        patch("canfar_lab.cli.init_clone_env._gh_login", return_value="sfabbro"),
        patch(
            "canfar_lab.cli.init_clone_env._gh_repo_exists",
            side_effect=lambda spec: spec == "sfabbro/foo",
        ),
    ):
        assert resolve_clone_spec("foo") == "sfabbro/foo"


def test_resolve_clone_spec_astroai_fallback() -> None:
    from canfar_lab.cli.init_clone_env import resolve_clone_spec

    with (
        patch("canfar_lab.cli.init_clone_env._gh_login", return_value="sfabbro"),
        patch(
            "canfar_lab.cli.init_clone_env._gh_repo_exists",
            side_effect=lambda spec: spec == "astroai/foo",
        ),
    ):
        assert resolve_clone_spec("foo") == "astroai/foo"


def test_resolve_clone_spec_no_login_uses_astroai() -> None:
    from canfar_lab.cli.init_clone_env import resolve_clone_spec

    with (
        patch("canfar_lab.cli.init_clone_env._gh_login", return_value=None),
        patch(
            "canfar_lab.cli.init_clone_env._gh_repo_exists",
            side_effect=lambda spec: spec == "astroai/foo",
        ),
    ):
        assert resolve_clone_spec("foo") == "astroai/foo"


def test_resolve_clone_spec_missing() -> None:
    from canfar_lab.cli.init_clone_env import resolve_clone_spec
    from canfar_lab.errors import LabError

    with (
        patch("canfar_lab.cli.init_clone_env._gh_login", return_value="sfabbro"),
        patch("canfar_lab.cli.init_clone_env._gh_repo_exists", return_value=False),
        pytest.raises(LabError, match="sfabbro/foo"),
    ):
        resolve_clone_spec("foo")


def test_clone_short_name_dry_run(lab_env: Path) -> None:
    with (
        patch("canfar_lab.cli.init_clone_env.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "canfar_lab.cli.init_clone_env.resolve_clone_spec",
            return_value="sfabbro/foo",
        ),
        patch("canfar_lab.utils.subprocess.run") as mock_run,
    ):
        result = runner.invoke(app, ["--dry-run", "clone", "foo"])
    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "sfabbro/foo" in result.output


def test_banner_with_project(lab_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = lab_env / "active"
    _pixi(project)
    monkeypatch.chdir(project)
    with patch("canfar_lab.cli.banner.git_status") as gs:
        gs.return_value = type("S", (), {"in_repo": True, "uncommitted": True})()
        result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "uncommitted" in result.output.lower() or "save" in result.output.lower()
