"""Tests for review-bench provisioning + dsh credential plumbing (keys only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from astroai_lab.agent import review_bench as rb
from astroai_lab.agent.registry import get_registry_agent


@pytest.fixture(autouse=True)
def _clear_dsh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*rb.DSH_KEYS, "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)


def test_registry_includes_dsh_npm() -> None:
    dsh = get_registry_agent("dsh")
    assert dsh is not None
    assert dsh["install"]["method"] == "npm"
    assert "dsh" in dsh["install"]["source"]
    assert dsh["config"]["path"] == "~/.dsh/settings.yaml"


def test_validate_vendored_preset_clean() -> None:
    checks, failures = rb.validate_preset()
    assert failures == []
    assert any("persona lenses" in c for c in checks)
    assert any("managed bench" in c for c in checks)


def test_validate_rejects_model_pins(tmp_path: Path) -> None:
    root = tmp_path / "bench"
    preset = root / "presets" / "review-bench"
    preset.mkdir(parents=True)
    (preset / "agent.cordis.yml").write_text(
        "- id: ask_statistician\n  config:\n    agentOptions:\n      model: deepseek-v4-pro\n",
        encoding="utf-8",
    )
    (root / "skills" / "review-panel").mkdir(parents=True)
    (root / "skills" / "review-panel" / "SKILL.md").write_text("x", encoding="utf-8")
    for name in (
        "references/rubric.md",
        "references/panel-round1.js",
        "references/brief-template.md",
        "references/report-template.md",
    ):
        p = root / "skills" / "review-panel" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    _, failures = rb.validate_preset(root)
    assert any("does not preset models" in f for f in failures)


def test_validate_rejects_missing_tree(tmp_path: Path) -> None:
    checks, failures = rb.validate_preset(tmp_path / "absent")
    assert failures
    assert checks == []


def test_discover_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    keys = rb.discover_dsh_keys(tmp_path)
    assert keys["GEMINI_API_KEY"] == "g-key"
    assert keys["DEEPSEEK_API_KEY"] == "d-key"


def test_discover_reads_opencode_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    auth = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"opencode-go": {"key": "zen-key"}}), encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    keys = rb.discover_dsh_keys(tmp_path)
    assert keys["OPENCODE_API_KEY"] == "zen-key"


def test_ensure_settings_never_touches_user_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen-key")
    settings = tmp_path / ".dsh" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        yaml.safe_dump({"agent-default-model": {"provider": "google", "model": "custom-1"}}),
        encoding="utf-8",
    )
    ensured = rb.ensure_dsh_settings(tmp_path, dry_run=False)
    assert ensured == ["opencode-go"]
    doc = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert doc["agent-default-model"] == {"provider": "google", "model": "custom-1"}
    entry = doc["llm-pi-ai"]["providers"]["opencode-go"]
    assert entry["apiKeyEnv"] == "OPENCODE_API_KEY"
    assert entry["api"] == "openai-completions"
    assert entry["baseURL"] == "https://opencode.ai/zen/go/v1"
    # Live fetch when reachable; otherwise yaml offline fallback.
    ids = {m["id"] for m in entry["models"]}
    assert "deepseek-v4.1-flash" in ids
    assert "muse-spark-1.3-contributor" in ids


def test_hand_declared_models_prefer_live_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen-key")
    monkeypatch.setattr(
        "astroai_lab.agent.support.fetch_openai_compat_model_ids",
        lambda *_a, **_k: ("live-model-a", "muse-spark-1.3-contributor"),
    )
    ensured = rb.ensure_dsh_settings(tmp_path, dry_run=False)
    assert ensured == ["opencode-go"]
    entry = yaml.safe_load((tmp_path / ".dsh" / "settings.yaml").read_text(encoding="utf-8"))[
        "llm-pi-ai"
    ]["providers"]["opencode-go"]
    assert entry["models"] == [
        {"id": "live-model-a"},
        {"id": "muse-spark-1.3-contributor"},
    ]


def test_ensure_settings_writes_catalog_routes_by_their_dsh_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openai-official` has to be written as the catalog route `openai`."""
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    ensured = rb.ensure_dsh_settings(tmp_path, dry_run=False)
    assert ensured == ["openai"]
    doc = yaml.safe_load((tmp_path / ".dsh" / "settings.yaml").read_text(encoding="utf-8"))
    assert doc["llm-pi-ai"]["providers"]["openai"] == {"apiKeyEnv": "OPENAI_API_KEY"}
    assert "agent-default-model" not in doc


