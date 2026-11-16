from __future__ import annotations

from pathlib import Path

from canfar_lab.agent import owned_files
from canfar_lab.agent.setup import install_file


def _src(tmp_path: Path, text: str) -> Path:
    src = tmp_path / "bundle" / "config.toml"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text)
    return src


def test_refresh_replaces_a_file_lab_wrote(tmp_path: Path) -> None:
    dst = Path.home() / ".codex" / "config.toml"
    assert install_file(_src(tmp_path, "v1"), dst, force=False, dry_run=False)
    with owned_files.keeping_user_edits() as kept:
        assert install_file(_src(tmp_path, "v2"), dst, force=True, dry_run=False)
    assert dst.read_text() == "v2"
    assert kept == []


def test_refresh_keeps_a_file_the_user_edited(tmp_path: Path) -> None:
    dst = Path.home() / ".codex" / "config.toml"
    install_file(_src(tmp_path, "v1"), dst, force=False, dry_run=False)
    dst.write_text('model = "my-own-model"')
    with owned_files.keeping_user_edits() as kept:
        assert not install_file(_src(tmp_path, "v2"), dst, force=True, dry_run=False)
    assert dst.read_text() == 'model = "my-own-model"'
    assert kept == [dst]


def test_refresh_keeps_a_file_with_no_record(tmp_path: Path) -> None:
    dst = Path.home() / ".claude" / "settings.json"
    dst.parent.mkdir(parents=True)
    dst.write_text("{}")
    with owned_files.keeping_user_edits() as kept:
        install_file(_src(tmp_path, "v2"), dst, force=True, dry_run=False)
    assert dst.read_text() == "{}"
    assert kept == [dst]


def test_reset_backs_up_the_user_edit(tmp_path: Path) -> None:
    dst = Path.home() / ".codex" / "config.toml"
    dst.parent.mkdir(parents=True)
    dst.write_text("mine")
    assert install_file(_src(tmp_path, "v2"), dst, force=True, dry_run=False)
    assert dst.read_text() == "v2"
    assert dst.with_name("config.toml.bak").read_text() == "mine"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    dst = Path.home() / ".codex" / "config.toml"
    assert install_file(_src(tmp_path, "v1"), dst, force=False, dry_run=True)
    assert not dst.exists()
    assert not (Path.home() / ".canfar" / "lab" / owned_files.RECORD_NAME).exists()
