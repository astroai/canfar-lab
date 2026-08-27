"""Unit tests for `agent config <id>`.

Covers format-aware show/get/set/unset across jsonc/json5 (textual edits
preserve comments), yaml, toml, and the read-only markdown case, plus the
CLI surface (`agent config hermes`, --key, key=value, --unset, --json).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent import agent_config as ac
from astroai_lab.cli.main import app
from astroai_lab.errors import LabError

runner = CliRunner()

HERMES_YAML = """# hermes config
model: nousresearch/hermes-3-llama-3.1-405b
provider: openrouter
"""

KILO_JSONC = """{
  // kilo settings
  "model": "kilo-default", // the default model
  "provider": "openrouter",
}
"""

OPENCLAW_JSON5 = """{
  // openclaw gateway
  "model": "openai/gpt-4o",
  "gateway": {
    "enabled": true,
    "port": 8080,
  },
}
"""

CODEX_TOML = """# codex config
model = "gpt-5"
model_provider = "openrouter"

[chat]
auto_send = true
"""


def _home(tmp_path: Path, agent_id: str, rel: str, content: str) -> Path:
    """Materialize an agent config file under a temp home; return home."""
    home = tmp_path / "home"
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return home


def _materialize(home: Path, rel: str, content: str) -> None:
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Path / format resolution
# ---------------------------------------------------------------------------


def test_agent_config_path_resolves_tilde(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    path = ac.agent_config_path("hermes", home=home)
    assert path == home / ".hermes" / "config.yaml"


def test_agent_config_path_unknown_agent() -> None:
    with pytest.raises(LabError, match="Unknown agent"):
        ac.agent_config_path("not-an-agent")


def test_config_format_declared(tmp_path: Path) -> None:
    assert ac.config_format("hermes") == "yaml"
    assert ac.config_format("openclaw") == "json5"
    assert ac.config_format("kilo") == "jsonc"
    assert ac.config_format("codex") == "toml"
    assert ac.config_format("cline") == "markdown"


# ---------------------------------------------------------------------------
# Read + get
# ---------------------------------------------------------------------------


def test_read_yaml(tmp_path: Path) -> None:
    home = _home(tmp_path, "hermes", ".hermes/config.yaml", HERMES_YAML)
    path, data = ac.read_agent_config("hermes", home=home)
    assert data["model"] == "nousresearch/hermes-3-llama-3.1-405b"
    assert data["provider"] == "openrouter"


def test_read_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    # Block setup so a truly missing config still errors (no silent invent).
    monkeypatch.setattr(
        "astroai_lab.agent.registry.setup_registry_agent",
        lambda *a, **k: {"ok": True, "errors": [], "actions": [], "agent": "hermes"},
    )
    with pytest.raises(LabError, match="config not found"):
        ac.read_agent_config("hermes", home=home)


def test_read_jsonc_tolerates_comments(tmp_path: Path) -> None:
    home = _home(tmp_path, "kilo", ".config/kilo/kilo.jsonc", KILO_JSONC)
    _, data = ac.read_agent_config("kilo", home=home)
    assert data["model"] == "kilo-default"
    assert data["provider"] == "openrouter"


def test_read_broken_json_raises(tmp_path: Path) -> None:
    home = _home(tmp_path, "kilo", ".config/kilo/kilo.jsonc", '{\n  "model": [unclosed\n')
    with pytest.raises(LabError, match="Cannot parse"):
        ac.read_agent_config("kilo", home=home)


def test_read_markdown_readonly(tmp_path: Path) -> None:
    home = _home(tmp_path, "cline", ".config/cline/cline-notes.md", "# notes\n")
    path, data = ac.read_agent_config("cline", home=home)
    assert path.name == "cline-notes.md"
    assert data == {}


def test_read_missing_cline_auto_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Older installs skipped setup — first `agent config cline` seeds notes."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_agent_plugins",
        lambda *a, **k: [],
    )
    path, data = ac.read_agent_config("cline", home=home)
    assert path.is_file()
    assert "Cline on CANFAR" in path.read_text(encoding="utf-8")
    assert data == {}


def test_read_missing_hermes_still_errors_when_setup_cannot_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    # hermes has no config bundle that writes config.yaml — scaffold creates it.
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_agent_plugins",
        lambda *a, **k: [],
    )
    path, data = ac.read_agent_config("hermes", home=home)
    assert path.is_file()
    assert isinstance(data, dict)


def test_get_config_value_dotted(tmp_path: Path) -> None:
    home = _home(tmp_path, "openclaw", ".openclaw/openclaw.json", OPENCLAW_JSON5)
    value, found = ac.get_config_value("openclaw", "gateway.port", home=home)
    assert found and value == 8080
    _, found = ac.get_config_value("openclaw", "gateway.missing", home=home)
    assert not found


def test_parse_value_literals() -> None:
    assert ac.parse_value("42") == 42
    assert ac.parse_value("true") is True
    assert ac.parse_value('"quoted"') == "quoted"
    assert ac.parse_value("plain-string") == "plain-string"


# ---------------------------------------------------------------------------
# JSONC / JSON5 textual edits (comments + trailing commas survive)
# ---------------------------------------------------------------------------


def test_set_jsonc_existing_key_preserves_comments(tmp_path: Path) -> None:
    home = _home(tmp_path, "kilo", ".config/kilo/kilo.jsonc", KILO_JSONC)
    actions = ac.edit_agent_config("kilo", home=home, set_items={"model": "kilo-new"})
    assert actions == [{"key": "model", "status": "set", "detail": "kilo-new"}]
    text = (home / ".config/kilo/kilo.jsonc").read_text(encoding="utf-8")
    assert "// kilo settings" in text  # comment preserved
    assert '"model": "kilo-new"' in text
    assert text.count('"model"') == 1  # replaced, not duplicated
    assert '"provider": "openrouter"' in text
    # still parses
    _, data = ac.read_agent_config("kilo", home=home)
    assert data["model"] == "kilo-new"
    assert data["provider"] == "openrouter"


def test_set_jsonc_insert_new_top_level(tmp_path: Path) -> None:
    home = _home(tmp_path, "kilo", ".config/kilo/kilo.jsonc", KILO_JSONC)
    ac.edit_agent_config("kilo", home=home, set_items={"temperature": 0.7})
    text = (home / ".config/kilo/kilo.jsonc").read_text(encoding="utf-8")
    assert '"temperature": 0.7' in text
    _, data = ac.read_agent_config("kilo", home=home)
    assert data["temperature"] == 0.7


def test_set_jsonc_insert_dotted_nested(tmp_path: Path) -> None:
    home = _home(tmp_path, "openclaw", ".openclaw/openclaw.json", OPENCLAW_JSON5)
    ac.edit_agent_config("openclaw", home=home, set_items={"gateway.timeout": 120})
    text = (home / ".openclaw/openclaw.json").read_text(encoding="utf-8")
    assert '"timeout": 120' in text
    _, data = ac.read_agent_config("openclaw", home=home)
    assert data["gateway"]["timeout"] == 120


def test_set_jsonc_insert_missing_root_creates_nested(tmp_path: Path) -> None:
    home = _home(tmp_path, "openclaw", ".openclaw/openclaw.json", "{}\n")
    ac.edit_agent_config("openclaw", home=home, set_items={"gateway.port": 9090})
    _, data = ac.read_agent_config("openclaw", home=home)
    assert data == {"gateway": {"port": 9090}}


def test_set_jsonc_dict_value(tmp_path: Path) -> None:
    home = _home(tmp_path, "openclaw", ".openclaw/openclaw.json", OPENCLAW_JSON5)
    ac.edit_agent_config(
        "openclaw",
        home=home,
        set_items={"server": {"command": "astroai", "args": ["mcp", "serve"]}},
    )
    _, data = ac.read_agent_config("openclaw", home=home)
    assert data["server"]["args"] == ["mcp", "serve"]


def test_unset_jsonc_middle_entry(tmp_path: Path) -> None:
    home = _home(tmp_path, "kilo", ".config/kilo/kilo.jsonc", KILO_JSONC)
    ac.edit_agent_config("kilo", home=home, unsets=["provider"])
    text = (home / ".config/kilo/kilo.jsonc").read_text(encoding="utf-8")
    assert "provider" not in text
    _, data = ac.read_agent_config("kilo", home=home)
    assert "provider" not in data


def test_unset_jsonc_last_entry_no_dangling_comma(tmp_path: Path) -> None:
    home = _home(
        tmp_path,
        "kilo",
        ".config/kilo/kilo.jsonc",
        '{\n  "model": "kilo-default",\n  "provider": "openrouter"\n}\n',
    )
    ac.edit_agent_config("kilo", home=home, unsets=["provider"])
    text = (home / ".config/kilo/kilo.jsonc").read_text(encoding="utf-8")
    assert '"provider"' not in text
    assert "openrouter" not in text
    _, data = ac.read_agent_config("kilo", home=home)
    assert data == {"model": "kilo-default"}


def test_edit_dry_run_writes_nothing(tmp_path: Path) -> None:
    home = _home(tmp_path, "kilo", ".config/kilo/kilo.jsonc", KILO_JSONC)
    actions = ac.edit_agent_config("kilo", home=home, set_items={"model": "x"}, dry_run=True)
    assert actions[0]["status"] == "would_set"
    assert '"model": "kilo-default"' in (home / ".config/kilo/kilo.jsonc").read_text()


# ---------------------------------------------------------------------------
# YAML edits
# ---------------------------------------------------------------------------


def test_set_yaml(tmp_path: Path) -> None:
    home = _home(tmp_path, "hermes", ".hermes/config.yaml", HERMES_YAML)
    ac.edit_agent_config("hermes", home=home, set_items={"model": "new-model"})
    _, data = ac.read_agent_config("hermes", home=home)
    assert data["model"] == "new-model"
    assert data["provider"] == "openrouter"


def test_set_yaml_insert_dotted(tmp_path: Path) -> None:
    home = _home(tmp_path, "hermes", ".hermes/config.yaml", "model: x\n")
    ac.edit_agent_config("hermes", home=home, set_items={"gateway.port": 9000})
    _, data = ac.read_agent_config("hermes", home=home)
    assert data["gateway"]["port"] == 9000


def test_unset_yaml(tmp_path: Path) -> None:
    home = _home(tmp_path, "hermes", ".hermes/config.yaml", HERMES_YAML)
    ac.edit_agent_config("hermes", home=home, unsets=["provider"])
    _, data = ac.read_agent_config("hermes", home=home)
    assert "provider" not in data


# ---------------------------------------------------------------------------
# TOML edits (codex)
# ---------------------------------------------------------------------------


def test_set_toml_top_level(tmp_path: Path) -> None:
    home = _home(tmp_path, "codex", ".codex/config.toml", CODEX_TOML)
    ac.edit_agent_config("codex", home=home, set_items={"model": "gpt-6"})
    text = (home / ".codex/config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-6"' in text
    _, data = ac.read_agent_config("codex", home=home)
    assert data["model"] == "gpt-6"


def test_set_toml_table_key(tmp_path: Path) -> None:
    home = _home(tmp_path, "codex", ".codex/config.toml", CODEX_TOML)
    ac.edit_agent_config("codex", home=home, set_items={"chat.auto_send": False})
    _, data = ac.read_agent_config("codex", home=home)
    assert data["chat"]["auto_send"] is False


def test_set_toml_new_table(tmp_path: Path) -> None:
    home = _home(tmp_path, "codex", ".codex/config.toml", "# empty\n")
    ac.edit_agent_config("codex", home=home, set_items={"chat.auto_send": True})
    _, data = ac.read_agent_config("codex", home=home)
    assert data["chat"]["auto_send"] is True


def test_set_toml_complex_value_raises(tmp_path: Path) -> None:
    home = _home(tmp_path, "codex", ".codex/config.toml", CODEX_TOML)
    with pytest.raises(LabError, match="scalar"):
        ac.edit_agent_config("codex", home=home, set_items={"model": {"a": 1}})


def test_unset_toml(tmp_path: Path) -> None:
    home = _home(tmp_path, "codex", ".codex/config.toml", CODEX_TOML)
    ac.edit_agent_config("codex", home=home, unsets=["model"])
    text = (home / ".codex/config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5"' not in text  # the model line is gone
    assert 'model_provider = "openrouter"' in text  # sibling key untouched
    _, data = ac.read_agent_config("codex", home=home)
    assert "model" not in data
    assert data["model_provider"] == "openrouter"


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_config_show_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize(tmp_path, ".hermes/config.yaml", HERMES_YAML)
    result = runner.invoke(app, ["--json", "agent", "config", "hermes"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["agent"] == "hermes"
    assert data["format"] == "yaml"
    assert data["data"]["model"] == "nousresearch/hermes-3-llama-3.1-405b"


def test_cli_config_key_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize(tmp_path, ".hermes/config.yaml", HERMES_YAML)
    result = runner.invoke(app, ["--json", "agent", "config", "hermes", "--key", "model"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["value"] == "nousresearch/hermes-3-llama-3.1-405b"


def test_cli_config_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize(tmp_path, ".hermes/config.yaml", HERMES_YAML)
    result = runner.invoke(app, ["--json", "agent", "config", "hermes", "model=new-model"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["actions"][0]["status"] == "set"
    assert "new-model" in (tmp_path / ".hermes/config.yaml").read_text()


def test_cli_config_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _materialize(tmp_path, ".hermes/config.yaml", HERMES_YAML)
    result = runner.invoke(app, ["--json", "agent", "config", "hermes", "--unset", "provider"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["actions"][0]["status"] == "unset"
    assert "provider" not in (tmp_path / ".hermes/config.yaml").read_text()


def test_cli_config_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # Setup would scaffold hermes; stub it so "missing" stays missing.
    monkeypatch.setattr(
        "astroai_lab.agent.registry.setup_registry_agent",
        lambda *a, **k: {"ok": True, "errors": [], "actions": [], "agent": "hermes"},
    )
    result = runner.invoke(app, ["--json", "agent", "config", "hermes"])
    assert result.exit_code == 1
    assert "config not found" in json.loads(result.stdout)["errors"][0]


def test_cli_config_codewhale_seeds_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_agent_plugins",
        lambda *a, **k: [],
    )
    result = runner.invoke(app, ["agent", "config", "codewhale"])
    assert result.exit_code == 0, result.output
    cfg = tmp_path / ".codewhale" / "config.toml"
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert 'provider = "openrouter"' in text
    assert "OPENROUTER_API_KEY" in text


def test_cli_config_pi_seeds_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_agent_plugins",
        lambda *a, **k: [],
    )
    result = runner.invoke(app, ["agent", "config", "pi"])
    assert result.exit_code == 0, result.output
    settings = tmp_path / ".pi" / "agent" / "settings.json"
    assert settings.is_file()
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data.get("defaultProvider") == "openrouter"
    auth = tmp_path / ".pi" / "agent" / "auth.json"
    assert auth.is_file()
    auth_data = json.loads(auth.read_text(encoding="utf-8"))
    assert auth_data["openrouter"]["key"] == "sk-or-test-key"


def test_cli_install_cline_runs_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: install printed `agent config cline` but never wrote notes."""
    from astroai_lab.cli import agent_cmd as agent_cmd_mod

    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(agent_cmd_mod, "user_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(
        "astroai_lab.agent.install.refuse_if_home_owned", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "astroai_lab.agent.registry._install_npm",
        lambda agent: (bin_dir / "cline").write_text("#!/bin/sh\n") or "cline",
    )
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_agent_plugins",
        lambda *a, **k: [],
    )
    # cline is registry-only (not in TOOLS) — install_registry_agent path.
    monkeypatch.setattr(
        "astroai_lab.agent.install.TOOLS",
        {k: v for k, v in __import__("astroai_lab.agent.install", fromlist=["TOOLS"]).TOOLS.items() if k != "cline"},
        raising=False,
    )

    result = runner.invoke(app, ["--yes", "agent", "install", "cline"])
    assert result.exit_code == 0, result.output
    notes = home / ".config" / "cline" / "cline-notes.md"
    assert notes.is_file(), result.output
    assert "Cline on CANFAR" in notes.read_text(encoding="utf-8")
    assert "config:" in result.output or "cline-notes" in result.output

