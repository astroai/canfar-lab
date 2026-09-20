"""Agent runtime relocation keeps DBs/session stores off the shared home."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from astroai_lab.core.home_layout import (
    AGENT_RUNTIME_DIRS,
    AGENT_RUNTIME_FORCE_DIRS,
    CLAUDE_RUNTIME_DIRS,
    CODEX_RUNTIME_DIRS,
    CURSOR_RUNTIME_DIRS,
    DSH_RUNTIME_DIRS,
    OMP_RUNTIME_DIRS,
    OPENCLAW_RUNTIME_DIRS,
    PI_RUNTIME_DIRS,
    ensure_omp_xdg_roots,
    relocate_agent_runtime,
    seed_hermes_home,
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
    natives = home / ".omp" / "natives"
    assert natives.is_symlink() and natives.resolve().is_dir()


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
    """Non-forced Claude trees still respect MIGRATE_LIMIT_MB."""
    from astroai_lab.core import home_layout

    monkeypatch.setattr(home_layout, "MIGRATE_LIMIT_MB", 0)
    home, data = env
    # .claude/todos is runtime but not in AGENT_RUNTIME_FORCE_DIRS.
    big = home / ".claude" / "todos"
    big.mkdir(parents=True)
    (big / "huge.db").write_bytes(b"x" * 4096)

    actions = relocate_agent_runtime(home, data)

    assert not (home / ".claude" / "todos").is_symlink()
    assert any(a.startswith("skipped:.claude/todos") for a in actions)


def test_oversized_omp_natives_are_force_relocated(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """omp natives/Chrome must leave /arc even when hundreds of MB."""
    from astroai_lab.core import home_layout

    monkeypatch.setattr(home_layout, "MIGRATE_LIMIT_MB", 0)
    home, data = env
    natives = home / ".omp" / "natives"
    natives.mkdir(parents=True)
    (natives / "pi_natives.linux-x64-modern.node").write_bytes(b"x" * 8192)

    actions = relocate_agent_runtime(home, data)

    link = home / ".omp" / "natives"
    assert link.is_symlink()
    assert (link.resolve() / "pi_natives.linux-x64-modern.node").is_file()
    assert any(a == "relocate:.omp/natives" for a in actions)
    assert ".omp/natives" in AGENT_RUNTIME_FORCE_DIRS


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


def test_omp_runtime_trees_are_relocated(env: Path) -> None:
    home, data = env
    relocate_agent_runtime(home, data)
    for rel in OMP_RUNTIME_DIRS:
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


def test_omp_config_notes_stay_on_home(env: Path) -> None:
    home, data = env
    notes = home / ".config" / "omp" / "astroai-notes.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("# notes\n", encoding="utf-8")
    relocate_agent_runtime(home, data)
    assert notes.is_file() and not notes.is_symlink()


def test_ensure_omp_xdg_roots_seeds_missing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    data = tmp_path / "data"
    state = tmp_path / "state"
    cache.mkdir()
    data.mkdir()
    state.mkdir()
    actions = ensure_omp_xdg_roots(cache, data, state)
    assert (cache / "omp").is_dir()
    assert (data / "omp").is_dir()
    assert (state / "omp").is_dir()
    assert len(actions) == 3
    assert ensure_omp_xdg_roots(cache, data, state) == []


def test_ensure_agent_runtime_skips_synthetic_home(tmp_path: Path) -> None:
    """Unit-test homes must not get session-scratch symlinks."""
    from astroai_lab.core.home_layout import ensure_agent_runtime_on_scratch

    fake = tmp_path / "not-real-home"
    fake.mkdir()
    assert ensure_agent_runtime_on_scratch(fake) == []


def test_ensure_agent_runtime_on_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install/setup entry point: env redirects + symlinks under the real home."""
    from astroai_lab.core import home_layout as hl

    home = tmp_path / "home"
    scratch = tmp_path / "scratch"
    home.mkdir()
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.setenv("WORK", str(scratch / "work"))
    for var in (
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "HERMES_HOME",
        "CODEX_SQLITE_HOME",
    ):
        monkeypatch.delenv(var, raising=False)

    actions = hl.ensure_agent_runtime_on_scratch(home, dry_run=False)
    assert any(a.startswith(("link:", "env:")) for a in actions)
    assert (home / ".codex" / "sessions").is_symlink()
    assert os.environ.get("CODEX_SQLITE_HOME", "").startswith(str(scratch))
    assert os.environ.get("HERMES_HOME", "").startswith(str(scratch))
    hermes = Path(os.environ["HERMES_HOME"])
    assert hermes.is_dir()


