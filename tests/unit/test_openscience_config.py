"""OpenScience config merge (~/.config/openscience/openscience.json)."""

from __future__ import annotations

import json
from pathlib import Path

from canfar_lab.agent.bundle_path import bundle_root
from canfar_lab.agent.openscience import (
    CONFIG_DIR_REL,
    CONFIG_REL,
    PRIMER_REL,
    STAMP_NAME,
    ensure_openscience_config,
    file_digest,
    merge_config,
)
from canfar_lab.agent.registry import get_registry_agent
from canfar_lab.agent.setup import default_bundle_names, run_bundle

CMD = ("/opt/astroai/venv/cadc/bin/canfar-lab", "mcp", "serve")


def _read(home: Path) -> dict:
    return json.loads((home / CONFIG_REL).read_text())


def test_fresh_home_gets_mcp_primer_skills_and_approvals(tmp_path: Path) -> None:
    root = bundle_root()
    actions = ensure_openscience_config(root, tmp_path, force=False, dry_run=False, mcp_command=CMD)
    assert actions == ["openscience:primer", "openscience:files", "openscience:config"]
    cfg = _read(tmp_path)
    assert cfg["autoupdate"] is False
    assert cfg["mcp"]["astroai"] == {
        "type": "local",
        "command": list(CMD),
        "enabled": True,
        "timeout": 600_000,
    }
    assert cfg["instructions"] == [str(tmp_path / PRIMER_REL)]
    assert "/arc/home" in (tmp_path / PRIMER_REL).read_text()
    skills_dir = Path(cfg["skills"]["paths"][0])
    assert (skills_dir / "cadc-archive-search" / "SKILL.md").is_file()
    assert cfg["skills"]["paths"][1] == "~/.agents/skills"
    assert cfg["permission"]["mcp"]["astroai_cluster_start"] == "ask"
    assert cfg["permission"]["mcp"]["astroai_job_submit"] == "ask"
    assert "astroai_resolve_target" not in cfg["permission"]["mcp"]
    assert cfg["sandbox"] == {"enabled": False, "onUnavailable": "warn"}


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    root = bundle_root()
    kwargs = {"force": False, "dry_run": False, "mcp_command": CMD}
    ensure_openscience_config(root, tmp_path, **kwargs)
    assert ensure_openscience_config(root, tmp_path, **kwargs) == []


def test_user_choices_survive_and_mcp_command_follows_cli(tmp_path: Path) -> None:
    cfg_path = tmp_path / CONFIG_REL
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        """{
  // user edits (JSONC)
  "model": "anthropic/claude-sonnet-4-5",
  "autoupdate": true,
  "mcp": {
    "astroai": {"type": "local", "command": ["old"]},
    "mine": {"type": "local", "command": ["x"]}
  },
  "instructions": ["~/notes.md"],
  "permission": {"mcp": {"astroai_job_submit": "allow"}, "bash": "ask"},
}
"""
    )
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    cfg = _read(tmp_path)
    assert cfg["model"] == "anthropic/claude-sonnet-4-5"
    assert cfg["autoupdate"] is True
    assert cfg["mcp"]["astroai"]["command"] == list(CMD)
    assert cfg["mcp"]["mine"] == {"type": "local", "command": ["x"]}
    assert cfg["instructions"][0] == "~/notes.md"
    assert cfg["permission"]["mcp"]["astroai_job_submit"] == "allow"
    assert cfg["permission"]["bash"] == "ask"
    assert cfg["permission"]["mcp"]["astroai_cluster_start"] == "ask"
    assert (cfg_path.with_name("openscience.json.bak")).read_text().startswith("{\n  // user")


def test_force_restores_managed_defaults() -> None:
    managed = {
        "autoupdate": False,
        "mcp": {"astroai": {"type": "local", "command": ["c"]}},
        "instructions": ["p"],
        "skills": {"paths": ["s"]},
        "permission": {"mcp": {"astroai_job_submit": "ask"}},
        "sandbox": {"enabled": False, "onUnavailable": "warn"},
    }
    out = merge_config(
        {"autoupdate": True, "permission": {"mcp": {"astroai_job_submit": "allow"}}},
        managed,
        force=True,
    )
    assert out["autoupdate"] is False
    assert out["permission"]["mcp"]["astroai_job_submit"] == "ask"
    assert out["sandbox"] == {"enabled": False, "onUnavailable": "warn"}


