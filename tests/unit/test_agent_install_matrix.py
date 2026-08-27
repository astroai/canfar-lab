"""Elaborate agent-install matrix: every registry agent + utilities.

Two personas, exercised for each installable id:

1. **Brand-new user** — empty $HOME, empty scratch bin. Dry-run install is
   allowed; setup scaffolds config/skills without writing under home for
   dry-run; mocked installers land a managed binary.

2. **User with their own crap** — pre-existing ``~/.local/bin/<binary>``,
   broken config files, leftover home trees. Install must refuse until
   ``--clean-home``; verify --fix repairs broken configs; managed install
   then succeeds (mocked).

No network downloads in the default suite — installers are mocked so CI stays
fast and offline-safe. Live URL reachability lives in a separate optional
test gated on ``ASTROAI_LAB_NETWORK_TESTS=1``.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from astroai_lab.agent import install as install_mod
from astroai_lab.agent.registry import (
    fix_registry_agent,
    get_registry_agent,
    install_registry_agent,
    list_registry_agents,
    load_registry,
    remove_registry_agent,
    setup_registry_agent,
)
from astroai_lab.errors import LabError

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

REGISTRY_IDS = [a["id"] for a in load_registry()]
assert REGISTRY_IDS, "registry must not be empty"
assert REGISTRY_IDS == sorted(REGISTRY_IDS)

# Tools that are not coding agents (still installable via agent install).
UTILITY_IDS = sorted(install_mod.TOOL_UTILITIES)


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Isolate HOME + managed bin/npm under tmp; hide host agent binaries."""
    home = tmp_path / "home"
    scratch = tmp_path / "scratch"
    bin_dir = scratch / "bin"
    npm_prefix = scratch / "npm"
    home.mkdir()
    bin_dir.mkdir(parents=True)
    (npm_prefix / "bin").mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASTROAI_LAB_BIN_DIR", str(bin_dir))
    monkeypatch.setenv("ASTROAI_LAB_NPM_PREFIX", str(npm_prefix))
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(install_mod, "_npm_prefix", lambda: npm_prefix)

    # Hide agent CLIs on the host PATH, but keep real tools (curl, npm, uv, gh).
    _real_which = install_mod.shutil.which
    _keep = frozenset({"curl", "npm", "uv", "gh", "bash", "sh", "tar", "unzip"})

    def _which(cmd: str) -> str | None:
        if cmd in _keep:
            return _real_which(cmd)
        return None

    monkeypatch.setattr(install_mod.shutil, "which", _which)

    session = SimpleNamespace(
        astroai_lab_bin_dir=bin_dir,
        astroai_lab_npm_prefix=npm_prefix,
    )
    session.exports = lambda: {
        "ASTROAI_LAB_BIN_DIR": str(bin_dir),
        "ASTROAI_LAB_NPM_PREFIX": str(npm_prefix),
        "PATH": f"{bin_dir}:{npm_prefix / 'bin'}",
    }
    monkeypatch.setattr(install_mod, "resolve_session_env", lambda ensure=False: session)
    return home, bin_dir, npm_prefix


def _drop_home_binary(home: Path, binary: str) -> Path:
    path = home / ".local" / "bin" / binary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho home-owned\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _config_rel(agent: dict) -> str | None:
    path = (agent.get("config") or {}).get("path")
    if not path:
        return None
    text = str(path)
    if text.startswith("~/"):
        return text[2:]
    if text.startswith("~"):
        return text[1:].lstrip("/")
    return text


def _no_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_agent_plugins",
        lambda *a, **k: [],
    )


# ---------------------------------------------------------------------------
# Schema: every shipped agent is installable and self-consistent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", REGISTRY_IDS, ids=REGISTRY_IDS)
def test_every_registry_agent_schema(agent_id: str) -> None:
    agent = get_registry_agent(agent_id)
    assert agent is not None
    assert agent["id"] == agent_id
    assert agent["binary"]
    assert agent["homepage"].startswith("http")
    install = agent["install"]
    method = install["method"]
    assert method in ("npm", "curl", "gh-release", "uv-tool")
    if method == "npm":
        assert install.get("source")
    elif method == "curl":
        assert str(install.get("source", "")).startswith("https://")
    elif method == "gh-release":
        assert install.get("repo") and install.get("asset")
        assert "{arch}" in install["asset"] or "x86_64" in install["asset"]
        # Public by default — new users must not be forced through gh auth.
        assert install.get("requires_gh_auth") in (None, False)
    elif method == "uv-tool":
        assert install.get("source")
    cfg = agent.get("config") or {}
    if cfg.get("path"):
        assert cfg.get("format") in ("json", "jsonc", "json5", "yaml", "toml", "markdown")


