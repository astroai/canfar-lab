from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.inventory import list_bundles, verify_setup
from astroai_lab.agent.setup import agent_setup, install_file
from astroai_lab.cli.main import app

runner = CliRunner()


def test_bundle_root_exists() -> None:
    from astroai_lab.agent.bundle_path import bundle_root

    assert (bundle_root() / "manifest.json").is_file()


def test_list_bundles() -> None:
    bundles = list_bundles()
    assert "cursor" in bundles
    assert "all" in bundles


def test_install_file(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("x")
    assert install_file(src, dst, force=False, dry_run=False)
    assert dst.read_text() == "x"


def test_agent_setup_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    agent_setup(bundles=["cli"], dry_run=True)
    assert not (home / ".astroai" / "lab" / "agent-env.sh").is_file()


def test_agent_verify_fresh_home_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No agents installed → verify passes (no Cursor nag)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        lambda *a, **k: {
            "binary": "x",
            "path": None,
            "source": "missing",
            "managed": False,
            "home_install": False,
            "home_path": None,
        },
    )
    issues = verify_setup(home)
    assert issues == []


def test_agent_verify_goose_scaffold_without_provider_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed goose + lab scaffold must not fail verify for missing provider.

    ``goose configure`` writes GOOSE_PROVIDER/GOOSE_MODEL; ``verify --fix``
    cannot. Blocking on those keys made post-install ``verify --fix --all``
    fail on every image that actually installed goose.
    """
    home = tmp_path / "home"
    cfg = home / ".config" / "goose"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text(
        "# AstroAI lab — run: goose configure\nextensions: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    def _classify(binary: str, *, home=None):
        if binary == "goose":
            return {
                "binary": binary,
                "path": "/tmp/goose",
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
    issues = verify_setup(home)
    assert not any("Goose provider" in i for i in issues)


def test_agent_verify_cursor_required_when_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    def _classify(binary: str, *, home=None):
        # Cursor Agent upstream binary is still named `agent`.
        if binary in ("agent", "cursor"):
            return {
                "binary": binary,
                "path": "/tmp/agent",
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
    issues = verify_setup(home)
    assert any("Cursor MCP not configured" in i for i in issues)


def test_agent_verify_reports_legacy_scratch_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    def _classify(binary: str, *, home=None):
        if binary in ("agent", "cursor"):
            return {
                "binary": binary,
                "path": str(tmp_path / "scratch" / ".local" / "bin" / "agent"),
                "source": "legacy",
                "managed": False,
                "home_install": False,
                "home_path": None,
                "legacy": True,
                "legacy_path": str(tmp_path / "scratch" / ".local" / "bin" / "agent"),
            }
        return {
            "binary": binary,
            "path": None,
            "source": "missing",
            "managed": False,
            "home_install": False,
            "home_path": None,
            "legacy": False,
            "legacy_path": None,
        }

    monkeypatch.setattr("astroai_lab.agent.install.classify_binary", _classify)
    issues = verify_setup(home)
    assert any("Legacy" in i and "cursor" in i for i in issues)


def test_agent_verify_opencode_syntax(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent.inventory import verify_config_syntax

    home = tmp_path / "home"
    oc = home / ".config" / "opencode"
    oc.mkdir(parents=True)
    (oc / "opencode.json").write_text("{ mcp: { broken } }\n")  # invalid JSON
    monkeypatch.setenv("HOME", str(home))
    issues = verify_config_syntax(home)
    assert any("syntax error" in i and "opencode" in i for i in issues)


def test_agent_verify_jsonc_ok(tmp_path: Path) -> None:
    from astroai_lab.agent.inventory import verify_config_syntax

    home = tmp_path / "home"
    oc = home / ".config" / "opencode"
    oc.mkdir(parents=True)
    (oc / "opencode.json").write_text(
        '{\n  // comment\n  "mcp": { "a": {} },\n}\n',
        encoding="utf-8",
    )
    assert verify_config_syntax(home) == []


def test_agent_install_requires_name() -> None:
    result = runner.invoke(app, ["agent", "install"])
    assert result.exit_code == 0
    out = result.stdout + result.stderr
    assert "agent list" in out
    assert "agent install NAME" in out


def test_agent_list_overview() -> None:
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code in (0, 1)
    out = result.stdout + result.stderr
    assert "Bin" in out and "Cfg" in out
    assert "Where" in out
    assert "agent install kilo" in out or "plugins list" in out.lower()
    plugins = runner.invoke(app, ["agent", "plugins", "list"])
    assert plugins.exit_code == 0
    bout = plugins.stdout + plugins.stderr
    assert "ray-manager-mcp" in bout or "Plugins" in bout or "token-efficient" in bout


def test_agent_setup_unknown_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--dry-run", "agent", "setup", "not-a-setup-name"])
    assert result.exit_code != 0


def test_agent_setup_cli_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--dry-run", "agent", "setup", "cli"])
    assert result.exit_code == 0


def test_install_tool_unknown() -> None:
    from astroai_lab.agent.install import install_tool
    from astroai_lab.errors import LabError

    with pytest.raises(LabError, match="Unknown tool"):
        install_tool("not-a-tool")


def test_install_tool_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent.install import install_tool

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("astroai_lab.agent.install.refuse_if_home_owned", lambda *a, **k: None)
    install_tool("node", dry_run=True)


def test_merge_mcp_servers(tmp_path: Path) -> None:
    from astroai_lab.agent.setup import merge_mcp_servers
    from astroai_lab.utils.json_utils import read_json, write_json

    src = tmp_path / "src.json"
    dst = tmp_path / "dst.json"
    src.write_text('{"mcpServers": {"a": {"url": "x"}}, "keepMe": false}')
    write_json(dst, {"mcpServers": {"b": {"url": "y"}}, "userKey": 1})
    merge_mcp_servers(src, dst, force=True, dry_run=False)
    data = read_json(dst)
    assert data["userKey"] == 1  # never clobber whole file
    assert data["mcpServers"]["a"]["url"] == "x"
    assert data["mcpServers"]["b"]["url"] == "y"


def test_npm_global_install_cmd_adds_allow_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent import install as install_mod

    monkeypatch.setattr(install_mod, "_npm_version_tuple", lambda: (11, 17))
    cmd = install_mod.npm_global_install_cmd(
        tmp_path / "prefix", "@oh-my-pi/pi-coding-agent@latest"
    )
    assert cmd[:4] == ["npm", "install", "-g", "--prefix"]
    assert "--dangerously-allow-all-scripts" in cmd
    assert cmd[-1] == "@oh-my-pi/pi-coding-agent@latest"


def test_npm_global_install_cmd_skips_flag_on_old_npm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent import install as install_mod

    monkeypatch.setattr(install_mod, "_npm_version_tuple", lambda: (10, 9))
    cmd = install_mod.npm_global_install_cmd(tmp_path / "prefix", "left-pad@1.3.0")
    assert "--dangerously-allow-all-scripts" not in cmd


def test_npm_install_environ_silences_update_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from astroai_lab.agent import install as install_mod

    monkeypatch.setattr(
        install_mod,
        "_session_environ",
        lambda extra=None: {"PATH": "/usr/bin", **(extra or {})},
    )
    env = install_mod.npm_install_environ()
    assert env["NPM_CONFIG_UPDATE_NOTIFIER"] == "false"
    assert env["NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS"] == "true"


def test_cursor_tool_and_binary() -> None:
    from astroai_lab.agent import install as install_mod

    assert install_mod.tool_binary("cursor") == "agent"
    assert "cursor" in install_mod.TOOLS
    assert "agent" not in install_mod.TOOLS


def test_cli_install_agent_name_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy name `agent` is not accepted; use `cursor`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["--json", "--dry-run", "agent", "install", "agent"])
    assert result.exit_code == 1
    import json

    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "Unknown" in data["errors"][0]


def test_curl_installer_environ_keeps_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent import install as install_mod

    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    home.mkdir()
    bin_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(
        install_mod,
        "_session_environ",
        lambda extra=None: {"PATH": "/usr/bin", "HOME": str(home), **(extra or {})},
    )
    env = install_mod.curl_installer_environ({"GOOSE_BIN_DIR": str(bin_dir)})
    assert env["HOME"] == str(home)
    assert env["XDG_BIN_DIR"] == str(bin_dir)
    assert env["GOOSE_BIN_DIR"] == str(bin_dir)


def test_find_curl_binary_prefers_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent import install as install_mod

    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    sandbox_bin = bin_dir.parent / "installer-home" / ".kilo" / "bin"
    home.mkdir()
    bin_dir.mkdir(parents=True)
    sandbox_bin.mkdir(parents=True)
    (sandbox_bin / "kilo").write_text("#!/bin/sh\nold-sandbox\n", encoding="utf-8")
    (bin_dir / "kilo").write_text("#!/bin/sh\nhome\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: bin_dir)
    found = install_mod.find_curl_binary("kilo")
    assert found == bin_dir / "kilo"


def test_link_copies_kilo_tree_sitter_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent import install as install_mod

    scratch_bin = tmp_path / "scratch" / "bin"
    src_dir = tmp_path / "scratch" / "installer-home" / ".kilo" / "bin"
    scratch_bin.mkdir(parents=True)
    src_dir.mkdir(parents=True)
    kilo = src_dir / "kilo"
    kilo.write_text("#!/bin/sh\n", encoding="utf-8")
    kilo.chmod(0o755)
    (src_dir / "tree-sitter").mkdir()
    (src_dir / "tree-sitter" / "lib.so").write_text("x", encoding="utf-8")
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: scratch_bin)
    install_mod._link_into_local_bin(kilo, "kilo")
    assert (scratch_bin / "kilo").is_file()
    assert (scratch_bin / "tree-sitter" / "lib.so").is_file()


def test_link_keeps_cursor_payload_next_to_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor's `agent` is a symlink into a version dir that also has `node`.

    Copying only the wrapper into bin/ makes it exec `$BIN_DIR/node`.
    """
    from astroai_lab.agent import install as install_mod

    scratch_bin = tmp_path / "scratch" / "bin"
    payload = (
        tmp_path
        / "scratch"
        / "installer-home"
        / ".local"
        / "share"
        / "cursor-agent"
        / "versions"
        / "2026.08.11-e8db854"
    )
    sandbox_bin = tmp_path / "scratch" / "installer-home" / ".local" / "bin"
    scratch_bin.mkdir(parents=True)
    payload.mkdir(parents=True)
    sandbox_bin.mkdir(parents=True)
    wrapper = payload / "cursor-agent"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'SCRIPT_DIR="$(dirname "$(realpath "$0")")"\n'
        'exec "$SCRIPT_DIR/node" "$SCRIPT_DIR/index.js" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    (payload / "node").write_text("#!/bin/sh\n", encoding="utf-8")
    (payload / "node").chmod(0o755)
    (payload / "index.js").write_text("console.log('ok')\n", encoding="utf-8")
    agent = sandbox_bin / "agent"
    agent.symlink_to(wrapper)
    # Stale copied wrapper from a previous broken install.
    (scratch_bin / "agent").write_text(
        "#!/usr/bin/env bash\nexec /missing/node\n", encoding="utf-8"
    )
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: scratch_bin)
    install_mod._link_into_local_bin(agent, "agent")

    landed = scratch_bin / "agent"
    assert landed.is_symlink()
    real = landed.resolve()
    assert (real.parent / "node").is_file()
    assert (real.parent / "index.js").is_file()
    assert not (scratch_bin / "node").exists()


