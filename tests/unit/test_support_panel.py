"""Tests for AstroAI support catalog + panel CLI helpers."""

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
    assert cat.routers[0].panel_default == "deepseek-v4.1-flash"
    assert "dsh" in cat.panel_agents
    assert "opencode" in cat.recommended_agents
    assert "muse" in cat.recommended_agents
    assert cat.role_model("data_scientist", "opencode-go") == "deepseek-v4.1-flash"
    assert rb.DSH_KEYS[0] == "OPENCODE_API_KEY"
    assert rb._KEY_TO_ROUTE["OPENCODE_API_KEY"][0] == "opencode-go"


def test_brand_logo_vendored() -> None:
    path = brand_logo_path()
    assert path is not None
    assert path.is_file()
    assert path.suffix == ".png"


def test_panel_role_pins_and_preset() -> None:
    pins = rb.panel_role_pins("opencode-go")
    assert pins["software_engineer"] == "deepseek-v4.1-flash"
    preset = rb.extract_preset_role_models()
    assert preset.get("data_scientist") == "deepseek-v4.1-flash"
    assert "vision" in preset.get("astrophysicist", "")


def test_next_fallback_skips_opencode_go() -> None:
    keys = {
        "OPENCODE_API_KEY": "z",
        "DEEPSEEK_API_KEY": "d",
        "GEMINI_API_KEY": "g",
    }
    assert rb.next_fallback_provider("opencode-go", keys) == "deepseek-official"
    assert rb.next_fallback_provider("deepseek-official", keys) == "google"
    assert rb.next_fallback_provider("anthropic-official", keys) is None


def test_is_opencode_go_headless_error() -> None:
    assert rb.is_opencode_go_headless_error("Error: MissingSessionID")
    assert rb.is_opencode_go_headless_error("x-opencode-session required")
    assert rb.is_opencode_go_headless_error("Console Go returned 400")
    assert not rb.is_opencode_go_headless_error("rate limit")


def test_force_provider_overrides_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    settings = tmp_path / ".dsh" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "agent-default-model:\n  provider: opencode-go\n  model: deepseek-v4.1-flash\n",
        encoding="utf-8",
    )
    route = rb.ensure_dsh_settings(tmp_path, dry_run=False, force_provider="deepseek-official")
    assert route == "deepseek-official"
    text = settings.read_text(encoding="utf-8")
    assert "deepseek-official" in text
    assert "astroaiPanel" in text or "AstroAI Panel" in text


def test_resolve_panel_route_detects_orphan_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = tmp_path / ".dsh" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "agent-default-model:\n  provider: deepseek-official\n  model: deepseek-flash\n",
        encoding="utf-8",
    )
    health = rb.resolve_panel_route(tmp_path)
    assert health["pinned"] == "deepseek-official"
    assert health["preferred"] == "opencode-go"
    assert health["effective"] == "opencode-go"
    assert health["pin_orphaned"] is True


def test_panel_doctor_flags_orphan_and_models_remap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("OPENCODE_API_KEY", "zen")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = home / ".dsh" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "agent-default-model:\n  provider: deepseek-official\n  model: deepseek-flash\n",
        encoding="utf-8",
    )
    doctor = runner.invoke(app, ["--json", "panel", "doctor"])
    assert doctor.exit_code == 0, doctor.output
    import json

    payload = json.loads(doctor.output)
    assert payload["pin_orphaned"] is True
    assert payload["effective"] == "opencode-go"
    assert payload["ok"] is False

    models = runner.invoke(app, ["panel", "models"])
    assert models.exit_code == 0, models.output
    assert "effective router: opencode-go" in models.output
    assert "→ remap" in models.output or "Shipped preset" in models.output


def test_panel_cli_models_routers_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    for cmd in (["panel", "routers"], ["panel", "models"], ["panel", "doctor"]):
        result = runner.invoke(app, ["--json", *cmd])
        assert result.exit_code in (0, 2), result.output
        assert "deepseek" in result.output.lower() or "opencode" in result.output.lower()


def test_agent_routers_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    result = runner.invoke(app, ["--json", "agent", "routers"])
    assert result.exit_code == 0, result.output
    assert "opencode-go" in result.output
