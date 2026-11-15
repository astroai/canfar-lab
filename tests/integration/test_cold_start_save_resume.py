"""Cold-start → save → resume feedback loops (integration, minimal mocks)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from canfar_lab.cli.main import app
from canfar_lab.config.settings import get_settings
from canfar_lab.core.project import list_saves, read_manifest, restore_env, save_env
from canfar_lab.models.manifest import ProjectKind

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def cold_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh session: empty work dir, isolated HOME (cold start)."""
    home = tmp_path / "home"
    work = tmp_path / "srcdir"
    scratch = tmp_path / "scratch"
    arc = tmp_path / "arc"
    for d in (home, work, scratch, arc):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.setenv("CANFAR_LAB_ARC_DIR", str(arc))
    monkeypatch.chdir(work)
    return work


def _pixi(path: Path, name: str = "demo") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pixi.toml").write_text(f'[project]\nname = "{name}"\n')
    (path / "pixi.lock").write_text("lock-v1")
    (path / "analysis.py").write_text('print("ok")\n')


def test_cold_start_init_save_env_resume_loop(cold_env: Path) -> None:
    """init → work → save → new session dir → resume restores project."""
    with pytest.MonkeyPatch.context() as m:
        m.setattr(
            "canfar_lab.core.project.init_project",
            lambda target, use_uv=False: ProjectKind.PIXI,
        )
        m.setattr("canfar_lab.cli.init_clone_env.git_init_and_commit", lambda p: None)
        init = runner.invoke(app, ["init", "mylab", "--no-gh"])
    assert init.exit_code == 0, init.output

    project = cold_env / "mylab"
    _pixi(project, "mylab")

    with pytest.MonkeyPatch.context() as m:
        m.chdir(project)
        save = runner.invoke(app, ["save", "mylab"])
    assert save.exit_code == 0, save.output

    saves = list_saves(Path.home() / ".canfar" / "lab" / "saves")
    assert len(saves) == 1
    save_dir, manifest = saves[0]
    assert manifest.name == "mylab"
    assert manifest.kind == ProjectKind.PIXI

    # Simulate next session: empty work dir, same HOME on /arc
    if project.is_dir():
        shutil.rmtree(project)

    with patch("canfar_lab.core.project.install_project"):
        resumed = runner.invoke(app, ["resume", "mylab"])
    assert resumed.exit_code == 0, resumed.output

    assert (cold_env / "mylab" / "pixi.toml").is_file()
    assert "mylab" in (cold_env / "mylab" / "pixi.toml").read_text()


def test_project_save_resume_roundtrip(cold_env: Path) -> None:
    """Direct save_env/restore_env roundtrip without CLI mocks."""
    project = cold_env / "roundtrip"
    _pixi(project, "roundtrip")
    save_root = Path.home() / ".canfar" / "lab" / "saves"
    saved = save_env("roundtrip", save_root / "roundtrip", project)

    manifest = read_manifest(saved / "manifest.json")
    assert manifest.saved_from == str(project.resolve())

    empty = cold_env / "fresh"
    empty.mkdir()
    with patch("canfar_lab.core.project.install_project"):
        restore_env(saved, empty)

    assert (empty / "pixi.toml").read_text() == (project / "pixi.toml").read_text()
    assert (empty / "pixi.lock").read_text() == "lock-v1"


def test_env_list_after_save(cold_env: Path) -> None:
    project = cold_env / "listed"
    _pixi(project, "listed")
    with pytest.MonkeyPatch.context() as m:
        m.chdir(project)
        save = runner.invoke(app, ["save", "listed"])
    assert save.exit_code == 0, save.output
    out = runner.invoke(app, ["save", "--list", "--json"])
    assert out.exit_code == 0
    rows = json.loads(out.stdout)
    assert any(r["name"] == "listed" for r in rows)


def test_resume_from_explicit_path(cold_env: Path, tmp_path: Path) -> None:
    external = tmp_path / "external-save"
    project = cold_env / "ext"
    _pixi(project, "ext")
    save_env("ext", external, project)

    target = cold_env / "from-external"
    target.mkdir()
    with patch("canfar_lab.core.project.install_project"):
        result = runner.invoke(
            app,
            ["resume", "ext", "--to", str(target), "--from", str(external)],
        )
    assert result.exit_code == 0, result.output
    assert (target / "pixi.toml").is_file()


def test_cold_start_second_save_updates_manifest(cold_env: Path) -> None:
    project = cold_env / "versioned"
    _pixi(project, "versioned")
    save_root = Path.home() / ".canfar" / "lab" / "saves"
    save_env("versioned", save_root / "versioned", project)

    (project / "pixi.lock").write_text("lock-v2")
    save_env("versioned", save_root / "versioned", project)

    manifest = read_manifest(save_root / "versioned" / "manifest.json")
    assert (save_root / "versioned" / "pixi.lock").read_text() == "lock-v2"
    assert manifest.name == "versioned"
