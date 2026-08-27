"""Tests for shared OpenRouter key discovery / marimo dotenv wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from astroai_lab.agent.setup import (
    OPENROUTER_KEY_ENV,
    discover_openrouter_key,
    ensure_openrouter_dotenv,
    openrouter_dotenv_path,
)


@pytest.fixture(autouse=True)
def _clear_openrouter_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENROUTER_KEY_ENV, raising=False)
    monkeypatch.delenv("OPENROUTER_KEY", raising=False)


def test_discover_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENROUTER_KEY_ENV, "sk-from-env")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert discover_openrouter_key(tmp_path) == "sk-from-env"


def test_discover_from_dotenv_then_marimo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    dotenv = openrouter_dotenv_path(tmp_path)
    dotenv.parent.mkdir(parents=True)
    dotenv.write_text(f"{OPENROUTER_KEY_ENV}=sk-from-dotenv\n", encoding="utf-8")
    assert discover_openrouter_key(tmp_path) == "sk-from-dotenv"

    dotenv.unlink()
    (tmp_path / ".marimo.toml").write_text(
        '[ai.openrouter]\napi_key = "sk-from-marimo"\n',
        encoding="utf-8",
    )
    assert discover_openrouter_key(tmp_path) == "sk-from-marimo"


def test_ensure_persists_marimo_key_to_shared_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".marimo.toml").write_text(
        '[ai.openrouter]\napi_key = "sk-reuse-me"\nbase_url = "https://openrouter.ai/api/v1"\n',
        encoding="utf-8",
    )
    key = ensure_openrouter_dotenv(tmp_path, dry_run=False)
    assert key == "sk-reuse-me"
    dotenv = openrouter_dotenv_path(tmp_path)
    assert dotenv.is_file()
    assert f"{OPENROUTER_KEY_ENV}=sk-reuse-me" in dotenv.read_text(encoding="utf-8")
    hook = tmp_path / ".astroai" / "lab" / "agent-env.sh"
    assert hook.is_file()
    assert "astroai openrouter dotenv" in hook.read_text(encoding="utf-8")
    assert discover_openrouter_key(tmp_path) == "sk-reuse-me"


def test_merge_marimo_seeds_api_key_and_runtime_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent.setup import _merge_marimo_openrouter

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv(OPENROUTER_KEY_ENV, "sk-setup-key")
    cfg = tmp_path / ".marimo.toml"
    _merge_marimo_openrouter(cfg, force=False, dry_run=False)
    text = cfg.read_text(encoding="utf-8")
    assert 'api_key = "sk-setup-key"' in text
    assert "[runtime]" in text
    assert str(openrouter_dotenv_path(tmp_path)) in text
    assert 'manager = "pixi"' in text
    assert (
        openrouter_dotenv_path(tmp_path)
        .read_text(encoding="utf-8")
        .startswith(f"{OPENROUTER_KEY_ENV}=sk-setup-key")
    )


def test_merge_marimo_replaces_pip_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent.setup import _merge_marimo_openrouter

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = tmp_path / ".marimo.toml"
    cfg.write_text(
        '[package_management]\nmanager = "pip"\n\n[ai.openrouter]\nbase_url = "https://openrouter.ai/api/v1"\n',
        encoding="utf-8",
    )
    _merge_marimo_openrouter(cfg, force=False, dry_run=False)
    assert 'manager = "pixi"' in cfg.read_text(encoding="utf-8")
