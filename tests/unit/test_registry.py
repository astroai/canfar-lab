"""Unit tests for the agent registry.

Covers loader + schema validation, status detection, verify issues
(installed-only gating), install dispatch, and catalog/list integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.registry import (
    fix_registry_agent,
    get_registry_agent,
    install_registry_agent,
    list_installed_registry_agents,
    list_registry_agents,
    load_registry,
    registry_agent_status,
    registry_ids,
    registry_verify_issues,
    setup_registry_agent,
    update_registry_agent,
)
from astroai_lab.cli.main import app
from astroai_lab.errors import LabError

runner = CliRunner()


def _write_agent_yaml(root: Path, name: str, body: str) -> Path:
    agents = root / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    path = agents / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Loader + schema validation
# ---------------------------------------------------------------------------


def test_load_registry_hermes_openclaw() -> None:
    agents = load_registry()
    ids = [a["id"] for a in agents]
    assert "hermes" in ids
    assert "openclaw" in ids
    assert ids == sorted(ids)  # sorted by id


def test_load_registry_includes_migrated_agents() -> None:
    """kilo/goose/cline/opencode/codex/cursor migrated from install.TOOLS."""
    ids = {a["id"] for a in load_registry()}
    assert {"kilo", "goose", "cline", "opencode", "codex", "cursor"} <= ids
    kilo = get_registry_agent("kilo")
    assert kilo is not None
    assert kilo["install"]["method"] == "curl"
    assert kilo["config"]["path"] == "~/.config/kilo/kilo.jsonc"
    codex = get_registry_agent("codex")
    assert codex is not None
    assert codex["install"]["method"] == "gh-release"
    assert codex["install"].get("requires_gh_auth") is False
    assert "{arch}" in codex["install"]["asset"]


def test_load_registry_includes_cursor() -> None:
    cursor = get_registry_agent("cursor")
    assert cursor is not None
    assert cursor["binary"] == "agent"
    assert cursor["install"]["source"] == "https://cursor.com/install"


def test_load_registry_empty_dir(tmp_path: Path) -> None:
    assert load_registry(tmp_path) == []


def test_registry_ids_and_get(tmp_path: Path) -> None:
    assert "hermes" in registry_ids()
    assert get_registry_agent("openclaw") is not None
    assert get_registry_agent("not-an-agent") is None


def test_validation_missing_required_key(tmp_path: Path) -> None:
    _write_agent_yaml(
        tmp_path,
        "broken",
        "name: No ID\nhomepage: https://x\nbinary: x\ninstall:\n  method: npm\n  source: x\n",
    )
    with pytest.raises(LabError, match="missing required key"):
        load_registry(tmp_path)


def test_validation_bad_install_method(tmp_path: Path) -> None:
    _write_agent_yaml(
        tmp_path,
        "broken",
        "id: broken\nname: Broken\nhomepage: https://x\nbinary: x\ninstall:\n  method: pip\n",
    )
    with pytest.raises(LabError, match="invalid install.method"):
        load_registry(tmp_path)


def test_validation_curl_missing_source(tmp_path: Path) -> None:
    _write_agent_yaml(
        tmp_path,
        "broken",
        "id: broken\nname: Broken\nhomepage: https://x\nbinary: x\ninstall:\n  method: curl\n",
    )
    with pytest.raises(LabError, match="requires install.source"):
        load_registry(tmp_path)


def test_validation_gh_release_missing_asset(tmp_path: Path) -> None:
    _write_agent_yaml(
        tmp_path,
        "broken",
        "id: broken\nname: Broken\nhomepage: https://x\nbinary: x\n"
        "install:\n  method: gh-release\n  repo: a/b\n",
    )
    with pytest.raises(LabError, match="requires install.repo and install.asset"):
        load_registry(tmp_path)


def test_validation_bad_yaml(tmp_path: Path) -> None:
    _write_agent_yaml(tmp_path, "broken", "id: [unclosed")
    with pytest.raises(LabError, match="Invalid YAML"):
        load_registry(tmp_path)


def test_validation_config_without_path(tmp_path: Path) -> None:
    _write_agent_yaml(
        tmp_path,
        "broken",
        "id: broken\nname: Broken\nhomepage: https://x\nbinary: x\n"
        "install:\n  method: npm\n  source: x\nconfig:\n  format: json\n",
    )
    with pytest.raises(LabError, match="config requires config.path"):
        load_registry(tmp_path)


# ---------------------------------------------------------------------------
# Status detection
# ---------------------------------------------------------------------------


def test_registry_agent_status_binary_only(monkeypatch: pytest.MonkeyPatch) -> None:
    hermes = get_registry_agent("hermes")
    assert hermes is not None

    def _fake_classify(binary: str, *, home=None):
        return {
            "binary": binary,
            "path": "/tmp/hermes",
            "source": "managed",
            "managed": True,
            "home_install": False,
            "home_path": None,
        }

    monkeypatch.setattr("astroai_lab.agent.install.classify_binary", _fake_classify)
    status = registry_agent_status(hermes, home=Path("/nonexistent-home"))
    assert status["id"] == "hermes"
    assert status["binary_ok"] is True
    assert status["managed"] is True
    assert status["installed"] is False  # config not present


def test_agy_cfg_present_without_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agy login state lives under ~/.gemini/antigravity-cli, not a required settings.json."""
    agy = get_registry_agent("agy")
    assert agy is not None
    home = tmp_path / "home"
    marker = home / ".gemini" / "antigravity-cli"
    marker.mkdir(parents=True)
    (marker / "keybindings.json").write_text("{}", encoding="utf-8")

    def _fake_classify(binary: str, *, home=None):
        return {
            "binary": binary,
            "path": "/tmp/agy",
            "source": "managed",
            "managed": True,
            "home_install": False,
            "home_path": None,
        }

    monkeypatch.setattr("astroai_lab.agent.install.classify_binary", _fake_classify)
    status = registry_agent_status(agy, home=home)
    assert status["config_declared"] is True
    assert status["config_present"] is True
    assert status["config_ok"] is True
    assert not (home / ".gemini" / "antigravity-cli" / "settings.json").is_file()


