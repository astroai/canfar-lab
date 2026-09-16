"""Agent runtime relocation keeps DBs/session stores off the shared home."""

from __future__ import annotations

from pathlib import Path

import pytest

from astroai_lab.core.home_layout import (
    AGENT_RUNTIME_DIRS,
    DSH_RUNTIME_DIRS,
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


def test_dangling_symlink_is_relinked(env: Path) -> None:
    home, data = env
    relocate_agent_runtime(home, data)
    link = home / ".claude" / "projects"
    assert link.is_symlink()
    # Simulate prior session scratch gone / wrong target.
    link.unlink()
    link.symlink_to(home / "missing-scratch" / "projects", target_is_directory=True)
    actions = relocate_agent_runtime(home, data)
    assert any(a.startswith("relink:") for a in actions)
    assert link.is_symlink()
    resolved = link.resolve()
    assert resolved.is_dir()
    assert data in resolved.parents or resolved == data


def test_dry_run_touches_nothing(env: Path) -> None:
    home, data = env
    real = home / ".claude" / "projects"
    real.mkdir(parents=True)
    actions = relocate_agent_runtime(home, data, dry_run=True)
    assert actions and not real.is_symlink()


def test_harness_session_state_is_relocated(env: Path) -> None:
    """dsh's session logs and KV stores are append-heavy and unbounded."""
    home, data = env
    relocate_agent_runtime(home, data)
    for rel in DSH_RUNTIME_DIRS:
        link = home / rel
        assert link.is_symlink(), rel
        assert data in link.resolve().parents


def test_harness_config_stays_on_home(env: Path) -> None:
    """Credentials, settings and installed profiles must survive the session."""
    home, data = env
    settings = home / ".dsh" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text("agent-default-model: {}\n", encoding="utf-8")
    profile = home / ".dsh" / "profiles" / "astroai"
    profile.mkdir(parents=True)

    relocated = {home / rel for rel in AGENT_RUNTIME_DIRS}

    assert settings not in relocated
    assert profile not in relocated
    assert settings.is_file() and not settings.is_symlink()


def test_harness_dirs_come_after_the_claude_ones(env: Path) -> None:
    """Order is documented behaviour; keep the harness entries appended."""
    assert AGENT_RUNTIME_DIRS[:4] == (
        ".claude/projects",
        ".claude/todos",
        ".claude/statsig",
        ".claude/shell-snapshots",
    )
    assert AGENT_RUNTIME_DIRS[4:] == DSH_RUNTIME_DIRS
