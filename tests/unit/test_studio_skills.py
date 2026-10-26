"""CANFAR prepare installs the skills the Studio assistant reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from canfar_lab.studio_skills import ensure_platform_skills


def test_prepare_installs_the_canfar_client_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "canfar_lab.studio_skills.fetch_canfar_skill_files",
        lambda **_kwargs: {
            "SKILL.md": "---\nname: canfar\n---\n# Work on the CANFAR Science Platform\n",
            "references/batch.md": "# batch\n",
            "references/data.md": "# data\n",
            "references/python.md": "# python\n",
        },
    )
    actions = ensure_platform_skills(tmp_path)
    skill = (tmp_path / ".agents" / "skills" / "canfar" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: canfar" in skill
    assert (tmp_path / ".agents" / "skills" / "canfar" / "references" / "data.md").is_file()
    assert any(line.startswith("skill canfar →") for line in actions)


def test_fetch_failure_keeps_the_session_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("canfar_lab.studio_skills.fetch_canfar_skill_files", lambda **_kwargs: None)
    actions = ensure_platform_skills(tmp_path)
    text = (tmp_path / ".agents" / "skills" / "canfar-session" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "/scratch" in text
    assert "/arc/home" in text
    assert not (tmp_path / ".agents" / "skills" / "canfar").exists()
    assert any(line.startswith("WARN canfar skill") for line in actions)