def test_agent_cli_runtime_trees_are_relocated(env: Path) -> None:
    """codex / cursor / pi / openclaw hot trees leave /arc via symlinks."""
    home, data = env
    relocate_agent_runtime(home, data)
    cli_dirs = (
        *CURSOR_RUNTIME_DIRS,
        *CODEX_RUNTIME_DIRS,
        *PI_RUNTIME_DIRS,
        *OPENCLAW_RUNTIME_DIRS,
    )
    for rel in cli_dirs:
        link = home / rel
        assert link.is_symlink(), rel
        assert data in link.resolve().parents


def test_oversized_codex_sessions_are_force_relocated(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.core import home_layout

    monkeypatch.setattr(home_layout, "MIGRATE_LIMIT_MB", 0)
    home, data = env
    sessions = home / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "rollout.jsonl").write_bytes(b"x" * 8192)

    actions = relocate_agent_runtime(home, data)

    link = home / ".codex" / "sessions"
    assert link.is_symlink()
    assert (link.resolve() / "rollout.jsonl").is_file()
    assert any(a == "relocate:.codex/sessions" for a in actions)
    assert ".codex/sessions" in AGENT_RUNTIME_FORCE_DIRS


def test_seed_hermes_home_copies_config_once(tmp_path: Path) -> None:
    home = tmp_path / "home"
    hermes = tmp_path / "scratch" / "hermes-home"
    home.mkdir()
    (home / ".hermes").mkdir()
    (home / ".hermes" / "config.yaml").write_text("model: x\n", encoding="utf-8")

    actions = seed_hermes_home(hermes, home)
    assert hermes.is_dir()
    assert (hermes / "config.yaml").read_text(encoding="utf-8") == "model: x\n"
    assert "seed:hermes-config" in actions
    assert seed_hermes_home(hermes, home) == []


def test_seed_hermes_home_dry_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    hermes = tmp_path / "scratch" / "hermes-home"
    home.mkdir()
    (home / ".hermes").mkdir()
    (home / ".hermes" / "config.yaml").write_text("model: x\n", encoding="utf-8")
    actions = seed_hermes_home(hermes, home, dry_run=True)
    assert "seed:hermes-home" in actions
    assert "seed:hermes-config" in actions
    assert not hermes.exists()


def test_harness_dirs_come_after_the_claude_ones(env: Path) -> None:
    """Order is documented behaviour; Claude first, then dsh/omp, then CLIs."""
    n_claude = len(CLAUDE_RUNTIME_DIRS)
    assert AGENT_RUNTIME_DIRS[:n_claude] == CLAUDE_RUNTIME_DIRS
    assert AGENT_RUNTIME_DIRS[n_claude : n_claude + len(DSH_RUNTIME_DIRS)] == DSH_RUNTIME_DIRS
    omp_start = n_claude + len(DSH_RUNTIME_DIRS)
    assert AGENT_RUNTIME_DIRS[omp_start : omp_start + len(OMP_RUNTIME_DIRS)] == OMP_RUNTIME_DIRS
    rest = AGENT_RUNTIME_DIRS[omp_start + len(OMP_RUNTIME_DIRS) :]
    assert rest == (
        *CURSOR_RUNTIME_DIRS,
        *CODEX_RUNTIME_DIRS,
        *PI_RUNTIME_DIRS,
        *OPENCLAW_RUNTIME_DIRS,
    )
