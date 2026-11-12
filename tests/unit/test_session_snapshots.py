"""canfar_lab.session.snapshots: $SCRATCH/src + dsh state to /arc and back."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from canfar_lab.errors import LabError
from canfar_lab.session import snapshots

pytestmark = pytest.mark.skipif(
    not (shutil.which("tar") and shutil.which("zstd") and shutil.which("git")),
    reason="needs tar, zstd and git",
)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    scratch = tmp_path / "scratch"
    (scratch / "src").mkdir(parents=True)
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("CANFAR_LAB_SNAPSHOT_DIR", str(tmp_path / "arc" / "snapshots"))
    return scratch


def _repo(path: Path) -> None:
    path.mkdir(parents=True)
    git = ["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    (path / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
    (path / "pixi.lock").write_text("lock\n", encoding="utf-8")
    (path / ".gitignore").write_text(".pixi/\n", encoding="utf-8")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)


def test_save_and_restore_round_trip(scratch: Path) -> None:
    repo = scratch / "src" / "proj"
    _repo(repo)
    (repo / "wip.py").write_text("print('uncommitted')\n", encoding="utf-8")
    (repo / ".pixi" / "envs").mkdir(parents=True)
    (repo / ".pixi" / "envs" / "big").write_text("x" * 1000, encoding="utf-8")
    state = snapshots.dsh_state_dir(scratch)
    state.mkdir(parents=True)
    (state / "sessions.db").write_text("dsh\n", encoding="utf-8")

    saved = snapshots.save("s1")
    assert saved["ok"] and saved["dsh_state"] == str(state)
    assert saved["repos"] == [
        {
            "path": "proj",
            "branch": "main",
            "head": saved["repos"][0]["head"],
            "remote": "",
            "dirty": 1,
            "ahead": None,
        }
    ]
    assert [r["name"] for r in snapshots.list_snapshots()] == ["s1"]

    # New session: empty scratch.
    shutil.rmtree(scratch)
    (scratch / "src").mkdir(parents=True)
    result = snapshots.restore("s1")
    assert result["restored"] == ["proj"] and not result["skipped"]
    assert (scratch / "src" / "proj" / "wip.py").read_text() == "print('uncommitted')\n"
    assert (scratch / "src" / "proj" / "pixi.lock").is_file()
    assert not (scratch / "src" / "proj" / ".pixi").exists()
    assert (scratch / "src" / "proj" / ".git").is_dir()
    assert (state / "sessions.db").read_text() == "dsh\n"
    assert result["dsh"]["restored"] and not result["dsh"]["restart_required"]


def test_include_envs_keeps_pixi(scratch: Path) -> None:
    (scratch / "src" / "p" / ".pixi").mkdir(parents=True)
    (scratch / "src" / "p" / ".pixi" / "env").write_text("e", encoding="utf-8")
    snapshots.save("full", include_envs=True, include_dsh=False)
    shutil.rmtree(scratch / "src" / "p")
    snapshots.restore("full")
    assert (scratch / "src" / "p" / ".pixi" / "env").is_file()


def test_restore_skips_existing_unless_overwrite(scratch: Path) -> None:
    (scratch / "src" / "a").mkdir()
    (scratch / "src" / "a" / "f").write_text("saved", encoding="utf-8")
    snapshots.save("s", include_dsh=False)
    (scratch / "src" / "a" / "f").write_text("live", encoding="utf-8")

    result = snapshots.restore("s")
    assert result["skipped"] == ["a"]
    assert (scratch / "src" / "a" / "f").read_text() == "live"

    result = snapshots.restore("s", overwrite=True)
    assert result["replaced"] == ["a"]
    assert (scratch / "src" / "a" / "f").read_text() == "saved"
    assert (Path(result["replaced_dir"]) / "a" / "f").read_text() == "live"


def test_live_dsh_state_kept_without_overwrite(scratch: Path) -> None:
    state = snapshots.dsh_state_dir(scratch)
    state.mkdir(parents=True)
    (state / "db").write_text("old", encoding="utf-8")
    snapshots.save("s")
    (state / "db").write_text("live", encoding="utf-8")

    assert not snapshots.restore("s")["dsh"]["restored"]
    assert (state / "db").read_text() == "live"

    # Replacing code never touches the live assistant history.
    assert not snapshots.restore("s", overwrite=True)["dsh"]["restored"]
    assert (state / "db").read_text() == "live"

    dsh = snapshots.restore("s", replace_dsh=True)["dsh"]
    assert dsh["restored"] and dsh["restart_required"]
    assert (state / "db").read_text() == "old"
    assert (Path(dsh["previous"]) / "db").read_text() == "live"


def test_save_refuses_existing_name_and_bad_names(scratch: Path) -> None:
    (scratch / "src" / "x").mkdir()
    snapshots.save("dup", include_dsh=False)
    with pytest.raises(LabError, match="already exists"):
        snapshots.save("dup")
    snapshots.save("dup", overwrite=True, include_dsh=False)
    for bad in ("../up", ".hidden", "a b", ""):
        with pytest.raises(LabError, match="Invalid snapshot name"):
            snapshots.save(bad or "-")
    with pytest.raises(LabError, match="Invalid snapshot name"):
        snapshots.restore("../dup")


def test_nothing_to_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "s"
    empty.mkdir()
    monkeypatch.setenv("CANFAR_LAB_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    with pytest.raises(LabError, match="Nothing to save"):
        snapshots.save("n", scratch=empty)


def test_delete_and_missing(scratch: Path) -> None:
    (scratch / "src" / "x").mkdir()
    snapshots.save("gone", include_dsh=False)
    assert snapshots.delete("gone")["ok"]
    assert snapshots.list_snapshots() == []
    with pytest.raises(LabError, match="not found"):
        snapshots.restore("gone")


def test_failed_save_leaves_no_snapshot(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (scratch / "src" / "x").mkdir()

    def _boom(*_a: object, **_k: object) -> None:
        raise LabError("disk full")

    monkeypatch.setattr(snapshots, "_pack", _boom)
    with pytest.raises(LabError, match="disk full"):
        snapshots.save("broken")
    root = snapshots.snapshot_root()
    assert list(root.iterdir()) == []


def test_two_overwrite_restores_keep_both_set_aside_copies(scratch: Path) -> None:
    (scratch / "src" / "notes.txt").write_text("saved", encoding="utf-8")
    snapshots.save("s", include_dsh=False)
    (scratch / "src" / "notes.txt").write_text("live-1", encoding="utf-8")
    first = snapshots.restore("s", overwrite=True)
    (scratch / "src" / "notes.txt").write_text("live-2", encoding="utf-8")
    second = snapshots.restore("s", overwrite=True)  # same second as the first
    assert first["replaced_dir"] != second["replaced_dir"]
    assert (Path(first["replaced_dir"]) / "notes.txt").read_text() == "live-1"
    assert (Path(second["replaced_dir"]) / "notes.txt").read_text() == "live-2"


def test_snapshot_names_reject_trailing_newline() -> None:
    with pytest.raises(LabError):
        snapshots._check_name("friday\n")
    with pytest.raises(LabError):
        snapshots._check_name(3)  # type: ignore[arg-type]
