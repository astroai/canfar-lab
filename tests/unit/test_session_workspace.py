"""canfar_lab.session.workspace: code/cache dir choice kept across sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from canfar_lab.errors import LabError
from canfar_lab.session import workspace


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "home"
    scratch = tmp_path / "scratch"
    home.mkdir()
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.delenv("SRCDIR", raising=False)
    return {"home": home, "scratch": scratch, "tmp": tmp_path}


def test_defaults_without_a_settings_file(env: dict[str, Path]) -> None:
    settings = workspace.load()
    assert not settings.configured and settings.mode == "scratch"
    assert workspace.configured_code_dir() is None
    assert workspace.code_dir() == env["scratch"] / "src"
    assert workspace.is_ephemeral(workspace.code_dir())


def test_choose_home_persists_privately(env: dict[str, Path]) -> None:
    result = workspace.set_code_dir("home")
    target = env["home"] / "src"
    assert result["new"] == str(target) and result["changed"]
    assert target.is_dir()
    path = workspace.settings_path()
    assert path == env["home"] / ".canfar" / "lab" / "workspace.json"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["code_dir"]["mode"] == "home"
    assert workspace.code_dir() == target and not workspace.is_ephemeral(target)


def test_custom_dir_and_validation(env: dict[str, Path]) -> None:
    custom = env["tmp"] / "projects" / "me"
    workspace.set_code_dir("custom", str(custom))
    assert workspace.code_dir() == custom and custom.is_dir()
    bad = [
        ("relative/path", "absolute"),
        (str(env["home"]), "cannot be"),
        (str(env["home"] / ".claude" / "x"), "hidden folders"),
        ("/etc/mine", "cannot be under /etc"),
        ("/", "cannot be /"),
    ]
    for path, message in bad:
        with pytest.raises(LabError, match=message):
            workspace.set_code_dir("custom", path)
    with pytest.raises(LabError, match="needs a path"):
        workspace.set_code_dir("custom")
    with pytest.raises(LabError, match="Unknown code directory mode"):
        workspace.set_code_dir("cloud")


def test_saved_choice_drives_work_dir_but_srcdir_wins(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from canfar_lab.config.settings import get_settings

    workspace.set_code_dir("home")
    get_settings.cache_clear()
    assert get_settings().resolve_work_dir() == env["home"] / "src"
    explicit = env["tmp"] / "explicit"
    explicit.mkdir()
    monkeypatch.setenv("SRCDIR", str(explicit))
    assert get_settings().resolve_work_dir() == explicit


def test_options_and_clamping(env: dict[str, Path]) -> None:
    workspace.set_options(autosave=False, interval_min=1, keep=999, restore_at_start="ask")
    s = workspace.load()
    assert (s.autosave.enabled, s.autosave.interval_min, s.autosave.keep) == (False, 5, 50)
    assert s.restore_at_start == "ask"
    with pytest.raises(LabError, match="restore_at_start"):
        workspace.set_options(restore_at_start="sometimes")


def test_cache_dir_never_on_home(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LabError, match="/arc home"):
        workspace.set_options(cache_dir=str(env["home"] / "cache"))
    cache = env["tmp"] / "cache"
    workspace.set_options(cache_dir=str(cache))
    assert workspace.cache_base() == cache
    from canfar_lab.shell.session_env import resolve_session_env

    exported = resolve_session_env(ensure=False).exports()
    assert exported["UV_CACHE_DIR"].startswith(str(cache))
    assert exported["CANFAR_LAB_RUNTIME_ROOT"].startswith(str(cache))
    workspace.set_options(reset_cache_dir=True)
    assert workspace.cache_base() is None
    exported = resolve_session_env(ensure=False).exports()
    assert exported["UV_CACHE_DIR"].startswith(str(env["scratch"]))


def test_damaged_settings_fall_back_to_defaults(env: dict[str, Path]) -> None:
    path = workspace.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    settings = workspace.load()
    assert settings.configured and settings.mode == "scratch"
    path.write_text(json.dumps({"code_dir": {"mode": "custom"}}), encoding="utf-8")
    assert workspace.load().mode == "scratch"  # custom without a path


def test_privacy_and_make_private(env: dict[str, Path]) -> None:
    folder = env["tmp"] / "shared"
    folder.mkdir()
    os.chmod(folder, 0o755)
    info = workspace.privacy(folder)
    assert info["private"] is False and info["group_access"] and info["other_access"]
    result = workspace.make_private(folder)
    assert result["private"] is True and os.stat(folder).st_mode & 0o777 == 0o700


def test_copy_projects_skips_envs_and_existing(env: dict[str, Path]) -> None:
    old, new = env["tmp"] / "old", env["tmp"] / "new"
    (old / "a" / ".pixi" / "envs").mkdir(parents=True)
    (old / "a" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (old / "b").mkdir()
    (new / "b").mkdir(parents=True)
    result = workspace.copy_projects(old, new)
    assert result["copied"] == ["a"] and result["skipped"] == ["b"]
    assert (new / "a" / "main.py").is_file() and not (new / "a" / ".pixi").exists()
    assert (old / "a" / "main.py").is_file()  # originals stay
    with pytest.raises(LabError, match="same"):
        workspace.copy_projects(old, old)


def test_describe_reports_restart_needed(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SRCDIR", str(env["scratch"] / "src"))
    workspace.set_code_dir("home")
    monkeypatch.setenv("SRCDIR", str(env["scratch"] / "src"))  # tools started before
    state = workspace.describe()
    assert state["restart_needed"] and state["live_code_path"] == str(env["scratch"] / "src")
    assert [c["mode"] for c in state["choices"]] == ["scratch", "home", "custom"]


def test_live_code_dir_prefers_the_image_file(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    live = env["tmp"] / "live"
    live.write_text(str(env["home"] / "src") + "\n", encoding="utf-8")
    monkeypatch.setenv("SRCDIR", "/somewhere/old")
    monkeypatch.setenv("CANFAR_LAB_LIVE_CODE_FILE", str(live))
    workspace.set_code_dir("home")
    state = workspace.describe()
    assert state["live_code_path"] == str(env["home"] / "src") and not state["restart_needed"]
