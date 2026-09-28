"""OpenScience config merge (~/.config/openscience/openscience.json)."""

from __future__ import annotations

import json
from pathlib import Path

from canfar_lab.agent.bundle_path import bundle_root
from canfar_lab.agent.openscience import (
    CONFIG_REL,
    PRIMER_REL,
    ensure_openscience_config,
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
    assert actions == ["openscience:primer", "openscience:config"]
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
    assert actions == ["openscience:primer", "openscience:config"]
    assert not (tmp_path / CONFIG_REL).exists()


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
    assert agent["install"] == {"method": "npm", "source": "@synsci/openscience@2.0.140"}


def test_every_bundled_skill_has_frontmatter() -> None:
    skills = sorted((bundle_root() / "openscience" / "skills").glob("*/SKILL.md"))
    assert len(skills) == 6
    for path in skills:
        head = path.read_text().split("---", 2)
        assert head[0] == ""
        assert f"name: {path.parent.name}\n" in head[1]
        assert "description: " in head[1]