def test_global_permission_string_is_left_alone() -> None:
    managed = {
        "autoupdate": False,
        "mcp": {"astroai": {}},
        "instructions": [],
        "skills": {"paths": []},
        "permission": {"mcp": {"astroai_job_submit": "ask"}},
        "sandbox": {"enabled": False, "onUnavailable": "warn"},
    }
    assert merge_config({"permission": "allow"}, managed, force=False)["permission"] == "allow"
    mcp_string = merge_config({"permission": {"mcp": "allow"}}, managed, force=False)
    assert mcp_string["permission"]["mcp"] == "allow"


def test_user_sandbox_choice_survives(tmp_path: Path) -> None:
    cfg_path = tmp_path / CONFIG_REL
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"sandbox": {"enabled": true, "allowWrite": ["/arc/projects/x"]}}')
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    assert _read(tmp_path)["sandbox"] == {
        "enabled": True,
        "allowWrite": ["/arc/projects/x"],
        "onUnavailable": "warn",
    }


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    actions = ensure_openscience_config(
        bundle_root(), tmp_path, force=False, dry_run=True, mcp_command=CMD
    )
    assert actions == ["openscience:primer", "openscience:files", "openscience:config"]
    assert not (tmp_path / CONFIG_REL).exists()
    assert not (tmp_path / CONFIG_DIR_REL / "agent").exists()


def test_bundle_is_in_default_setup_and_registry(tmp_path: Path, monkeypatch) -> None:
    root = bundle_root()
    assert "openscience" in default_bundle_names(root)
    monkeypatch.setattr(
        "canfar_lab.studio_profile.mcp_serve_command", lambda **_: CMD, raising=True
    )
    run_bundle("openscience", root, tmp_path, None, force=False, dry_run=False)
    assert _read(tmp_path)["mcp"]["astroai"]["command"] == list(CMD)
    agent = get_registry_agent("openscience")
    assert agent is not None
    assert agent["install"] == {"method": "npm", "source": "@synsci/openscience@2.0.143"}


def test_every_bundled_skill_has_frontmatter() -> None:
    skills = sorted((bundle_root() / "openscience" / "skills").glob("*/SKILL.md"))
    assert len(skills) == 16
    for path in skills:
        head = path.read_text().split("---", 2)
        assert head[0] == ""
        assert f"name: {path.parent.name}\n" in head[1]
        assert "description: " in head[1]
        assert "summary: " in head[1]
        assert "category: astronomy\n" in head[1]


def test_astronomy_agent_and_commands_are_installed(tmp_path: Path) -> None:
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    cfg_dir = tmp_path / CONFIG_DIR_REL
    agent = (cfg_dir / "agent" / "astronomy.md").read_text()
    front = agent.split("---", 2)[1]
    assert "mode: subagent\n" in front
    assert "skills: [astronomy, physics, visualization]\n" in front
    assert "{{DOMAIN_SKILLS}}" in agent and "{{SCIENCE}}" in agent
    commands = sorted(p.stem for p in (cfg_dir / "command").glob("*.md"))
    assert commands == ["astro-check", "find-data", "save-results", "scale-out"]
    for name in commands:
        text = (cfg_dir / "command" / f"{name}.md").read_text()
        assert "description: " in text.split("---", 2)[1]
        assert "$ARGUMENTS" in text


