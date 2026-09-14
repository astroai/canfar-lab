"""Unit tests for AstroAI Studio launcher helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab import studio as studio_mod
from astroai_lab.cli.main import app

runner = CliRunner()


def test_detect_profile_laptop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("skaha_sessionid", raising=False)
    monkeypatch.delenv("SKAHA_SESSIONID", raising=False)
    monkeypatch.delenv("ASTROAI_SESSION_KIND", raising=False)
    assert studio_mod.detect_profile() == "laptop"


def test_detect_profile_canfar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("skaha_sessionid", "abc")
    assert studio_mod.detect_profile() == "canfar"


def test_prepare_studio_writes_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.agent.review_bench.ensure_review_bench",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "astroai_lab.agent.review_bench.ensure_dsh_dotenv",
        lambda *a, **k: {},
    )
    monkeypatch.setattr(
        "astroai_lab.agent.review_bench.ensure_dsh_settings",
        lambda *a, **k: None,
    )
    result = studio_mod.prepare_studio(home=home, profile="laptop")
    assert result["ok"] is True
    assert result["profile"] == "laptop"
    profile = home / ".dsh" / "studio-profile.yaml"
    assert profile.is_file()
    text = profile.read_text(encoding="utf-8")
    assert "profile: laptop" in text
    assert "bash_timeout_sec:" in text
    patch = home / ".dsh" / "cordis.patch.yml"
    assert patch.is_file()
    patch_text = patch.read_text(encoding="utf-8")
    assert "bash-sandbox" in patch_text
    assert "timeoutMs: 600000" in patch_text
    # Idempotent refresh when switching profile.
    result2 = studio_mod.prepare_studio(home=home, profile="canfar")
    assert result2["ok"] is True
    patch_text2 = patch.read_text(encoding="utf-8")
    assert patch_text2.count("- id: bash-sandbox") == 1
    assert "timeoutMs: 300000" in patch_text2
    assert "timeoutMs: 600000" not in patch_text2


def test_studio_web_cmd_uses_npx_when_no_dsh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("astroai_lab.studio.shutil.which", lambda *_: None)
    cmd = studio_mod.studio_web_cmd(repo, port=3099, profile="laptop")
    assert cmd[0] == "npx"
    assert "--port" in cmd
    assert "3099" in cmd


def test_cli_studio_prepare_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("skaha_sessionid", raising=False)
    result = runner.invoke(app, ["--json", "--dry-run", "studio", "--prepare"])
    assert result.exit_code == 0
    assert "profile" in result.stdout


def test_cli_studio_skills() -> None:
    result = runner.invoke(app, ["studio", "--skills"])
    assert result.exit_code == 0
    assert "skills add" in result.stdout
    assert "canfar-skills" in result.stdout
