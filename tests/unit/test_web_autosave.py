"""canfar_lab.web.autosave: scheduled and shutdown snapshots of scratch code."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from canfar_lab.session import snapshots, workspace
from canfar_lab.web.autosave import AutoSaver
from canfar_lab.web.jobs import JobRunner

pytestmark = pytest.mark.skipif(
    not (shutil.which("tar") and shutil.which("zstd")), reason="needs tar and zstd"
)


@pytest.fixture
def code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home, scratch = tmp_path / "home", tmp_path / "scratch"
    home.mkdir()
    (scratch / "src" / "proj").mkdir(parents=True)
    (scratch / "src" / "proj" / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("CANFAR_LAB_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    return scratch / "src"


def _saver() -> AutoSaver:
    saver = AutoSaver(JobRunner(capture=False))
    saver.last_save = 0.0  # pretend the interval has passed
    return saver


def test_due_only_when_changed_and_interval_passed(code: Path) -> None:
    saver = _saver()
    assert saver.due()
    job = saver.tick()
    assert job is not None and saver.runner.wait(job, timeout=30) and job.state == "ok"
    rows = snapshots.list_snapshots()
    assert len(rows) == 1 and rows[0]["auto"] and rows[0]["name"].startswith("auto-")
    saver.last_save = 0.0
    assert not saver.due()  # nothing changed since
    (code / "proj" / "b.py").write_text("y = 2\n", encoding="utf-8")
    saver.last_save = time.time()
    assert not saver.due()  # changed, but the interval has not passed


def test_not_for_persistent_dirs_or_when_off(code: Path) -> None:
    workspace.set_options(autosave=False)
    assert not _saver().due()
    workspace.set_options(autosave=True)
    workspace.set_code_dir("home")
    (Path.home() / "src" / "p").mkdir(parents=True)
    assert not _saver().due()


def test_prune_keeps_manual_snapshots(code: Path) -> None:
    workspace.set_options(keep=1)
    snapshots.save("manual", include_dsh=False)
    saver = _saver()
    for i in range(3):
        (code / "proj" / f"f{i}.py").write_text(str(i), encoding="utf-8")
        saver._save()
        time.sleep(1.1)  # names carry a seconds timestamp
    names = [r["name"] for r in snapshots.list_snapshots()]
    assert "manual" in names and sum(n.startswith("auto-") for n in names) == 1


def test_final_saves_only_changes(code: Path) -> None:
    saver = _saver()
    assert saver.final(budget=20) is not None
    assert saver.final(budget=20) is None  # nothing new
