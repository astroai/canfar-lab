"""Agent runtime relocation keeps DBs/session stores off the shared home."""

from __future__ import annotations

from pathlib import Path

import pytest

from astroai_lab.core.home_layout import (
    AGENT_RUNTIME_DIRS,
    relocate_agent_runtime,
)


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    data = tmp_path / "scratch-data"
    home.mkdir()
    return home, data


def test_fresh_home_creates_symlinks(env: Path) -> None:
    home, data = env
    actions = relocate_agent_runtime(home, data)
    assert len(actions) == len(AGENT_RUNTIME_DIRS)
    projects = home / ".claude" / "projects"
    assert projects.is_symlink() and projects.resolve().is_dir()


def test_small_existing_dir_is_relocated(env: Path) -> None:
    home, data = env
    real = home / ".claude" / "projects"
    real.mkdir(parents=True)
    (real / "history.jsonl").write_text("{}", encoding="utf-8")

    actions = relocate_agent_runtime(home, data)

    assert any(a.startswith("relocate:") for a in actions)
    link = home / ".claude" / "projects"
    assert link.is_symlink()
    moved = link.resolve()
    assert (moved / "history.jsonl").is_file()


def test_oversized_dir_is_left_and_reported(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.core import home_layout

    monkeypatch.setattr(home_layout, "MIGRATE_LIMIT_MB", 0)
    home, data = env
    big = home / ".claude" / "projects"
    big.mkdir(parents=True)
    (big / "huge.db").write_bytes(b"x" * 4096)

    actions = relocate_agent_runtime(home, data)

    assert not (home / ".claude" / "projects").is_symlink()
    assert any(a.startswith("skipped:") for a in actions)


def test_idempotent_second_run(env: Path) -> None:
    home, data = env
    relocate_agent_runtime(home, data)
    assert relocate_agent_runtime(home, data) == []


def test_dry_run_touches_nothing(env: Path) -> None:
    home, data = env
    real = home / ".claude" / "projects"
    real.mkdir(parents=True)
    actions = relocate_agent_runtime(home, data, dry_run=True)
    assert actions and not real.is_symlink()