def test_unserviceable_routes_are_skipped_not_half_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete hand-declared route must not poison the settings document."""
    from astroai_lab.agent import support as support_mod

    monkeypatch.setattr(support_mod, "load_support", _catalog_with_broken_route)
    monkeypatch.setattr(rb, "load_support", _catalog_with_broken_route)
    monkeypatch.setenv("OPENCODE_API_KEY", "zen-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    assert rb.unserviceable_keys() == {"OPENCODE_API_KEY"}
    ensured = rb.ensure_dsh_settings(tmp_path)
    assert ensured == ["deepseek-official"]
    doc = yaml.safe_load((tmp_path / ".dsh" / "settings.yaml").read_text(encoding="utf-8"))
    assert "opencode-go" not in doc["llm-pi-ai"]["providers"]


def _catalog_with_broken_route() -> object:
    """Support catalog whose hand-declared route lost its endpoint."""
    from astroai_lab.agent.support import Router, SupportCatalog

    return SupportCatalog(
        routers=(
            Router(
                id="opencode-go",
                key="OPENCODE_API_KEY",
                api="openai-completions",
                base_url="",
            ),
            Router(
                id="deepseek-official",
                key="DEEPSEEK_API_KEY",
            ),
        ),
        recommended_agents=(),
        panel_agents=(),
    )


def test_ensure_settings_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    assert rb.ensure_dsh_settings(tmp_path, dry_run=True) == ["deepseek-official"]
    assert not (tmp_path / ".dsh" / "settings.yaml").exists()


def test_ensure_dotenv_preserves_other_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen-key")
    dotenv = tmp_path / ".astroai" / "lab" / ".env"
    dotenv.parent.mkdir(parents=True)
    dotenv.write_text("OPENROUTER_API_KEY=sk-or\n", encoding="utf-8")
    rb.ensure_dsh_dotenv(tmp_path, dry_run=False)
    text = dotenv.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-or" in text
    assert "OPENCODE_API_KEY=zen-key" in text
    assert oct(dotenv.stat().st_mode & 0o777) == "0o600"


def test_ensure_review_bench_installs_and_idempotent(tmp_path: Path) -> None:
    assert rb.ensure_review_bench(tmp_path, dry_run=True) is True
    assert not (tmp_path / ".astroai").exists()  # dry-run writes nothing
    assert rb.ensure_review_bench(tmp_path, dry_run=False) is True
    bench = tmp_path / ".astroai" / "lab" / "review-bench"
    assert (bench / "presets" / "review-bench" / "agent.cordis.yml").is_file()
    assert (bench / "skills" / "review-panel" / "SKILL.md").is_file()
    web = tmp_path / ".dsh" / "profiles" / "web" / "cordis.patch.yml"
    assert web.is_file()
    assert (tmp_path / ".dsh" / "profiles" / "headless" / "cordis.yml").is_file()
    # Second run without force: nothing new, no clobber.
    assert rb.ensure_review_bench(tmp_path, dry_run=False) is False
    # Differing user layer is kept without --force.
    web.write_text("custom: true\n", encoding="utf-8")
    with pytest.warns(UserWarning, match="Refusing to overwrite"):
        assert rb.ensure_review_bench(tmp_path, dry_run=False) is False
    assert web.read_text(encoding="utf-8") == "custom: true\n"
    assert rb.ensure_review_bench(tmp_path, dry_run=False, force=True) is True


def test_no_keys_without_env(tmp_path: Path) -> None:
    assert rb.discover_dsh_keys(tmp_path) == {}
    assert rb.ensure_dsh_settings(tmp_path, dry_run=False) == []
    assert os.environ.get("OPENCODE_API_KEY") is None  # never leak into process env