def test_agy_cfg_absent_without_gemini_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agy = get_registry_agent("agy")
    assert agy is not None

    def _fake_classify(binary: str, *, home=None):
        return {
            "binary": binary,
            "path": "/tmp/agy",
            "source": "managed",
            "managed": True,
            "home_install": False,
            "home_path": None,
        }

    monkeypatch.setattr("astroai_lab.agent.install.classify_binary", _fake_classify)
    status = registry_agent_status(agy, home=tmp_path / "empty-home")
    assert status["config_present"] is False
    assert status["config_ok"] is False


def test_registry_agent_status_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    openclaw = get_registry_agent("openclaw")
    assert openclaw is not None
    home = tmp_path / "home"
    (home / ".openclaw").mkdir(parents=True)
    (home / ".openclaw" / "openclaw.json").write_text("{}", encoding="utf-8")

    def _fake_classify(binary: str, *, home=None):
        return {
            "binary": binary,
            "path": "/tmp/openclaw",
            "source": "managed",
            "managed": True,
            "home_install": False,
            "home_path": None,
        }

    monkeypatch.setattr("astroai_lab.agent.install.classify_binary", _fake_classify)
    status = registry_agent_status(openclaw, home=home)
    assert status["config_ok"] is True
    assert status["config_present"] is True
    assert status["installed"] is True
    assert status["managed"] is True


