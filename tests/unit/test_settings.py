from __future__ import annotations

from pathlib import Path

import pytest

from canfar_lab.config.settings import get_settings
from canfar_lab.errors import LabError


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_yaml_config_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg_dir = home / ".astroai" / "lab"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text("default_pm: uv\nclone_from_env: ml-base\n")
    monkeypatch.setenv("HOME", str(home))
    settings = get_settings()
    assert settings.default_pm == "uv"
    assert settings.clone_from_env == "ml-base"


def test_desk_is_off_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANFAR_LAB_DESK", raising=False)
    assert get_settings().desk is False
    get_settings.cache_clear()
    monkeypatch.setenv("CANFAR_LAB_DESK", "yes")
    assert get_settings().desk is True


def test_lab_error_hint() -> None:
    err = LabError("missing tool", hint="astroai doctor")
    assert "missing tool" in str(err)
    assert err.hint == "astroai doctor"