def test_registry_covers_every_yaml_on_disk() -> None:
    from astroai_lab.agent.bundle_path import bundle_root

    on_disk = {p.stem for p in (bundle_root() / "agents").glob("*.yaml")}
    assert on_disk == set(REGISTRY_IDS)


# ---------------------------------------------------------------------------
# Brand-new user
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", REGISTRY_IDS, ids=REGISTRY_IDS)
def test_new_user_dry_run_install_allowed(
    agent_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, bin_dir, _ = _session(tmp_path, monkeypatch)
    # Empty home: no refuse.
    assert install_registry_agent(agent_id, dry_run=True) == agent_id
    assert list(bin_dir.iterdir()) == []
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("agent_id", REGISTRY_IDS, ids=REGISTRY_IDS)
def test_new_user_setup_scaffolds_without_clobber(
    agent_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _no_plugins_fx
) -> None:
    home, _, _ = _session(tmp_path, monkeypatch)
    # Bundles merge MCP into configs — stub merges so we only test scaffold/clobber.
    monkeypatch.setattr(
        "astroai_lab.agent.setup.run_bundle",
        lambda *a, **k: None,
    )
    agent = get_registry_agent(agent_id)
    assert agent is not None
    result = setup_registry_agent(agent_id, home=home, dry_run=False)
    assert result["ok"] is True
    assert not result["errors"]

    rel = _config_rel(agent)
    if not rel:
        return
    cfg = home / rel
    assert cfg.is_file(), f"{agent_id} should scaffold {rel}"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n# user marker\n", encoding="utf-8")
    # Default setup never clobbers; force is opt-in overwrite for bundles.
    setup_registry_agent(agent_id, home=home, force=False)
    assert "# user marker" in cfg.read_text(encoding="utf-8")


@pytest.fixture
def _no_plugins_fx(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_plugins(monkeypatch)


@pytest.mark.parametrize(
    ("agent_id", "needle"),
    [
        ("kilo", "$schema"),
        ("codex", "mcp_servers.fetch"),
        ("goose", "extensions"),
    ],
)
def test_new_user_bundle_template_not_empty_scaffold(
    agent_id: str,
    needle: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _no_plugins_fx,
) -> None:
    """Regression: scaffold-before-bundle left new users with `{}` configs."""
    home, _, _ = _session(tmp_path, monkeypatch)
    result = setup_registry_agent(agent_id, home=home)
    assert result["ok"] is True
    agent = get_registry_agent(agent_id)
    assert agent is not None
    rel = _config_rel(agent)
    assert rel
    text = (home / rel).read_text(encoding="utf-8")
    assert needle in text
    assert not text.strip().endswith("{}\n") or needle in text



@pytest.mark.parametrize("agent_id", REGISTRY_IDS, ids=REGISTRY_IDS)
def test_new_user_mocked_install_lands_managed_binary(
    agent_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each install method is exercised via a stub that drops the binary."""
    home, bin_dir, npm_prefix = _session(tmp_path, monkeypatch)
    agent = get_registry_agent(agent_id)
    assert agent is not None
    binary = str(agent["binary"])
    method = agent["install"]["method"]
    seen: list[str] = []

    def _land(_agent: dict | None = None, *_a, **_k) -> str:
        seen.append(method)
        target = bin_dir / binary
        target.write_text("#!/bin/sh\n", encoding="utf-8")
        target.chmod(0o755)
        return agent_id if _agent is None else str(_agent.get("id", agent_id))

    # TOOLS-backed agents go through install_tool — stub that instead.
    if agent_id in install_mod.TOOLS:

        def _tool(name: str, *, dry_run: bool = False) -> None:
            assert name == agent_id
            if dry_run:
                return
            _land()

        monkeypatch.setattr(install_mod, "install_tool", _tool)
        monkeypatch.setattr(install_mod, "refuse_if_home_owned", lambda *a, **k: None)
    else:
        monkeypatch.setattr("astroai_lab.agent.registry._install_npm", _land)
        monkeypatch.setattr("astroai_lab.agent.registry._install_curl", _land)
        monkeypatch.setattr("astroai_lab.agent.registry._install_uv_tool", _land)
        monkeypatch.setattr("astroai_lab.agent.registry._install_gh_release", _land)

    # Skip post-install setup network/plugins.
    monkeypatch.setattr(
        "astroai_lab.agent.registry.setup_registry_agent",
        lambda *a, **k: {"ok": True, "errors": [], "actions": [], "agent": agent_id},
    )

    assert install_registry_agent(agent_id, dry_run=False) == agent_id
    assert (bin_dir / binary).is_file()
    info = install_mod.classify_binary(binary, home=home)
    assert info["managed"] is True
    assert info["home_install"] is False
    # npm agents may also appear under npm prefix in real life; we only land bin_dir.


# ---------------------------------------------------------------------------
# User arriving with their own crap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", REGISTRY_IDS, ids=REGISTRY_IDS)
def test_dirty_home_refuses_install_until_clean(
    agent_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, bin_dir, _ = _session(tmp_path, monkeypatch)
    agent = get_registry_agent(agent_id)
    assert agent is not None
    binary = str(agent["binary"])
    home_bin = _drop_home_binary(home, binary)

    with pytest.raises(LabError, match="already installed under your home|not managed"):
        install_registry_agent(agent_id, dry_run=True)

    # Without --clean-home, remove also refuses for registry-only / TOOLS alike.
    with pytest.raises(LabError, match="--clean-home"):
        remove_registry_agent(agent_id, home=home, dry_run=True)

    results = remove_registry_agent(agent_id, home=home, clean_home=True, dry_run=False)
    assert not home_bin.exists()
    assert results  # at least the home-binary removal

    # Now a brand-new-style dry-run install is allowed.
    assert install_registry_agent(agent_id, dry_run=True) == agent_id
    assert list(bin_dir.iterdir()) == []


@pytest.mark.parametrize("agent_id", REGISTRY_IDS, ids=REGISTRY_IDS)
def test_dirty_broken_config_is_repaired(
    agent_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, _ = _session(tmp_path, monkeypatch)
    agent = get_registry_agent(agent_id)
    assert agent is not None
    rel = _config_rel(agent)
    if not rel:
        pytest.skip("no config.path")
    fmt = str((agent.get("config") or {}).get("format", "json"))
    if fmt == "markdown":
        pytest.skip("markdown configs are read-only")

    cfg = home / rel
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Unparseable junk per format.
    junk = {
        "json": "{not json",
        "jsonc": "{not json",
        "json5": "{not json",
        "yaml": ":\n  - [",
        "toml": "model = [unclosed",
    }[fmt]
    cfg.write_text(junk, encoding="utf-8")

    # Mark binary present so fix path treats the agent as relevant.
    result = fix_registry_agent(agent_id, home=home)
    assert result["ok"] is True
    assert any("repair" in a or "created" in a for a in result["actions"])
    text = cfg.read_text(encoding="utf-8")
    assert text != junk
    # Must parse again.
    from astroai_lab.agent import agent_config as ac

    ac.validate_config_text(agent_id, text, home=home)


def test_dirty_home_plus_managed_prefers_managed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If scratch already has the CLI, a leftover home copy is ignored for install."""
    home, bin_dir, _ = _session(tmp_path, monkeypatch)
    managed = bin_dir / "kilo"
    managed.write_text("#!/bin/sh\n", encoding="utf-8")
    managed.chmod(0o755)
    _drop_home_binary(home, "kilo")
    info = install_mod.classify_binary("kilo", home=home)
    assert info["managed"] is True
    install_mod.refuse_if_home_owned("kilo", home=home)  # must not raise


# ---------------------------------------------------------------------------
# Method dispatch matrix (one agent per method, plus every id dry-run)
# ---------------------------------------------------------------------------


def test_dispatch_npm_curl_gh_uv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _session(tmp_path, monkeypatch)
    hits: dict[str, str] = {}

    monkeypatch.setattr(
        "astroai_lab.agent.registry._install_npm",
        lambda agent: hits.setdefault("npm", agent["id"]) or agent["id"],
    )
    monkeypatch.setattr(
        "astroai_lab.agent.registry._install_curl",
        lambda agent: hits.setdefault("curl", agent["id"]) or agent["id"],
    )
    monkeypatch.setattr(
        "astroai_lab.agent.registry._install_gh_release",
        lambda agent: hits.setdefault("gh-release", agent["id"]) or agent["id"],
    )
    monkeypatch.setattr(
        "astroai_lab.agent.registry._install_uv_tool",
        lambda agent: hits.setdefault("uv-tool", agent["id"]) or agent["id"],
    )
    # Avoid TOOLS short-circuit for overlapped ids by clearing TOOLS.
    monkeypatch.setattr(install_mod, "TOOLS", {}, raising=False)

    samples = {
        "npm": "cline",
        "curl": "kilo",
        "gh-release": "codex",
        "uv-tool": "swival",
    }
    for method, agent_id in samples.items():
        hits.clear()
        assert install_registry_agent(agent_id) == agent_id
        assert method in hits


@pytest.mark.parametrize("util_id", UTILITY_IDS, ids=UTILITY_IDS)
def test_utility_dry_run_install(
    util_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session(tmp_path, monkeypatch)
    if util_id == "node":
        # node is image-baked — install_tool may no-op or check presence.
        install_mod.install_tool(util_id, dry_run=True)
        return
    install_mod.install_tool(util_id, dry_run=True)


# ---------------------------------------------------------------------------
# Codex package extras (the bug class that bit new users)
# ---------------------------------------------------------------------------


def test_codex_package_install_puts_host_and_bwrap_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, bin_dir, _ = _session(tmp_path, monkeypatch)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(install_mod, "_gh_auth_ok", lambda: False)

    def fake_curl(repo: str, asset: str, dest: Path) -> None:
        assert "codex-package" in asset
        dest.parent.mkdir(parents=True, exist_ok=True)
        pkg = tmp_path / "pkg"
        (pkg / "bin").mkdir(parents=True)
        (pkg / "codex-resources").mkdir()
        (pkg / "codex-package.json").write_text("{}\n", encoding="utf-8")
        for name in ("codex", "codex-code-mode-host"):
            p = pkg / "bin" / name
            p.write_text("#!/bin/sh\n", encoding="utf-8")
            p.chmod(0o755)
        bwrap = pkg / "codex-resources" / "bwrap"
        bwrap.write_text("#!/bin/sh\n", encoding="utf-8")
        bwrap.chmod(0o755)
        with tarfile.open(dest, "w:gz") as tf:
            for path in pkg.rglob("*"):
                if path.is_file():
                    tf.add(path, arcname=str(path.relative_to(pkg)))

    monkeypatch.setattr(install_mod, "_download_public_gh_release", fake_curl)
    from astroai_lab.agent.registry import _install_gh_release

    agent = get_registry_agent("codex")
    assert agent is not None
    assert _install_gh_release(agent) == "codex"
    assert (bin_dir / "codex").exists()
    assert (bin_dir / "codex-code-mode-host").exists()
    assert (bin_dir / "bwrap").exists()
    # Home stayed clean — new user did not pollute /arc.
    assert not (home / ".local").exists()


def test_codex_fix_adds_mcp_timeouts_for_legacy_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, _ = _session(tmp_path, monkeypatch)
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "[mcp_servers.fetch]\ncommand = \"uvx\"\nargs = [\"mcp-server-fetch\"]\nenabled = true\n",
        encoding="utf-8",
    )
    result = fix_registry_agent("codex", home=home)
    assert any("startup_timeout_sec" in a for a in result["actions"])
    assert "startup_timeout_sec = 120" in cfg.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Optional live network checks (new-user URL reachability)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("ASTROAI_LAB_NETWORK_TESTS") != "1",
    reason="set ASTROAI_LAB_NETWORK_TESTS=1 to hit real installer URLs",
)
@pytest.mark.parametrize(
    "agent_id",
    [a["id"] for a in load_registry() if a["install"]["method"] in ("curl", "gh-release")],
)
def test_live_installer_urls_reachable(agent_id: str) -> None:
    import platform
    import ssl
    import urllib.request

    agent = get_registry_agent(agent_id)
    assert agent is not None
    install = agent["install"]
    if install["method"] == "curl":
        url = str(install["source"])
    else:
        asset = str(install["asset"]).replace("{arch}", platform.machine())
        url = f"https://github.com/{install['repo']}/releases/latest/download/{asset}"

    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "astroai-lab-tests/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        assert resp.status in (200, 206)
        # Read a tiny prefix so we don't pull 100MB+ package bodies in CI.
        chunk = resp.read(64)
        assert chunk
