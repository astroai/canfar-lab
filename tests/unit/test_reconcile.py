"""Unit tests for agent state reconciliation (verify --fix)."""

from __future__ import annotations

from pathlib import Path

import pytest

from astroai_lab.agent import reconcile
from astroai_lab.agent.reconcile import (
    drift_issues,
    is_managed_skill_dir,
    packaged_skill_names,
    reconcile_all,
    reconcile_skills,
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def _skill(path: Path, name: str, *, marker: bool = False) -> Path:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\nbody\n")
    if marker:
        (path / reconcile.MARKER).write_text("")
    return path


def test_managed_detection_by_prefix_and_marker(tmp_path: Path) -> None:
    assert is_managed_skill_dir(_skill(tmp_path / "astroai-old", "astroai-old"))
    assert is_managed_skill_dir(_skill(tmp_path / "canfar-x", "canfar-x"))
    assert is_managed_skill_dir(_skill(tmp_path / "renamed", "totally-new", marker=True))
    user = _skill(tmp_path / "my-own-skill", "my-own-skill")
    assert not is_managed_skill_dir(user)
    assert not is_managed_skill_dir(tmp_path / "does-not-exist")


def test_reconcile_removes_obsolete_but_never_user_skills(home: Path) -> None:
    skills = home / ".cursor" / "skills"
    obsolete = _skill(skills / "astroai-ancient", "astroai-ancient")
    shipped = _skill(skills / "astroai-lab-workflow", "astroai-lab-workflow")
    user = _skill(skills / "my-research-hacks", "my-research-hacks")

    results = reconcile_skills(home)

    assert not obsolete.exists(), "obsolete managed skill must be removed"
    assert shipped.exists(), "shipped skill must survive (it refreshes in place)"
    assert user.exists(), "user skills must never be touched"
    removed = [r for r in results if r["status"] == "removed"]
    assert any("astroai-ancient" in r["target"] for r in removed)


def test_drift_issues_reports_obsolete_and_stale_paths(home: Path) -> None:
    _skill(home / ".cursor" / "skills" / "astroai-ancient", "astroai-ancient")
    mcp = home / ".cursor" / "mcp.json"
    mcp.parent.mkdir(parents=True, exist_ok=True)
    mcp.write_text(
        '{"mcpServers": {"ghost": {"command": "/nonexistent/bin/ghost-mcp"}}}',
        encoding="utf-8",
    )

    drift = drift_issues(home)

    assert any("astroai-ancient" in d for d in drift)
    assert any("ghost" in d for d in drift)


def test_reconcile_mcp_paths_rewrites_missing_binary_to_path(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import os

    from astroai_lab.agent.agent_targets import MCP_TARGETS

    assert MCP_TARGETS["cursor"].relpath == ".cursor/mcp.json"
    mcp = home / ".cursor" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    fake_on_path = home / "path-bin" / "ghost-mcp"
    fake_on_path.parent.mkdir(parents=True)
    fake_on_path.write_text("#!/bin/sh\n")
    fake_on_path.chmod(0o755)
    mcp.write_text(
        '{"mcpServers": {'
        '"gone": {"command": "/old/dead/location/ghost-mcp"}, '
        '"alive": {"command": "python"}'
        "}}",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", f"{fake_on_path.parent}:{os.environ['PATH']}")

    results = reconcile.reconcile_mcp_paths(home)

    rewritten = [r for r in results if r["target"] == "cursor:gone"]
    assert rewritten and rewritten[0]["status"] == "rewrote-path"
    data = json.loads(mcp.read_text(encoding="utf-8"))
    assert data["mcpServers"]["gone"]["command"] == str(fake_on_path)


def test_dry_run_changes_nothing(home: Path) -> None:
    _skill(home / ".cursor" / "skills" / "astroai-ancient", "astroai-ancient")

    results = reconcile_all(home, dry_run=True)

    assert (home / ".cursor" / "skills" / "astroai-ancient").exists()
    assert all(r["status"].startswith("would_") for rows in results.values() for r in rows)


def test_packaged_names_include_bundle_and_plugin_skills() -> None:
    names = packaged_skill_names()

    assert "astroai-lab-workflow" in names  # bundle skill
    assert "astroai-ray" in names  # bundled plugin skill
