from __future__ import annotations

from pathlib import Path

import pytest

from canfar_lab import config_dir, saves_dir
from canfar_lab.config.settings import LabSettings, get_settings
from canfar_lab.core.paths import resolve_paths


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def lab_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("WORK", raising=False)
    monkeypatch.delenv("SRCDIR", raising=False)
    monkeypatch.delenv("SCRATCH", raising=False)
    monkeypatch.delenv("CANFAR_LAB_SAVE_DIR", raising=False)
    return home


def test_config_dirs_under_astroai(lab_home: Path) -> None:
    assert config_dir() == lab_home / ".canfar" / "lab"
    assert saves_dir() == lab_home / ".canfar" / "lab" / "saves"


def test_work_dir_from_env(lab_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = lab_home / "work"
    work.mkdir()
    monkeypatch.setenv("WORK", str(work))
    settings = LabSettings()
    assert settings.resolve_work_dir() == work


def test_srcdir_wins_over_work(lab_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = lab_home / "src"
    work = lab_home / "work"
    monkeypatch.setenv("SRCDIR", str(src))
    monkeypatch.setenv("WORK", str(work))
    settings = LabSettings()
    assert settings.resolve_work_dir() == src
    assert src.is_dir()


def test_work_dir_creates_missing_explicit_path(
    lab_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = lab_home / "src"
    monkeypatch.setenv("WORK", str(work))
    settings = LabSettings()
    assert settings.resolve_work_dir() == work
    assert work.is_dir()


def test_yaml_work_dir(lab_home: Path) -> None:
    work = lab_home / "src"
    cfg = lab_home / ".canfar" / "lab"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text(f'work_dir: "{work}"\n')
    get_settings.cache_clear()
    assert get_settings().resolve_work_dir() == work
    assert work.is_dir()


def test_work_dir_falls_back_to_srcdir(lab_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srcdir = lab_home / "srcdir"
    srcdir.mkdir()
    monkeypatch.setenv("WORK", str(srcdir))
    settings = LabSettings()
    assert settings.resolve_work_dir() == srcdir


def test_save_dir_default(lab_home: Path) -> None:
    settings = LabSettings()
    assert settings.resolve_save_dir() == saves_dir()


def test_save_dir_override(lab_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = lab_home / "custom-saves"
    monkeypatch.setenv("CANFAR_LAB_SAVE_DIR", str(custom))
    settings = LabSettings()
    assert settings.resolve_save_dir() == custom


def test_yaml_save_dir_affects_resolve_paths(lab_home: Path) -> None:
    custom = lab_home / "yaml-saves"
    cfg = lab_home / ".canfar" / "lab"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text(f'save_dir: "{custom}"\n')
    get_settings.cache_clear()
    paths = resolve_paths()
    assert paths.save_dir == custom


def test_resolve_paths(lab_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = lab_home / "srcdir"
    work.mkdir()
    monkeypatch.setenv("WORK", str(work))
    paths = resolve_paths()
    assert paths.work_dir == work
    assert paths.config_dir == lab_home / ".canfar" / "lab"


def test_config_dir_is_canfar_lab(lab_home: Path) -> None:
    from canfar_lab import config_dir

    assert config_dir() == lab_home / ".canfar" / "lab"


def _fake_devs(
    scratch: Path,
    *,
    srcdir: Path | None = None,
    srcdir_dev: int = 1,
    scratch_dev: int = 2,
    root_dev: int = 1,
):
    scratch_r = scratch.resolve()
    srcdir_r = srcdir.resolve() if srcdir is not None else None

    def fake_dev(path: Path) -> int | None:
        p = Path(path)
        if p == Path("/"):
            return root_dev
        if p == Path("/srcdir") and srcdir_r is None:
            return srcdir_dev
        try:
            resolved = p.resolve()
        except OSError:
            return root_dev
        if srcdir_r is not None and (resolved == srcdir_r or srcdir_r in resolved.parents):
            return srcdir_dev
        if resolved == scratch_r or scratch_r in resolved.parents:
            return scratch_dev
        return root_dev

    return fake_dev


def test_overlay_srcdir_relocates_to_scratch_src(
    lab_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from canfar_lab.core.session_common import overlay_work_dir

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("canfar_lab.core.session_common._dev", _fake_devs(scratch))
    work = overlay_work_dir(Path("/srcdir"), scratch)
    assert work == scratch / "src"
    assert work.is_dir()


def test_overlay_keeps_bind_mounted_srcdir(
    lab_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from canfar_lab.core.session_common import overlay_work_dir

    scratch = tmp_path / "scratch"
    srcdir = tmp_path / "srcdir"
    scratch.mkdir()
    srcdir.mkdir()
    monkeypatch.setattr(
        "canfar_lab.core.session_common._dev",
        _fake_devs(scratch, srcdir=srcdir, srcdir_dev=3, root_dev=1, scratch_dev=2),
    )
    assert overlay_work_dir(srcdir, scratch, srcdir=srcdir) is None


def test_overlay_honors_explicit_work(
    lab_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from canfar_lab.core.session_common import overlay_work_dir

    scratch = tmp_path / "scratch"
    custom = tmp_path / "custom"
    scratch.mkdir()
    custom.mkdir()
    monkeypatch.setattr("canfar_lab.core.session_common._dev", _fake_devs(scratch))
    assert overlay_work_dir(custom, scratch) is None


def test_overlay_seeds_srcdir_once(
    lab_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from canfar_lab.core.session_common import overlay_work_dir

    scratch = tmp_path / "scratch"
    srcdir = tmp_path / "overlay-src"
    scratch.mkdir()
    srcdir.mkdir()
    (srcdir / "hello.py").write_text("print(1)\n")
    monkeypatch.setattr("canfar_lab.core.session_common._dev", _fake_devs(scratch))
    work = overlay_work_dir(srcdir, scratch, srcdir=srcdir)
    assert work is not None
    assert (work / "hello.py").read_text() == "print(1)\n"
    (srcdir / "hello.py").write_text("stale\n")
    work2 = overlay_work_dir(srcdir, scratch, srcdir=srcdir)
    assert work2 == work
    assert (work / "hello.py").read_text() == "print(1)\n"


def test_overlay_disabled_by_env(
    lab_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from canfar_lab.core.session_common import overlay_work_dir

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CANFAR_LAB_WORK_ON_SCRATCH", "0")
    monkeypatch.setattr("canfar_lab.core.session_common._dev", _fake_devs(scratch))
    assert overlay_work_dir(Path("/srcdir"), scratch) is None


def test_resolve_work_dir_relocates_when_srcdir_is_overlay(
    lab_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("WORK", "/srcdir")
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.setattr("canfar_lab.core.session_common._dev", _fake_devs(scratch))
    settings = LabSettings()
    assert settings.resolve_work_dir() == scratch / "src"