def test_managed_files_keep_user_edits_and_follow_the_bundle(tmp_path: Path) -> None:
    root = bundle_root()
    kwargs = {"dry_run": False, "mcp_command": CMD}
    ensure_openscience_config(root, tmp_path, force=False, **kwargs)
    cfg_dir = tmp_path / CONFIG_DIR_REL
    agent = cfg_dir / "agent" / "astronomy.md"
    command = cfg_dir / "command" / "find-data.md"
    shipped = agent.read_text()

    agent.write_text(shipped + "\nMy own rule.\n")
    assert "openscience:files" not in ensure_openscience_config(
        root, tmp_path, force=False, **kwargs
    )
    assert agent.read_text().endswith("My own rule.\n")

    # An unedited file we installed earlier follows a newer bundle.
    stamp = json.loads((cfg_dir / STAMP_NAME).read_text())
    command.write_text("old bundle text\n")
    stamp["command/find-data.md"] = file_digest("old bundle text\n")
    (cfg_dir / STAMP_NAME).write_text(json.dumps(stamp))
    assert "openscience:files" in ensure_openscience_config(root, tmp_path, force=False, **kwargs)
    assert command.read_text() != "old bundle text\n"

    # Files we no longer ship are removed only while unedited.
    retired = cfg_dir / "command" / "retired.md"
    retired.write_text("x\n")
    stamp = json.loads((cfg_dir / STAMP_NAME).read_text())
    stamp["command/retired.md"] = file_digest("x\n")
    (cfg_dir / STAMP_NAME).write_text(json.dumps(stamp))
    ensure_openscience_config(root, tmp_path, force=False, **kwargs)
    assert not retired.exists()

    ensure_openscience_config(root, tmp_path, force=True, **kwargs)
    assert agent.read_text() == shipped


def test_user_file_with_a_managed_name_is_not_replaced(tmp_path: Path) -> None:
    agent = tmp_path / CONFIG_DIR_REL / "agent" / "astronomy.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\ndescription: mine\n---\nmine\n")
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    assert agent.read_text() == "---\ndescription: mine\n---\nmine\n"


def test_llm_endpoint_from_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTROAI_LLM_BASE_URL", "http://vllm.example:8000/v1")
    monkeypatch.setenv("ASTROAI_LLM_MODELS", "qwen3-32b, gpt-oss-120b")
    monkeypatch.setenv("ASTROAI_LLM_API_KEY", "secret")
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    provider = _read(tmp_path)["provider"]["canfar"]
    assert provider == {
        "npm": "@ai-sdk/openai-compatible",
        "name": "CANFAR LLM endpoint",
        "options": {
            "baseURL": "http://vllm.example:8000/v1",
            "apiKey": "{env:ASTROAI_LLM_API_KEY}",
        },
        "models": {"qwen3-32b": {"name": "qwen3-32b"}, "gpt-oss-120b": {"name": "gpt-oss-120b"}},
    }
    assert "secret" not in (tmp_path / CONFIG_REL).read_text()

    for name in ("ASTROAI_LLM_BASE_URL", "ASTROAI_LLM_MODELS", "ASTROAI_LLM_API_KEY"):
        monkeypatch.delenv(name)
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    assert "canfar" not in _read(tmp_path).get("provider", {})


def test_llm_endpoint_from_lab_dotenv_without_key(tmp_path: Path, monkeypatch) -> None:
    for name in ("ASTROAI_LLM_BASE_URL", "ASTROAI_LLM_MODELS", "ASTROAI_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".astroai" / "lab" / ".env"
    dotenv.parent.mkdir(parents=True)
    dotenv.write_text("ASTROAI_LLM_BASE_URL=http://10.0.0.5:8000/v1\nASTROAI_LLM_MODELS=llama\n")
    cfg_path = tmp_path / CONFIG_REL
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"provider": {"mine": {"npm": "x"}}}')
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    providers = _read(tmp_path)["provider"]
    assert providers["mine"] == {"npm": "x"}
    assert providers["canfar"]["options"] == {
        "baseURL": "http://10.0.0.5:8000/v1",
        "apiKey": "canfar",
    }


def test_user_provider_named_canfar_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    for name in ("ASTROAI_LLM_BASE_URL", "ASTROAI_LLM_MODELS", "ASTROAI_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    cfg_path = tmp_path / CONFIG_REL
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"provider": {"canfar": {"name": "my own"}}}')
    ensure_openscience_config(bundle_root(), tmp_path, force=False, dry_run=False, mcp_command=CMD)
    assert _read(tmp_path)["provider"]["canfar"] == {"name": "my own"}