def test_classify_binary_home_canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent import install as install_mod

    home = tmp_path / "home"
    home_bin = home / ".local" / "bin"
    scratch_bin = tmp_path / "scratch" / ".local" / "bin"
    home.mkdir()
    home_bin.mkdir(parents=True)
    scratch_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCRATCH", str(tmp_path / "scratch"))
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: home_bin)
    monkeypatch.setattr(install_mod, "_npm_prefix", lambda: home_bin.parent)
    monkeypatch.setattr(
        install_mod,
        "resolve_session_env",
        lambda ensure=False: type(
            "E",
            (),
            {
                "astroai_lab_bin_dir": home_bin,
                "astroai_lab_npm_prefix": home_bin.parent,
            },
        )(),
    )

    managed = home_bin / "kilo"
    managed.write_text("#!/bin/sh\n", encoding="utf-8")
    managed.chmod(0o755)
    info = install_mod.classify_binary("kilo", home=home)
    assert info["source"] == install_mod.BINARY_SOURCE_MANAGED
    assert info["managed"] is True
    assert info["home_install"] is True

    (scratch_bin / "goose").write_text("#!/bin/sh\n", encoding="utf-8")
    info_legacy = install_mod.classify_binary("goose", home=home)
    assert info_legacy["source"] == install_mod.BINARY_SOURCE_LEGACY
    assert info_legacy["legacy"] is True

    # refuse is a no-op now
    install_mod.refuse_if_home_owned("goose", home=home)


def test_remove_home_cli_without_clean_home_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from astroai_lab.agent import install as install_mod

    home = tmp_path / "home"
    home_bin = home / ".local" / "bin"
    home.mkdir()
    home_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: home_bin)
    monkeypatch.setattr(install_mod, "_npm_prefix", lambda: home_bin.parent)
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: None)
    session = SimpleNamespace(
        astroai_lab_bin_dir=home_bin,
        astroai_lab_npm_prefix=home_bin.parent,
    )
    session.exports = dict
    monkeypatch.setattr(install_mod, "resolve_session_env", lambda ensure=False: session)
    (home_bin / "copilot").write_text("#!/bin/sh\n", encoding="utf-8")

    results = install_mod.uninstall_tool("copilot", home=home, dry_run=False)
    assert not (home_bin / "copilot").exists()
    assert any("binary:" in r.target or "home-binary:" in r.target for r in results)