def test_probe_version_parses_semver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from astroai_lab.agent.registry import probe_version

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "slowcli"
    # Sleep 1.2s then print — must succeed with default 3s timeout (old 0.8s failed).
    fake.write_text("#!/bin/sh\nsleep 1.2\necho 'slowcli 9.8.7'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.delenv("ASTROAI_LAB_PROBE_VERSION", raising=False)
    assert probe_version("slowcli") == "9.8.7"


def test_probe_version_resolves_session_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from astroai_lab.agent.registry import probe_version

    bin_dir = tmp_path / "session-bin"
    bin_dir.mkdir()
    fake = bin_dir / "sesscli"
    fake.write_text("#!/bin/sh\necho 'sesscli 1.2.3'\n", encoding="utf-8")
    fake.chmod(0o755)

    session = SimpleNamespace(
        astroai_lab_bin_dir=bin_dir,
        astroai_lab_npm_prefix=tmp_path / "npm",
    )
    monkeypatch.setattr(
        "astroai_lab.shell.session_env.resolve_session_env",
        lambda *, ensure=False: session,  # noqa: ARG005
    )
    # Not on PATH — only session bin.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("ASTROAI_LAB_PROBE_VERSION", raising=False)
    assert probe_version("sesscli") == "1.2.3"


def test_probe_version_respects_disable_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent.registry import probe_version

    monkeypatch.setenv("ASTROAI_LAB_PROBE_VERSION", "0")
    assert probe_version("python3") is None


def test_probe_agent_version_uses_registry_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent.registry import get_registry_agent, probe_agent_version

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "freebuff"
    cli.write_text("#!/bin/sh\necho slowbuff 9.9.9\n", encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setenv("ASTROAI_LAB_BIN_DIR", str(bin_dir))
    monkeypatch.delenv("ASTROAI_LAB_PROBE_VERSION", raising=False)

    agent = get_registry_agent("freebuff")
    assert agent is not None
    assert probe_agent_version(agent) == "9.9.9"


def test_probe_agent_launch_uses_registry_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify must use the same argv as list (freebuff -v, not --version)."""
    from astroai_lab.agent.registry import get_registry_agent, probe_agent_launch

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "freebuff"
    # Fail if called with --version; succeed with -v.
    cli.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo boom; exit 1; fi\n'
        'if [ "$1" = "-v" ]; then echo freebuff 1.2.3; exit 0; fi\n'
        "exit 2\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    monkeypatch.setenv("ASTROAI_LAB_BIN_DIR", str(bin_dir))
    monkeypatch.delenv("ASTROAI_LAB_PROBE_VERSION", raising=False)

    agent = get_registry_agent("freebuff")
    assert agent is not None
    assert probe_agent_launch(agent) is None


def test_omp_cfg_detects_dot_omp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent.registry import get_registry_agent, registry_agent_status

    omp = get_registry_agent("omp")
    assert omp is not None
    home = tmp_path / "home"
    omp_root = home / ".omp" / "agent"
    omp_root.mkdir(parents=True)
    (omp_root / "config.yml").write_text("model: test\n", encoding="utf-8")

    def _fake_classify(binary: str, *, home=None):
        return {
            "binary": binary,
            "path": "/tmp/omp",
            "source": "managed",
            "managed": True,
            "home_install": False,
            "home_path": None,
        }

    monkeypatch.setattr("astroai_lab.agent.install.classify_binary", _fake_classify)
    status = registry_agent_status(omp, home=home)
    assert status["config_present"] is True


# ---------------------------------------------------------------------------
# Verify issues (installed-only gating)
# ---------------------------------------------------------------------------


def _fake_classify(
    *,
    source: str = "missing",
    managed: bool = False,
    home_install: bool = False,
    path: str | None = None,
):
    def _inner(binary: str, *, home=None):
        return {
            "binary": binary,
            "path": path,
            "source": source,
            "managed": managed,
            "home_install": home_install,
            "home_path": path if home_install else None,
        }

    return _inner


def test_verify_issues_nothing_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing managed → installed_only reports nothing (fresh image gate).
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="missing"),
    )
    assert registry_verify_issues(home=Path("/nonexistent-home"), installed_only=True) == []


def test_verify_issues_full_reports_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="missing"),
    )
    issues = registry_verify_issues(home=Path("/nonexistent-home"), installed_only=False)
    assert any("binary not found" in i and "hermes" in i for i in issues)


def test_verify_issues_installed_missing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="managed", managed=True, path="/tmp/x"),
    )
    issues = registry_verify_issues(home=home, installed_only=True)
    assert any("config missing" in i and "hermes" in i for i in issues)


# ---------------------------------------------------------------------------
# Install dispatch
# ---------------------------------------------------------------------------


def test_install_registry_agent_unknown() -> None:
    with pytest.raises(LabError, match="Unknown agent"):
        install_registry_agent("not-an-agent")


def test_install_registry_agent_tools_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    # hermes/openclaw/cursor exist in install.TOOLS → keep the battle-tested installer.
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "astroai_lab.agent.install.install_tool",
        lambda name, dry_run=False: calls.append((name, dry_run)),
    )
    monkeypatch.setattr("astroai_lab.agent.install.refuse_if_home_owned", lambda *a, **k: None)
    install_registry_agent("hermes", dry_run=True)
    assert calls == [("hermes", True)]
    calls.clear()
    install_registry_agent("cursor", dry_run=True)
    assert calls == [("cursor", True)]


def test_install_registry_agent_migrated_not_in_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Migrated agents no longer resolve through install.TOOLS."""
    from astroai_lab.agent import install as install_mod

    calls: list[str] = []
    monkeypatch.setattr(install_mod, "install_tool", lambda name, dry_run=False: calls.append(name))
    monkeypatch.setattr(
        install_mod,
        "classify_binary",
        _fake_classify(source="missing"),
    )
    # kilo is registry-driven now — install_tool must NOT be called for it.
    install_registry_agent("kilo", dry_run=True)
    assert calls == []


def test_install_registry_agent_curl_env_bin_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_install_curl expands {bin_dir} tokens in install.env (goose/kilo/opencode)."""
    from astroai_lab.agent import install as install_mod
    from astroai_lab.agent import registry as registry_mod

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    captured: dict[str, str | None] = {}

    def fake_pipe_bash(url: str, *, env: dict[str, str] | None = None, args=None) -> None:
        captured["env"] = env
        (bin_dir / "goose").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(install_mod, "_curl_pipe_bash", fake_pipe_bash)
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(install_mod, "_link_into_local_bin", lambda *a, **k: None)
    monkeypatch.setattr(install_mod, "_verify_cmd", lambda *a, **k: None)

    agent = {
        "id": "goose",
        "binary": "goose",
        "install": {
            "method": "curl",
            "source": "https://x/download.sh",
            "env": {"GOOSE_BIN_DIR": "{bin_dir}", "CONFIGURE": "false"},
        },
    }
    assert registry_mod._install_curl(agent) == "goose"
    assert captured["env"] == {"GOOSE_BIN_DIR": str(bin_dir), "CONFIGURE": "false"}


def test_install_gh_release_templates_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    """_install_gh_release replaces {arch} with platform.machine() (codex)."""
    from astroai_lab.agent import install as install_mod
    from astroai_lab.agent import registry as registry_mod

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        install_mod,
        "_gh_release_bin",
        lambda repo, asset, binary, **kwargs: seen.append((asset, binary)),
    )
    monkeypatch.setattr(install_mod, "_verify_cmd", lambda *a, **k: None)
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    agent = {
        "id": "codex",
        "binary": "codex",
        "install": {
            "method": "gh-release",
            "repo": "openai/codex",
            "asset": "codex-package-{arch}-unknown-linux-musl.tar.gz",
        },
    }
    assert registry_mod._install_gh_release(agent) == "codex"
    assert seen == [("codex-package-x86_64-unknown-linux-musl.tar.gz", "codex")]


def test_install_registry_agent_curl_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    # A registry-only agent (not in TOOLS) with method curl → curl installer.
    from astroai_lab.agent import registry as registry_mod

    monkeypatch.setattr("astroai_lab.agent.install.TOOLS", {}, raising=False)

    def fake_install_npm(agent):  # pragma: no cover
        return "never"

    monkeypatch.setattr(registry_mod, "_install_curl", lambda agent: "curl-done")
    monkeypatch.setattr(registry_mod, "_install_npm", fake_install_npm)
    monkeypatch.setattr(registry_mod, "_install_uv_tool", fake_install_npm)
    monkeypatch.setattr(registry_mod, "_install_gh_release", fake_install_npm)

    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="missing"),
    )
    agent = {
        "id": "regonly",
        "binary": "regonly",
        "install": {"method": "curl", "source": "https://x/i.sh"},
    }
    monkeypatch.setattr(registry_mod, "get_registry_agent", lambda _: agent)
    assert install_registry_agent("regonly") == "curl-done"


# ---------------------------------------------------------------------------
# List covers every installable agent
# ---------------------------------------------------------------------------


def test_agent_list_includes_every_tools_entry() -> None:
    """`agent list` lists coding agents; TOOL utilities stay install-only.

    Exception: ``node`` / ``ast-grep`` are TOOLS utilities, not list agents.
    """
    from astroai_lab.agent.install import TOOL_UTILITIES, TOOLS

    ids = {a["id"] for a in list_registry_agents()}
    assert set(TOOLS) - TOOL_UTILITIES <= ids
    assert ids.isdisjoint(TOOL_UTILITIES)
    assert "claude" in ids and "copilot" in ids and "qoder" in ids
    assert "cursor" in ids and "kilo" in ids
    assert "hyperfine" not in TOOLS


def test_kilo_and_opencode_curl_skip_shell_rc() -> None:
    kilo = get_registry_agent("kilo")
    opencode = get_registry_agent("opencode")
    assert kilo is not None and opencode is not None
    assert "--no-modify-path" in (kilo["install"].get("args") or [])
    assert "--no-modify-path" in (opencode["install"].get("args") or [])


def test_cli_agent_list_covers_installable_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent.install import TOOL_UTILITIES, TOOLS

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ASTROAI_LAB_PROBE_VERSION", "0")
    result = runner.invoke(app, ["--json", "agent", "list"])
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    names = {row["id"] for row in data["agents"]}
    assert set(TOOLS) - TOOL_UTILITIES <= names
    assert names.isdisjoint(TOOL_UTILITIES)


def test_cli_agent_install_needs_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nameless `agent install` points at `agent list`, it does not list agents."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["--json", "agent", "install"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["help"] == "astroai agent install NAME [NAME…]"
    assert "list" in data["try"]


def test_cli_agent_list_human_shows_status() -> None:
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "Bin" in out
    assert "Cfg" in out
    assert "Where" in out
    assert "agent install kilo" in out


def test_cli_agent_list_includes_new_agents() -> None:
    result = runner.invoke(app, ["--json", "agent", "list"])
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    ids = {item["id"] for item in data["agents"]}
    assert {"hermes", "openclaw", "zcode", "omp"} <= ids


def test_cli_agent_install_unknown() -> None:
    result = runner.invoke(app, ["--json", "agent", "install", "not-an-agent"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "Unknown tool" in data["errors"][0]


def test_cli_agent_install_multiple_json_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("astroai_lab.agent.install.refuse_if_home_owned", lambda *a, **k: None)
    result = runner.invoke(app, ["--json", "--dry-run", "agent", "install", "kilo", "not-an-agent"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["tools"] == ["kilo", "not-an-agent"]
    by_tool = {r["tool"]: r for r in data["results"]}
    assert by_tool["kilo"]["ok"] is True
    assert by_tool["not-an-agent"]["ok"] is False


def test_cli_agent_install_partial_failure_shows_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("astroai_lab.agent.install.refuse_if_home_owned", lambda *a, **k: None)
    result = runner.invoke(app, ["--dry-run", "agent", "install", "kilo", "not-an-agent"])
    assert result.exit_code == 1
    assert "1/2 install(s) failed" in result.output
    assert "not-an-agent" in result.output


def test_verify_setup_includes_registry_for_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fresh home, nothing on PATH → verify_setup reports nothing (day-one pass).
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="missing"),
    )
    from astroai_lab.agent.inventory import verify_setup

    issues = verify_setup(home)
    assert not any("binary not found" in i and "hermes" in i for i in issues)
    assert issues == []


def test_classify_ignores_system_sg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux /usr/bin/sg (newgrp) must not count as ast-grep."""
    from pathlib import Path as P

    from astroai_lab.agent.install import _is_system_sg_impostor, classify_binary
    from astroai_lab.config.settings import get_settings

    monkeypatch.setenv("ASTROAI_LAB_BIN_DIR", str(tmp_path / "nobin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    get_settings.cache_clear()
    monkeypatch.setattr("astroai_lab.agent.install.managed_bin_roots", list)
    monkeypatch.setattr(
        "astroai_lab.agent.install.shutil.which",
        lambda name: "/usr/bin/sg" if name == "sg" else None,
    )
    info = classify_binary("sg", home=tmp_path / "home")
    assert info["source"] == "missing"
    assert _is_system_sg_impostor(P("/usr/bin/sg")) is True


# ---------------------------------------------------------------------------
# Registry-driven setup (`agent setup <id>` / `--all`)
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep setup/update tests hermetic: no real plugin application."""
    monkeypatch.setattr("astroai_lab.agent.plugins.apply_agent_plugins", lambda *a, **k: [])


def test_setup_registry_agent_unknown() -> None:
    with pytest.raises(LabError, match="Unknown agent"):
        setup_registry_agent("not-an-agent")


def test_setup_registry_agent_scaffolds_config(tmp_path: Path, _no_plugins) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = setup_registry_agent("hermes", home=home)
    assert result["ok"] is True
    assert result["agent"] == "hermes"
    cfg = home / ".hermes" / "config.yaml"
    assert cfg.is_file()
    assert any("created config" in a for a in result["actions"])
    # second run is a no-op (config exists, plugins skipped)
    result2 = setup_registry_agent("hermes", home=home)
    assert any("config exists" in a for a in result2["actions"])


def test_setup_registry_agent_never_clobbers_existing(tmp_path: Path, _no_plugins) -> None:
    home = tmp_path / "home"
    cfg = home / ".hermes" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("model: mine\n", encoding="utf-8")
    setup_registry_agent("hermes", home=home, force=True)
    assert cfg.read_text() == "model: mine\n"


def test_setup_registry_agent_dry_run_writes_nothing(tmp_path: Path, _no_plugins) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = setup_registry_agent("hermes", home=home, dry_run=True)
    assert any("would create config" in a for a in result["actions"])
    assert not (home / ".hermes" / "config.yaml").exists()
    # no stamp written on dry-run
    assert not (home / ".astroai" / "lab" / "agent-setup-stamp").exists()


def test_setup_registry_agent_creates_skills_dir(tmp_path: Path, _no_plugins) -> None:
    home = tmp_path / "home"
    home.mkdir()
    setup_registry_agent("hermes", home=home)
    assert (home / ".hermes" / "skills").is_dir()


def test_setup_registry_agent_post_install_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ran: list[str] = []
    monkeypatch.setattr("astroai_lab.agent.registry._run_post_install", lambda cmd: ran.append(cmd))
    # default: not run
    setup_registry_agent("openclaw", home=home)
    assert ran == []
    # opt-in: run
    result = setup_registry_agent("openclaw", home=home, post_install=True)
    assert ran == ["openclaw onboard"]
    assert any("ran post-install" in a for a in result["actions"])


def test_setup_registry_agent_plugin_errors_mark_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent.plugins import PluginResult

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_agent_plugins",
        lambda *a, **k: [PluginResult("ray-manager-mcp", "hermes", "failed", "boom")],
    )
    result = setup_registry_agent("hermes", home=home)
    assert result["ok"] is False
    assert any("plugin ray-manager-mcp" in e for e in result["errors"])


def test_setup_registry_agent_applies_defaults_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """setup <id> must not auto-install every opt-in skill (polars, etc.)."""
    from astroai_lab.agent import plugins as plugins_mod
    from astroai_lab.agent.plugins import PluginResult

    home = tmp_path / "home"
    home.mkdir()
    seen: list[str] = []

    def fake_install(plugin_id, **kwargs):
        seen.append(plugin_id)
        return [PluginResult(plugin_id, "cursor", "skipped", "test")]

    monkeypatch.setattr(plugins_mod, "install_plugin", fake_install)
    # Pretend cursor is installed so installed_only does not filter the matrix.
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        lambda *a, **k: {
            "binary": "agent",
            "path": "/tmp/agent",
            "source": "managed",
            "managed": True,
            "home_install": False,
            "home_path": None,
        },
    )
    result = setup_registry_agent("cursor", home=home)
    assert result["ok"] is True
    assert "polars" not in seen
    assert "librarian" not in seen
    assert "pydantic-skills" not in seen
    assert "token-efficient" in seen or "mcp-context7" in seen or "mcp-memory" in seen
    assert any("applied config bundle" in a for a in result["actions"])


def test_list_installed_registry_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="managed", managed=True, path="/tmp/x"),
    )
    ids = [a["id"] for a in list_installed_registry_agents(home)]
    assert "hermes" in ids
    assert "openclaw" in ids


def test_list_installed_includes_home_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="home", managed=False, home_install=True, path="/home/x/bin/kilo"),
    )
    ids = [a["id"] for a in list_installed_registry_agents(home)]
    assert "hermes" in ids


def test_verify_issues_includes_home_owned_missing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="home", managed=False, home_install=True, path="/home/x/bin/x"),
    )
    issues = registry_verify_issues(home=home, installed_only=True)
    assert any("config missing" in i and "hermes" in i for i in issues)


def test_repair_restores_agent_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Broken config → launch fails → repair → launch works (before/after proof)."""
    import os
    import stat
    import subprocess

    from astroai_lab.agent.fix import repair_installed_agents
    from astroai_lab.agent.inventory import verify_setup
    from astroai_lab.agent.registry import probe_launch

    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASTROAI_LAB_BIN_DIR", str(bin_dir))
    # Prefer fake bin dir, but keep system tools (cat) for the stub script.
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    # Enable launch probing for this test only (conftest sets 0).
    monkeypatch.setenv("ASTROAI_LAB_PROBE_VERSION", "1")
    from astroai_lab.config.settings import get_settings

    get_settings.cache_clear()

    # Only kilo looks installed — ignore host CLIs on PATH.
    def _classify(binary: str, *, home=None):
        if binary == "kilo":
            return {
                "binary": binary,
                "path": str(bin_dir / "kilo"),
                "source": "managed",
                "managed": True,
                "home_install": False,
                "home_path": None,
            }
        return {
            "binary": binary,
            "path": None,
            "source": "missing",
            "managed": False,
            "home_install": False,
            "home_path": None,
        }

    monkeypatch.setattr("astroai_lab.agent.install.classify_binary", _classify)

    # Fake kilo: --version fails when config has no `{` (broken / missing).
    kilo = bin_dir / "kilo"
    kilo.write_text(
        "#!/bin/sh\n"
        'CFG="${HOME}/.config/kilo/kilo.jsonc"\n'
        'if [ "$1" = "--version" ]; then\n'
        '  case "$(cat "$CFG" 2>/dev/null)" in\n'
        '    *"{"*) echo "kilo 9.9.9"; exit 0 ;;\n'
        '    *) echo "bad config" >&2; exit 1 ;;\n'
        "  esac\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    kilo.chmod(kilo.stat().st_mode | stat.S_IEXEC)

    cfg = home / ".config" / "kilo" / "kilo.jsonc"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("NOT JSON broken\n", encoding="utf-8")

    before = subprocess.run(
        [str(kilo), "--version"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        check=False,
    )
    assert before.returncode != 0
    assert probe_launch("kilo") is not None

    issues = verify_setup(home)
    assert any(
        "kilo" in i.lower() or "syntax" in i.lower() or "broken" in i.lower() for i in issues
    )

    repair = repair_installed_agents(home=home, dry_run=False)
    assert repair["ok"] is True
    assert (
        any(
            (r.fixed and "kilo" in r.detail) or (r.fixed and r.target == "kilo.jsonc")
            for r in repair["setup"]
        )
        or "kilo" in repair["fixed"]
        or any("repaired" in a for a in repair["actions"])
    )
    assert "{" in cfg.read_text()

    after = subprocess.run(
        [str(kilo), "--version"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        check=False,
    )
    assert after.returncode == 0
    assert "9.9.9" in after.stdout
    assert probe_launch("kilo") is None
    assert not any("failed to launch" in i and "kilo" in i.lower() for i in verify_setup(home))


def test_cli_setup_agent_registry_driven(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("astroai_lab.agent.plugins.apply_agent_plugins", lambda *a, **k: [])
    result = runner.invoke(app, ["--json", "agent", "setup", "hermes"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert any("created config" in a for a in data["actions"])
    assert (tmp_path / ".hermes" / "config.yaml").is_file()


def test_cli_setup_mixed_bundle_and_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("astroai_lab.agent.plugins.apply_agent_plugins", lambda *a, **k: [])
    result = runner.invoke(app, ["--json", "--dry-run", "agent", "setup", "cli", "hermes"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert any("would create config" in a for a in data["actions"])


# ---------------------------------------------------------------------------
# Registry-driven update (`agent update <id>`)
# ---------------------------------------------------------------------------


def test_update_registry_agent_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_plugins
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="managed", managed=True, path="/tmp/x"),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "astroai_lab.agent.registry.install_registry_agent",
        lambda name, dry_run=False: calls.append(name),
    )
    result = update_registry_agent("hermes", home=home)
    assert calls == []  # up-to-date → no reinstall
    assert any("binary up-to-date" in a for a in result["actions"])


def test_update_registry_agent_installs_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_plugins
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="missing"),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "astroai_lab.agent.registry.install_registry_agent",
        lambda name, dry_run=False: calls.append(name) or name,
    )
    result = update_registry_agent("hermes", home=home)
    assert calls == ["hermes"]
    assert any("binary install" in a for a in result["actions"])


def test_update_registry_agent_reinstall_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_plugins
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="managed", managed=True, path="/tmp/x"),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "astroai_lab.agent.registry.install_registry_agent",
        lambda name, dry_run=False: calls.append(name) or name,
    )
    result = update_registry_agent("hermes", home=home, force_reinstall=True)
    assert calls == ["hermes"]
    assert any("binary reinstall" in a for a in result["actions"])


def test_update_registry_agent_install_failure_marks_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_plugins
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="missing"),
    )

    def boom(name, dry_run=False):  # pragma: no cover
        raise LabError("install failed")

    monkeypatch.setattr("astroai_lab.agent.registry.install_registry_agent", boom)
    result = update_registry_agent("hermes", home=home)
    assert result["ok"] is False
    assert any("install failed" in e for e in result["errors"])


def test_cli_update_agent_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="managed", managed=True, path="/tmp/x"),
    )
    monkeypatch.setattr("astroai_lab.agent.plugins.apply_agent_plugins", lambda *a, **k: [])
    result = runner.invoke(app, ["--json", "--dry-run", "agent", "update", "hermes"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["agent"] == "hermes"
    assert data["ok"] is True
    assert any("binary up-to-date" in a for a in data["actions"])


# ---------------------------------------------------------------------------
# Registry-driven repair (`agent verify --fix <id>` / `--all`)
# ---------------------------------------------------------------------------


def test_fix_registry_agent_unknown() -> None:
    with pytest.raises(LabError, match="Unknown agent"):
        fix_registry_agent("not-an-agent")


def test_fix_registry_agent_scaffolds_missing_config(tmp_path: Path) -> None:
    """Missing config → scaffolded (and the scaffold must parse back)."""
    from astroai_lab.agent import agent_config as ac

    home = tmp_path / "home"
    home.mkdir()
    result = fix_registry_agent("hermes", home=home)
    assert result["ok"] is True
    cfg = home / ".hermes" / "config.yaml"
    assert cfg.is_file()
    assert any("created config" in a for a in result["actions"])
    # scaffold parses as valid YAML and the skills dir was created
    ac.validate_config_text("hermes", cfg.read_text(), home=home)
    assert (home / ".hermes" / "skills").is_dir()
    # stamp refreshed (failed marker cleared)
    assert (home / ".astroai" / "lab" / "agent-setup-stamp").is_file()


def test_fix_registry_agent_reports_healthy_existing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cfg = home / ".hermes" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("model: mine\n", encoding="utf-8")
    result = fix_registry_agent("hermes", home=home)
    assert result["ok"] is True
    assert any("config healthy" in a for a in result["actions"])
    assert cfg.read_text() == "model: mine\n"  # never clobbers a healthy file


def test_fix_registry_agent_repairs_broken_jsonc(tmp_path: Path) -> None:
    from astroai_lab.agent import agent_config as ac

    home = tmp_path / "home"
    cfg = home / ".config" / "kilo" / "kilo.jsonc"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{\n  "model": [unclosed\n', encoding="utf-8")
    result = fix_registry_agent("kilo", home=home)
    assert any("repaired broken jsonc config" in a for a in result["actions"])
    # the reset file parses again (// header scaffold is JSONC-legal)
    text = cfg.read_text()
    assert text.lstrip().startswith("//")
    assert ac.validate_config_text("kilo", text, home=home) == {}


def test_fix_registry_agent_repairs_broken_toml(tmp_path: Path) -> None:
    from astroai_lab.agent import agent_config as ac

    home = tmp_path / "home"
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("model = [unclosed", encoding="utf-8")
    result = fix_registry_agent("codex", home=home)
    assert any("repaired broken toml config" in a for a in result["actions"])
    ac.validate_config_text("codex", cfg.read_text(), home=home)


def test_fix_registry_agent_sets_codex_mcp_timeouts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[mcp_servers.fetch]\ncommand = "uvx"\nargs = ["mcp-server-fetch"]\nenabled = true\n',
        encoding="utf-8",
    )
    result = fix_registry_agent("codex", home=home)
    assert any("startup_timeout_sec" in a for a in result["actions"])
    text = cfg.read_text(encoding="utf-8")
    assert "startup_timeout_sec = 120" in text
    # Idempotent once set high enough.
    result2 = fix_registry_agent("codex", home=home)
    assert any("config healthy" in a for a in result2["actions"])


def test_fix_registry_agent_markdown_read_only(tmp_path: Path) -> None:
    """Existing markdown config is healthy (read-only); a missing one scaffolds."""
    home = tmp_path / "home"
    cfg = home / ".config" / "cline" / "cline-notes.md"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("# notes\n", encoding="utf-8")
    result = fix_registry_agent("cline", home=home)
    assert result["ok"] is True
    assert any("markdown read-only" in a for a in result["actions"])
    assert cfg.read_text() == "# notes\n"  # never rewritten

    home2 = tmp_path / "home2"
    home2.mkdir()
    result2 = fix_registry_agent("cline", home=home2)
    assert any("created config" in a for a in result2["actions"])
    assert (home2 / ".config" / "cline" / "cline-notes.md").is_file()


def test_fix_registry_agent_dry_run_writes_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = fix_registry_agent("hermes", home=home, dry_run=True)
    assert any("would create config" in a for a in result["actions"])
    assert not (home / ".hermes" / "config.yaml").exists()
    assert not (home / ".hermes" / "skills").exists()
    assert not (home / ".astroai" / "lab" / "agent-setup-stamp").exists()


def test_fix_registry_agent_dry_run_broken_config_untouched(tmp_path: Path) -> None:
    """Dry-run over a broken config reports 'would repair' but writes nothing."""
    home = tmp_path / "home"
    cfg = home / ".config" / "kilo" / "kilo.jsonc"
    cfg.parent.mkdir(parents=True)
    broken = '{\n  "model": [unclosed\n'
    cfg.write_text(broken, encoding="utf-8")
    result = fix_registry_agent("kilo", home=home, dry_run=True)
    assert any("would repair broken jsonc config" in a for a in result["actions"])
    assert cfg.read_text() == broken  # untouched in dry-run
    assert not (home / ".astroai" / "lab" / "agent-setup-stamp").exists()


def test_setup_scaffold_parses_for_json5(tmp_path: Path, _no_plugins) -> None:
    """Regression: the json5 scaffold uses `//` headers so it parses back."""
    from astroai_lab.agent import agent_config as ac

    home = tmp_path / "home"
    home.mkdir()
    setup_registry_agent("openclaw", home=home)
    cfg = home / ".openclaw" / "openclaw.json"
    assert cfg.is_file()
    assert cfg.read_text().lstrip().startswith("//")
    ac.validate_config_text("openclaw", cfg.read_text(), home=home)


def test_cli_fix_config_agent_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["--json", "agent", "verify", "--fix", "hermes"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["agent"] == "hermes"
    assert data["ok"] is True
    assert any("created config" in a for a in data["actions"])
    assert (tmp_path / ".hermes" / "config.yaml").is_file()


def test_cli_fix_config_agent_human_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON path prints actions + the 'config OK' summary line."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "verify", "--fix", "hermes"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "config OK" in out
    assert (tmp_path / ".hermes" / "config.yaml").is_file()


def test_cli_fix_config_all_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--fix --all` matches bare `--fix` (shared repair + verify payload)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        _fake_classify(source="missing"),
    )
    result = runner.invoke(app, ["--json", "agent", "verify", "--fix", "--all"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert "issues" in data
    assert data["issues"] == []


def test_cli_fix_config_clean_conflicts_with_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["agent", "verify", "--fix", "hermes", "--clean"])
    assert result.exit_code == 2  # typer usage error (BadParameter)
    assert "cannot be combined" in (result.stdout + result.stderr)
