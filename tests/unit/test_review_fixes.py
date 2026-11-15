"""Regressions found in the Settings/workspace review (2026-11-15)."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from canfar_lab.core.state_home import lab_home, migrate_legacy_state
from canfar_lab.errors import LabError
from canfar_lab.session import snapshots, workspace
from canfar_lab.web import onboarding
from canfar_lab.web.autosave import AutoSaver
from canfar_lab.web.jobs import JobRunner

needs_tar = pytest.mark.skipif(
    not (shutil.which("tar") and shutil.which("zstd")), reason="needs tar and zstd"
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home, scratch = tmp_path / "home", tmp_path / "scratch"
    home.mkdir()
    (scratch / "src").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("skaha_sessionid", "sess-a")
    monkeypatch.setenv("CANFAR_LAB_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    monkeypatch.delenv("SRCDIR", raising=False)
    return {"home": home, "scratch": scratch, "tmp": tmp_path}


def test_migration_when_new_path_links_to_legacy(tmp_path: Path) -> None:
    old = tmp_path / ".astroai" / "lab"
    old.mkdir(parents=True)
    (old / ".env").write_text("K=v\n", encoding="utf-8")
    new = lab_home(tmp_path)
    new.parent.mkdir(parents=True)
    new.symlink_to(old, target_is_directory=True)
    migrate_legacy_state(tmp_path)
    assert not new.is_symlink() and (new / ".env").read_text() == "K=v\n"
    assert old.is_symlink() and (old / ".env").is_file()
    assert not list(new.parent.glob("lab.pre-migrate-*"))


@needs_tar
def test_prune_never_removes_another_sessions_only_save(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = env["scratch"] / "src" / "p"
    proj.mkdir()
    monkeypatch.setenv("skaha_sessionid", "sess-b")
    (proj / "b.py").write_text("b", encoding="utf-8")
    snapshots.save("auto-b-final", auto=True, include_dsh=False)
    monkeypatch.setenv("skaha_sessionid", "sess-a")
    for i in range(3):
        (proj / f"a{i}.py").write_text(str(i), encoding="utf-8")
        snapshots.save(f"auto-a-{i}", auto=True, include_dsh=False)
        snapshots.prune_auto(1)
    names = {r["name"] for r in snapshots.list_snapshots()}
    assert names == {"auto-b-final", "auto-a-2"}
    # Session B's save is not this session's: no fingerprint confusion.
    assert snapshots.latest_fingerprint() == next(
        r["fingerprint"] for r in snapshots.list_snapshots() if r["name"] == "auto-a-2"
    )


@needs_tar
def test_final_save_skipped_while_a_job_runs(env: dict[str, Path]) -> None:
    (env["scratch"] / "src" / "p").mkdir()
    (env["scratch"] / "src" / "p" / "x.py").write_text("x", encoding="utf-8")
    saver = AutoSaver(JobRunner(capture=False))
    gate = threading.Event()
    job = saver.runner.start("snapshot_restore", "Restore", lambda _j: gate.wait(5) and {})
    assert saver.final(budget=0.6) is None and "still running" in str(saver.last_error)
    gate.set()
    saver.runner.wait(job, timeout=5)


def test_copy_refuses_nesting(env: dict[str, Path]) -> None:
    src = env["scratch"] / "src"
    (src / "p").mkdir()
    with pytest.raises(LabError, match="inside the other"):
        workspace.copy_projects(src, src / "new")


def test_saved_custom_dir_is_rechecked(env: dict[str, Path]) -> None:
    path = workspace.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"code_dir": {"mode": "custom", "path": "/etc/evil"}}', encoding="utf-8")
    assert workspace.configured_code_dir() is None
    assert workspace.code_dir() == env["scratch"] / "src"


def test_explicit_work_beats_saved_dir_but_image_default_does_not(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from canfar_lab.config.settings import get_settings

    workspace.set_code_dir("home")
    monkeypatch.setenv("WORK", "/srcdir")
    get_settings.cache_clear()
    assert get_settings().resolve_work_dir() == env["home"] / "src"
    job = env["tmp"] / "job"
    job.mkdir()
    monkeypatch.setenv("WORK", str(job))
    assert get_settings().resolve_work_dir() == job


def _agent() -> dict:
    return {
        "id": "pi",
        "label": "Pi",
        "installed": True,
        "needs_restore": False,
        "remembered": True,
        "ready": True,
        "login": "pi",
        "keys": [],
    }


def test_onboarding_respects_start_empty_and_asks_for_restart() -> None:
    snap = {"name": "s", "created_at": "2026-11-06T17:00:00+00:00", "repos": []}
    code = {"src": "/s/src", "entries": [], "repos": [], "ephemeral": True}
    never = onboarding.build(
        agents=[_agent()], keys=[], snapshots=[snap], code={**code, "restore_at_start": "never"}
    )
    assert never["ready"] and never["steps"][-1]["status"] == "optional"
    moved = onboarding.build(
        agents=[_agent()],
        keys=[],
        snapshots=[],
        code={**code, "restart_needed": True, "live_src": "/old", "restart_supported": True},
    )
    step = moved["steps"][-1]
    assert step["id"] == "restart" and step["action"]["op"] == "restart_tools"
    assert not moved["ready"] and moved["next"] == "restart"


def test_friendly_times() -> None:
    now = 1_794_700_000.0  # 2026-11-14T03:06:40Z
    assert onboarding._when("2026-11-14T01:05:00+00:00", now) == "today at 01:05 UTC"
    assert onboarding._when("2026-11-13T22:00:00+00:00", now) == "yesterday at 22:00 UTC"
    assert onboarding._when("2026-11-06T17:00:00+00:00", now) == "on 6 Nov"
    assert onboarding._when("2025-01-02T00:00:00+00:00", now) == "on 2 Jan 2025"
    assert onboarding._when("garbage", now) == "garbage"
