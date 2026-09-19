"""Unit tests for AstroAI Studio launcher helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab import studio as studio_mod
from astroai_lab import studio_profile as sp
from astroai_lab.cli.main import app
from astroai_lab.errors import LabError

runner = CliRunner()


def test_detect_profile_laptop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("skaha_sessionid", raising=False)
    monkeypatch.delenv("SKAHA_SESSIONID", raising=False)
    monkeypatch.delenv("ASTROAI_SESSION_KIND", raising=False)
    assert studio_mod.detect_profile() == "laptop"


def test_detect_profile_canfar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("skaha_sessionid", "abc")
    assert studio_mod.detect_profile() == "canfar"


def _stub_bench(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.agent.review_bench.ensure_review_bench", lambda *a, **k: False)
    monkeypatch.setattr("astroai_lab.agent.review_bench.ensure_dsh_dotenv", lambda *a, **k: {})
    monkeypatch.setattr("astroai_lab.agent.review_bench.ensure_dsh_settings", lambda *a, **k: None)


def test_prepare_studio_writes_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DSH_HOME", raising=False)
    _stub_bench(monkeypatch)
    result = studio_mod.prepare_studio(home=home, profile="laptop", install_bundles=False)
    assert result["ok"] is True
    assert result["profile"] == "laptop"

    profile = sp.managed_studio_dir(home) / "studio-profile.yaml"
    assert profile.is_file()
    text = profile.read_text(encoding="utf-8")
    assert "profile: laptop" in text
    assert "bash_timeout_sec:" in text

    patch = home / ".dsh" / "cordis.patch.yml"
    assert patch.is_file()
    patch_text = patch.read_text(encoding="utf-8")
    assert "bash-sandbox" in patch_text
    assert "timeoutMs: 600000" in patch_text

    # The owned dsh profile is written in full, from the shipped web template.
    assert result["dsh_profile"] == sp.STUDIO_PROFILE_NAME
    assert result["bundles"] == ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]
    manifest = (sp.profile_dir(home) / "package.json").read_text(encoding="utf-8")
    assert "dsh-profile-astroai" in manifest

    # Idempotent refresh when switching profile.
    result2 = studio_mod.prepare_studio(home=home, profile="canfar", install_bundles=False)
    assert result2["ok"] is True
    patch_text2 = patch.read_text(encoding="utf-8")
    assert patch_text2.count("- id: bash-sandbox") == 1
    assert "timeoutMs: 300000" in patch_text2
    assert "timeoutMs: 600000" not in patch_text2


def test_studio_web_cmd_requires_a_real_dsh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No npx shim: npm >= 10 swallows the launcher flags Studio needs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("astroai_lab.studio.shutil.which", lambda *_: None)
    monkeypatch.setattr("astroai_lab.studio._DSH_SEARCH_PATHS", ())
    monkeypatch.delenv("ASTROAI_STUDIO_DSH", raising=False)
    assert studio_mod.dsh_binary() is None
    with pytest.raises(LabError) as excinfo:
        studio_mod.studio_web_cmd(repo, port=3099, profile="laptop")
    assert "npx" in str(excinfo.value)


def test_studio_web_cmd_boots_the_owned_profile(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".dsh").mkdir(parents=True)
    (repo / ".dsh" / "cordis.patch.yml").write_text("- id: x\n", encoding="utf-8")
    cmd = studio_mod.studio_web_cmd(
        repo, port=3099, profile="laptop", dsh_bin="/opt/astroai/bin/dsh"
    )
    assert cmd[:3] == ["/opt/astroai/bin/dsh", "--profile", sp.STUDIO_PROFILE_NAME]
    assert cmd[3:5] == ["--patch", str((repo / ".dsh" / "cordis.patch.yml").resolve())]
    assert cmd[-3:] == ["--no-open", "--port", "3099"]
    # No repo patch means no --patch flag rather than a dangling path.
    bare = studio_mod.studio_web_cmd(tmp_path, profile="laptop", dsh_bin="dsh")
    assert "--patch" not in bare


def test_canfar_adds_trusted_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ASTROAI_STUDIO_TRUSTED_HOST", "ws-uv.canfar.net")
    monkeypatch.setenv("HOSTNAME", "pod-1")
    cmd = studio_mod.studio_web_cmd(repo, profile="canfar", dsh_bin="dsh")
    assert cmd.count("--trusted-host") == 2
    assert "ws-uv.canfar.net" in cmd
    # Loopback profiles must not advertise outside authorities.
    assert "--trusted-host" not in studio_mod.studio_web_cmd(repo, profile="laptop", dsh_bin="dsh")


def test_dsh_binary_prefers_the_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "dsh"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("ASTROAI_STUDIO_DSH", str(binary))
    assert studio_mod.dsh_binary() == str(binary)
    # A bogus override must not shadow a real binary on PATH.
    monkeypatch.setenv("ASTROAI_STUDIO_DSH", str(tmp_path / "nope"))
    assert studio_mod.dsh_binary() != str(tmp_path / "nope")


def test_dsh_binary_finds_scratch_managed_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_bin = tmp_path / "scratch" / ".local" / "bin"
    scratch_bin.mkdir(parents=True)
    binary = scratch_bin / "dsh"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.delenv("ASTROAI_STUDIO_DSH", raising=False)
    monkeypatch.setenv("ASTROAI_LAB_BIN_DIR", str(scratch_bin))
    monkeypatch.setenv("SCRATCH", str(tmp_path / "scratch"))
    monkeypatch.setattr(studio_mod.shutil, "which", lambda _: None)
    assert studio_mod.dsh_binary() == str(binary)


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


def test_cli_studio_doctor_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASTROAI_STUDIO_DSH", str(tmp_path / "missing-dsh"))
    result = runner.invoke(app, ["--json", "studio", "--doctor"])
    payload = json.loads(result.stdout)
    assert payload["profile"] == "laptop"
    assert isinstance(payload["checks"], list)
    assert payload["fatal"] is True  # the stub dsh path does not exist


def test_studio_env_routes_stores_off_home_on_canfar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.delenv("DSH_HOME", raising=False)
    env = studio_mod.studio_env(profile="canfar", home=tmp_path)
    assert env["ASTROAI_STUDIO_STATE"].startswith(str(scratch))
    assert env["npm_config_store_dir"].startswith(str(scratch))
    # Laptop keeps the durable default rather than inventing a scratch.
    laptop = studio_mod.studio_env(profile="laptop", home=tmp_path)
    assert laptop["ASTROAI_STUDIO_STATE"] == str(tmp_path / ".dsh" / "state")
    assert "npm_config_store_dir" not in laptop


def test_dsh_version_pin_matches_agent_yaml() -> None:
    """Studio pin, review-bench pin, and agent install source must stay aligned."""
    from importlib import resources

    import yaml

    from astroai_lab.agent import review_bench as rb

    text = (resources.files("astroai_lab") / "data" / "agent" / "agents" / "dsh.yaml").read_text(
        encoding="utf-8"
    )
    data = yaml.safe_load(text)
    source = data["install"]["source"]
    assert source == f"@deepseek-ai/dsh@{studio_mod.DSH_VERSION}"
    assert rb.DSH_VERSION == studio_mod.DSH_VERSION
    assert studio_mod.DSH_VERSION in sp.DSH_INSTALL_HINT
