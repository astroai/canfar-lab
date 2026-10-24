"""Install root: home vs scratch, upgrade drop links, settings payload."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canfar_lab.agent.bin_root import (
    ROOT_HOME,
    ROOT_SCRATCH,
    align_home_bin_drops,
    layout_report,
    set_bin_root,
    settings_payload,
    sync_fast_io_env,
    write_bin_root,
)
from canfar_lab.core.paths import user_bin_dir
from canfar_lab.errors import LabError


def _session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, scratch: bool
) -> tuple[Path, Path | None]:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.delenv("CANFAR_LAB_BIN_DIR", raising=False)
    monkeypatch.setenv("CANFAR_LAB_RUNTIME_ROOT", str(work / ".runtime-test"))
    for name in ("SKAHA_SESSIONID", "skaha_sessionid", "SKAHA_HOSTNAME", "skaha_hostname"):
        monkeypatch.delenv(name, raising=False)
    scratch_dir: Path | None = None
    if scratch:
        scratch_dir = tmp_path / "scratch"
        scratch_dir.mkdir()
        monkeypatch.setenv("SCRATCH", str(scratch_dir))
        monkeypatch.setenv("ASTROAI_SESSION_KIND", "studio")
    else:
        monkeypatch.delenv("SCRATCH", raising=False)
        monkeypatch.delenv("ASTROAI_SESSION_KIND", raising=False)
    return home, scratch_dir


def test_home_root_uses_home_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    write_bin_root(home, ROOT_HOME)
    assert user_bin_dir() == home / ".local" / "bin"


def test_bin_dir_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _scratch = _session(tmp_path, monkeypatch, scratch=True)
    write_bin_root(home, ROOT_HOME)
    override = tmp_path / "override" / "bin"
    monkeypatch.setenv("CANFAR_LAB_BIN_DIR", str(override))
    assert user_bin_dir() == override


def test_laptop_path_unchanged_when_root_file_says_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _scratch = _session(tmp_path, monkeypatch, scratch=False)
    write_bin_root(home, ROOT_SCRATCH)
    bin_dir = user_bin_dir()
    assert bin_dir == tmp_path / "work" / ".runtime-test" / "bin"
    assert not str(bin_dir).startswith(str(home))


def test_scratch_directory_without_a_session_is_a_laptop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    monkeypatch.delenv("ASTROAI_SESSION_KIND", raising=False)
    assert user_bin_dir() == tmp_path / "work" / ".runtime-test" / "bin"
    payload = settings_payload(home)
    assert payload["choice"] is False
    with pytest.raises(LabError, match="CANFAR"):
        set_bin_root(home, ROOT_SCRATCH)


def test_set_bin_root_refuses_on_laptop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _scratch = _session(tmp_path, monkeypatch, scratch=False)
    with pytest.raises(LabError, match="CANFAR"):
        set_bin_root(home, ROOT_SCRATCH)


def test_scratch_root_symlinks_drop_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    drop = home / ".opencode" / "bin"
    drop.mkdir(parents=True)
    (drop / "opencode").write_text("bin", encoding="utf-8")
    actions = align_home_bin_drops(home, scratch=scratch, root=ROOT_SCRATCH)
    assert any(item.endswith(".opencode/bin") for item in actions)
    assert drop.is_symlink()
    assert (scratch / ".local" / "bin" / "opencode").is_file()


def test_home_root_leaves_drop_dir_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    drop = home / ".opencode" / "bin"
    drop.mkdir(parents=True)
    (drop / "opencode").write_text("bin", encoding="utf-8")
    align_home_bin_drops(home, scratch=scratch, root=ROOT_HOME)
    assert drop.is_dir()
    assert not drop.is_symlink()
    assert (drop / "opencode").is_file()


def test_home_root_keeps_binaries_from_a_scratch_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    managed = scratch / ".local" / "bin"
    managed.mkdir(parents=True)
    (managed / "opencode").write_text("bin", encoding="utf-8")
    drop = home / ".local" / "bin"
    drop.parent.mkdir(parents=True)
    drop.symlink_to(managed, target_is_directory=True)
    align_home_bin_drops(home, scratch=scratch, root=ROOT_HOME)
    assert drop.is_dir() and not drop.is_symlink()
    assert (drop / "opencode").read_text(encoding="utf-8") == "bin"


def test_scratch_root_keeps_drop_dir_with_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    drop = home / ".kilo" / "bin"
    (drop / "extra").mkdir(parents=True)
    (drop / "kilo").write_text("bin", encoding="utf-8")
    actions = align_home_bin_drops(home, scratch=scratch, root=ROOT_SCRATCH)
    assert "kept:.kilo/bin" in actions
    assert not drop.is_symlink()


def test_second_home_binary_is_an_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    write_bin_root(home, ROOT_SCRATCH)
    drop = home / ".opencode" / "bin"
    drop.mkdir(parents=True)
    (drop / "opencode").write_text("home", encoding="utf-8")
    managed = scratch / ".local" / "bin"
    managed.mkdir(parents=True)
    (managed / "opencode").write_text("scratch", encoding="utf-8")
    (drop / "notes").mkdir()
    report = layout_report(home)
    assert any("second binary" in str(item) for item in report["issues"])


def test_fast_io_hook_prefixes_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, scratch = _session(tmp_path, monkeypatch, scratch=True)
    assert scratch is not None
    write_bin_root(home, ROOT_SCRATCH)
    assert sync_fast_io_env(home) == "updated"
    text = (home / ".astroai" / "lab" / "agent-env.sh").read_text(encoding="utf-8")
    assert "CANFAR_LAB_BIN_DIR" in text
    assert 'export PATH="${CANFAR_LAB_BIN_DIR}:${PATH}"' in text
    assert "XDG_CACHE_HOME" in text
    assert sync_fast_io_env(home) is None


def test_settings_payload_has_key_names_not_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _scratch = _session(tmp_path, monkeypatch, scratch=True)
    secret = "supersecretvalue1"
    env = home / ".astroai" / "lab" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text(f"OPENROUTER_API_KEY={secret}\n", encoding="utf-8")
    payload = settings_payload(home)
    blob = json.dumps(payload)
    assert secret not in blob
    assert "value" not in payload
    keys = payload["keys"]
    assert isinstance(keys, list)
    row = next(
        item for item in keys if isinstance(item, dict) and item["key"] == "OPENROUTER_API_KEY"
    )
    assert row["present"] is True
    assert row["label"] == "OpenRouter"
    assert row["signup_url"].startswith("https://")
    assert payload["root"] == ROOT_SCRATCH
    assert payload["choice"] is True
    assert payload["default_root"] == ROOT_SCRATCH
