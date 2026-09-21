"""Tests for AstroAI support catalog + panel CLI helpers (keys only)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent import review_bench as rb
from astroai_lab.agent.support import brand_logo_path, clear_support_cache, load_support
from astroai_lab.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_support() -> None:
    clear_support_cache()
    yield
    clear_support_cache()


def test_support_yaml_loads() -> None:
    cat = load_support()
    assert cat.routers[0].id == "opencode-go"
    assert cat.routers[0].key == "OPENCODE_API_KEY"
    assert cat.routers[0].base_url.endswith("/zen/go/v1")
    assert "deepseek-v4.1-flash" in cat.routers[0].models
    assert "muse-spark-1.3-contributor" in cat.routers[0].models
    assert len(cat.routers[0].models) <= 8  # offline seed, not the live catalog
    assert cat.routers[0].serviceable()
    assert not hasattr(cat.routers[0], "panel_default")
    assert "dsh" in cat.panel_agents
    assert "opencode" in cat.recommended_agents
    assert "muse" in cat.recommended_agents
    assert rb.DSH_KEYS[0] == "OPENCODE_API_KEY"
    assert rb._KEY_TO_ROUTE["OPENCODE_API_KEY"] == "opencode-go"


def test_brand_logo_vendored() -> None:
    path = brand_logo_path()
    assert path is not None
    assert path.is_file()
    assert path.suffix == ".png"


def test_no_model_pins_anywhere() -> None:
    assert rb.panel_role_pins("opencode-go") == {}
    assert rb.extract_preset_role_models() == {}
    text = (
        rb.vendored_review_bench_root() / "presets" / "review-bench" / "agent.cordis.yml"
    ).read_text(encoding="utf-8")
    assert "model:" not in text


def test_next_fallback_skips_opencode_go() -> None:
    keys = {
        "OPENCODE_API_KEY": "z",
        "DEEPSEEK_API_KEY": "d",
        "GEMINI_API_KEY": "g",
    }
    assert rb.next_fallback_provider("opencode-go", keys) == "deepseek-official"
    assert rb.next_fallback_provider("deepseek-official", keys) == "google"
    assert rb.next_fallback_provider("anthropic-official", keys) is None
    assert rb.next_fallback_provider(None, keys) == "deepseek-official"


def test_is_opencode_go_headless_error() -> None:
    assert rb.is_opencode_go_headless_error("Error: MissingSessionID")
    assert rb.is_opencode_go_headless_error("x-opencode-session required")
    assert rb.is_opencode_go_headless_error("Console Go returned 400")
    assert rb.is_opencode_go_headless_error("Console Go 401 unauthorized")
    assert rb.is_opencode_go_headless_error("opencode session required")
    assert not rb.is_opencode_go_headless_error("rate limit")


def test_ensure_settings_never_writes_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    ensured = rb.ensure_dsh_settings(tmp_path, dry_run=False)
    assert set(ensured) == {"opencode-go", "deepseek-official"}
    import yaml

    doc = yaml.safe_load((tmp_path / ".dsh" / "settings.yaml").read_text(encoding="utf-8"))
    assert "agent-default-model" not in doc
    assert doc["llm-pi-ai"]["providers"]["deepseek-official"] == {"apiKeyEnv": "DEEPSEEK_API_KEY"}


def test_resolve_key_health_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = tmp_path / ".dsh" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "agent-default-model:\n  provider: deepseek-official\n  model: custom\n",
        encoding="utf-8",
    )
    health = rb.resolve_panel_route(tmp_path)
    assert health["pinned"] == "deepseek-official"
    assert health["keys_present"] == ["OPENCODE_API_KEY"]
    assert health["usable"] is True


def test_panel_doctor_keys_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("OPENCODE_API_KEY", "zen")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    doctor = runner.invoke(app, ["--json", "panel", "doctor"])
    assert doctor.exit_code == 0, doctor.output
    import json

    payload = json.loads(doctor.output)
    assert payload["ok"] is True
    assert payload["keys_present"] == ["OPENCODE_API_KEY"]
    assert "note" in payload

    models = runner.invoke(app, ["--json", "panel", "models"])
    assert models.exit_code == 2, models.output
    assert "deprecated" in models.output


def test_panel_cli_routers_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    for cmd in (["panel", "routers"], ["panel", "doctor"]):
        result = runner.invoke(app, ["--json", *cmd])
        assert result.exit_code == 0, result.output
        assert "deepseek" in result.output.lower() or "opencode" in result.output.lower()


def test_agent_routers_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    result = runner.invoke(app, ["--json", "agent", "routers"])
    assert result.exit_code == 0, result.output
    assert "opencode-go" in result.output
