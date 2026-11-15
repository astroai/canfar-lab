"""canfar_lab.core.state_home: ~/.astroai -> ~/.canfar/lab migration."""

from __future__ import annotations

import os
from pathlib import Path

from canfar_lab.core.state_home import lab_home, migrate_legacy_state, ray_home


def test_paths(tmp_path: Path) -> None:
    assert lab_home(tmp_path) == tmp_path / ".canfar" / "lab"
    assert ray_home(tmp_path) == tmp_path / ".canfar" / "lab" / "ray"


def test_moves_lab_and_ray_and_leaves_symlinks(tmp_path: Path) -> None:
    old = tmp_path / ".astroai"
    (old / "lab" / "saves" / "x").mkdir(parents=True)
    (old / "lab" / ".env").write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")
    (old / "ray" / "clusters").mkdir(parents=True)
    actions = migrate_legacy_state(tmp_path)
    assert actions
    new = lab_home(tmp_path)
    assert (new / ".env").read_text() == "OPENROUTER_API_KEY=k\n"
    assert (new / "saves" / "x").is_dir()
    assert (new / "ray" / "clusters").is_dir()
    for name, target in (("lab", new), ("ray", new / "ray")):
        link = old / name
        assert link.is_symlink() and link.resolve() == target.resolve()
    # Old paths still work for an older image sharing this home.
    assert (old / "lab" / ".env").is_file()
    assert os.stat(new).st_mode & 0o777 == 0o700
    assert migrate_legacy_state(tmp_path) == []


def _write(path: Path, text: str, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_ancient_new_dir_loses_to_current_legacy(tmp_path: Path) -> None:
    old, new = tmp_path / ".astroai" / "lab", lab_home(tmp_path)
    _write(new / "config.yaml", "ancient\n", 1_000_000)
    _write(old / "config.yaml", "current\n", 2_000_000)
    _write(old / ".env", "K=v\n", 2_000_000)
    actions = migrate_legacy_state(tmp_path)
    assert (new / "config.yaml").read_text() == "current\n"
    assert (new / ".env").read_text() == "K=v\n"
    asides = list(new.parent.glob("lab.pre-migrate-*/lab/config.yaml"))
    assert len(asides) == 1 and asides[0].read_text() == "ancient\n"
    assert old.is_symlink()
    assert any(a.startswith("newer:") for a in actions)


def test_fresh_legacy_bootlog_does_not_hide_new_state(tmp_path: Path) -> None:
    """An older image wrote a boot log to ~/.astroai/lab after the move."""
    old, new = tmp_path / ".astroai" / "lab", lab_home(tmp_path)
    _write(new / ".env", "K=v\n", 2_000_000)
    _write(new / "workspace.json", "{}\n", 2_000_000)
    _write(new / "boot.log", "new\n", 2_000_000)
    _write(old / "boot.log", "older\n", 1_000_000)
    migrate_legacy_state(tmp_path)
    assert (new / ".env").read_text() == "K=v\n"
    assert (new / "workspace.json").is_file()
    assert (new / "boot.log").read_text() == "new\n"
    assert old.is_symlink()


def test_nothing_to_do(tmp_path: Path) -> None:
    assert migrate_legacy_state(tmp_path) == []
    assert not (tmp_path / ".canfar").exists()


def test_new_dir_created_concurrently_never_nests(tmp_path: Path) -> None:
    """An image boot created ~/.canfar/lab (empty or not) without the lock."""
    for content in (False, True):
        home = tmp_path / ("full" if content else "empty")
        old = home / ".astroai" / "lab"
        _write(old / ".env", "K=v\n", 2_000_000)
        new = lab_home(home)
        new.mkdir(parents=True)
        if content:
            _write(new / "boot.log", "x\n", 3_000_000)
        migrate_legacy_state(home)
        assert (new / ".env").read_text() == "K=v\n"
        assert not (new / "lab").exists()
        assert old.is_symlink()
