"""The generated dsh profile: layer content, ordering, storage, doctor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from astroai_lab import studio_profile as sp


class _DshLoader(yaml.SafeLoader):
    """SafeLoader that keeps dsh's ``!!js`` expressions as plain strings."""


_DshLoader.add_constructor(
    "tag:yaml.org,2002:js",
    lambda loader, node: loader.construct_scalar(node),
)


def load_patch(text: str) -> list:
    return yaml.load(text, Loader=_DshLoader)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DSH_HOME", raising=False)
    monkeypatch.delenv("SCRATCH", raising=False)
    monkeypatch.delenv("ASTROAI_LAB_DATA", raising=False)
    return home


# ── state routing ───────────────────────────────────────────────────────────


def test_laptop_state_is_durable_under_dsh_home(home: Path) -> None:
    state = sp.resolve_state_root(home, profile="laptop")
    assert state.path == home / ".dsh" / "state"
    assert state.durable is True
    assert state.note is None


def test_canfar_state_lands_on_scratch(home: Path, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    state = sp.resolve_state_root(home, profile="canfar", scratch=scratch)
    assert state.path.parent == scratch
    assert state.path.name.startswith(".studio-")
    assert state.durable is False
    # Never the quota-constrained home, and never silent about it.
    assert home not in state.path.parents


def test_canfar_without_scratch_warns_and_avoids_home(home: Path, tmp_path: Path) -> None:
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    state = sp.resolve_state_root(home, profile="canfar", env={"TMPDIR": str(tmp)})
    assert state.path.parent == tmp
    assert state.durable is False
    assert state.note is not None and "not persisted" in state.note


def test_scratch_env_is_honoured(home: Path, tmp_path: Path) -> None:
    scratch = tmp_path / "scr"
    scratch.mkdir()
    state = sp.resolve_state_root(home, profile="canfar", env={"SCRATCH": str(scratch)})
    assert str(state.path).startswith(str(scratch))


# ── bundle ordering ─────────────────────────────────────────────────────────


def test_desired_bundles_puts_the_team_layers_in_order() -> None:
    existing = [
        "@deepseek-ai/dsh-web-app",
        "@deepseek-ai/dsh-experimental-agent-team-web-profile",
        "@deepseek-ai/dsh-experimental-agent-team-profile",
        "@deepseek-ai/dsh-base",
    ]
    assert sp.desired_bundles(existing, with_team=True) == [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app",
        *sp.TEAM_BUNDLES,
    ]


def test_desired_bundles_drops_team_layers_and_keeps_unknowns() -> None:
    existing = [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-experimental-agent-team-profile",
        "@acme/dsh-extra",
    ]
    # The web prefix is repaired even when a manifest lost it; a bundle the user
    # added themselves keeps its position and is never dropped.
    assert sp.desired_bundles(existing, with_team=False) == [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app",
        "@acme/dsh-extra",
    ]


def test_team_bundles_missing() -> None:
    assert sp.team_bundles_missing(["@deepseek-ai/dsh-base"], with_team=True) == list(
        sp.TEAM_BUNDLES
    )
    assert sp.team_bundles_missing(["@deepseek-ai/dsh-base"], with_team=False) == []


def test_bundle_installed_checks_profile_then_fallback(home: Path) -> None:
    profile = sp.profile_dir(home)
    fallback = profile.parent / "node_modules" / "@deepseek-ai" / "dsh-base"
    fallback.mkdir(parents=True)
    (fallback / "package.json").write_text("{}", encoding="utf-8")
    assert sp.bundle_installed(profile, "@deepseek-ai/dsh-base") is True
    assert sp.bundle_installed(profile, "@deepseek-ai/dsh-web-app") is False
    assert sp.uninstalled_bundles(profile, ["@deepseek-ai/dsh-base"]) == []


# ── plan and layer content ──────────────────────────────────────────────────


def test_plan_is_pure_and_describes_a_fresh_profile(home: Path) -> None:
    plan = sp.plan_studio_profile(home, profile="laptop", with_team=True)
    assert plan.fresh_profile is True
    assert plan.bundles == (*sp.WEB_TEMPLATE_BUNDLES, *sp.TEAM_BUNDLES)
    assert plan.manifest["dsh"]["profile"]["patchReload"] == "live"
    assert plan.manifest["name"] == "dsh-profile-astroai"
    assert not plan.dir.exists()  # planning writes nothing
    assert plan.files == (
        plan.dir / "package.json",
        plan.dir / "cordis.patch.yml",
        plan.dir / "pnpm-workspace.yaml",
    )


def test_layer_targets_the_rows_it_claims_and_parses(home: Path) -> None:
    plan = sp.plan_studio_profile(home, profile="laptop", with_team=True)
    doc = load_patch(plan.layer_yaml)
    assert isinstance(doc, list)
    rows = {row.get("id"): row for row in doc if isinstance(row, dict) and "id" in row}
    assert rows["agent-presets"]["config"]["default"] == "standard"
    assert rows["bash-sandbox"]["config"]["timeoutMs"] == 600_000
    assert rows["session-query-sqlite"]["config"]["openAt"] == "first-search"
    assert rows["session-persistence-jsonl"]["config"]["root"].endswith("/sessions")
    assert rows["spill-local"]["config"]["cleanupPeriodDays"] == sp.SPILL_CLEANUP_DAYS
    insert = [row for row in doc if isinstance(row, dict) and "insert" in row]
    assert len(insert) == 1
    mcp = insert[0]["insert"][0]
    assert mcp["id"] == "mcp-astroai"
    assert mcp["name"] == "@deepseek-ai/dsh-mcp-client"
    assert mcp["config"]["transport"] == "stdio"
    assert mcp["config"]["serverName"] == "astroai"
    assert mcp["config"]["failOnStartupError"] is False
    assert mcp["config"]["cwd"] == "process.cwd()"
    assert mcp["config"]["env"]["HOME"] == "process.env.HOME"


def test_preset_roots_prefer_the_studio_root_then_the_bench(home: Path) -> None:
    plan = sp.plan_studio_profile(home, profile="laptop")
    doc = load_patch(plan.layer_yaml)
    roots = next(row for row in doc if row.get("id") == "agent-presets")["config"]["roots"]
    assert [r["path"] for r in roots] == [
        str(sp.managed_studio_preset_root(home)),
        str(sp.managed_bench_dir(home) / "presets"),
    ]
    assert all(r["trust"] == "system" for r in roots)


def test_canfar_layer_routes_state_to_scratch(home: Path, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    plan = sp.plan_studio_profile(home, profile="canfar", scratch=scratch)
    assert str(scratch) in plan.layer_yaml
    assert str(home) not in plan.layer_yaml.replace(str(home / ".astroai"), "")


def test_mcp_command_falls_back_to_the_module_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `home` keeps this off the developer's own ~/.astroai/lab/.env pin.
    monkeypatch.delenv(sp.MCP_BIN_ENV, raising=False)
    monkeypatch.setattr(sp.shutil, "which", lambda *_: None)
    command = sp.mcp_serve_command(home=tmp_path)
    assert command[1:] == ("-m", "astroai_lab", "mcp", "serve")


def test_mcp_bin_override_prefers_env_then_dotenv(tmp_path: Path) -> None:
    real = tmp_path / "astroai"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    dotenv_only = tmp_path / "other"
    dotenv_only.write_text("#!/bin/sh\n", encoding="utf-8")
    lab = tmp_path / ".astroai" / "lab"
    lab.mkdir(parents=True)
    (lab / ".env").write_text(f"{sp.MCP_BIN_ENV}={dotenv_only}\n", encoding="utf-8")
    assert sp.mcp_serve_command(home=tmp_path)[0] == str(dotenv_only)
    monkey = pytest.MonkeyPatch()
    monkey.setenv(sp.MCP_BIN_ENV, str(real))
    try:
        assert sp.mcp_serve_command(home=tmp_path)[0] == str(real)
    finally:
        monkey.undo()


def test_mcp_bin_override_ignores_a_path_that_is_gone(tmp_path: Path) -> None:
    # A stale pin must not brick the row; fall back to PATH resolution.
    missing = tmp_path / "uninstalled-astroai"
    monkey = pytest.MonkeyPatch()
    monkey.setenv(sp.MCP_BIN_ENV, str(missing))
    monkey.setattr(sp.shutil, "which", lambda *_: "/usr/bin/astroai")
    try:
        assert sp.mcp_bin_override(tmp_path) is None
        assert sp.mcp_serve_command(home=tmp_path) == ("/usr/bin/astroai", "mcp", "serve")
    finally:
        monkey.undo()


def test_mcp_row_reads_env_at_boot_not_at_generate_time(home: Path) -> None:
    """No secret and no machine-specific path may be baked into the profile."""
    row = sp.mcp_row_yaml(("astroai", "mcp", "serve"))
    assert "!!js process.env.HOME" in row
    assert str(home) not in row
    assert "!!js process.cwd()" in row


def test_agent_presets_row_restates_the_fields_it_keeps(home: Path) -> None:
    """A patch replaces the row's whole config, so required fields must be restated."""
    plan = sp.plan_studio_profile(home, profile="laptop")
    doc = load_patch(plan.layer_yaml)
    config = next(row for row in doc if row.get("id") == "agent-presets")["config"]
    assert "default" in config and "roots" in config


# ── apply ───────────────────────────────────────────────────────────────────


def fake_install(directory: Path, names: tuple[str, ...] = sp.TEAM_BUNDLES) -> None:
    """Stand in for a pnpm install so the positive path can be tested offline."""
    for name in names:
        target = directory / "node_modules" / Path(name)
        target.mkdir(parents=True)
        (target / "package.json").write_text("{}", encoding="utf-8")


def test_manifest_is_never_treated_as_foreign_and_keeps_dependencies(home: Path) -> None:
    """A JSON manifest cannot carry a comment banner, and pnpm's pins must survive."""
    directory = sp.profile_dir(home)
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(
        json.dumps(
            {
                "name": "dsh-profile-astroai",
                "private": True,
                "dependencies": {
                    "@deepseek-ai/dsh-experimental-agent-team-profile": "0.1.5-alpha.2"
                },
                "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"], "patchReload": "live"}},
            }
        ),
        encoding="utf-8",
    )
    fake_install(directory)
    result = sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop"), install_bundles=False
    )
    assert not any("kept" in action for action in result["actions"])
    manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    assert manifest["dsh"]["profile"]["bundles"] == [*sp.WEB_TEMPLATE_BUNDLES, *sp.TEAM_BUNDLES]
    assert manifest["dependencies"]["@deepseek-ai/dsh-experimental-agent-team-profile"] == (
        "0.1.5-alpha.2"
    )
    assert manifest["dsh"]["profile"]["patchReload"] == "live"


def test_recovery_reinstates_the_team_bundles_in_the_manifest(home: Path) -> None:
    """The degrade path must be reversible by a later successful --prepare."""
    sp.apply_studio_profile(sp.plan_studio_profile(home, profile="laptop"), install_bundles=False)
    manifest_path = sp.profile_dir(home) / "package.json"
    degraded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert degraded["dsh"]["profile"]["bundles"] == list(sp.WEB_TEMPLATE_BUNDLES)

    fake_install(sp.profile_dir(home))
    result = sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop"), install_bundles=False
    )
    assert result["degraded"] is False
    recovered = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert recovered["dsh"]["profile"]["bundles"] == [*sp.WEB_TEMPLATE_BUNDLES, *sp.TEAM_BUNDLES]


def test_apply_writes_manifest_layer_and_workspace(home: Path) -> None:
    plan = sp.plan_studio_profile(home, profile="laptop", with_team=True)
    fake_install(plan.dir)
    result = sp.apply_studio_profile(plan, install_bundles=False)
    assert result["degraded"] is False
    manifest = json.loads((plan.dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["dsh"]["profile"]["bundles"] == list(plan.bundles)
    assert (plan.dir / "cordis.patch.yml").read_text(encoding="utf-8") == plan.layer_yaml
    assert (plan.dir / "pnpm-workspace.yaml").read_text(encoding="utf-8") == "nodeLinker: hoisted\n"
    assert any("created profile astroai" in action for action in result["actions"])


def test_apply_is_idempotent(home: Path) -> None:
    plan = sp.plan_studio_profile(home, profile="laptop")
    fake_install(plan.dir)
    sp.apply_studio_profile(plan, install_bundles=False)
    second = sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop"), install_bundles=False
    )
    assert not any(action.startswith("wrote") for action in second["actions"])
    assert second["degraded"] is False
    assert second["bundles"] == list(plan.bundles)
    assert "created profile astroai" not in " ".join(second["actions"])


def test_no_install_retries_every_run_and_never_leaves_a_broken_profile(home: Path) -> None:
    """A missing install degrades now and is retried on the next --prepare."""
    first = sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop"), install_bundles=False
    )
    assert first["degraded"] is True
    assert first["bundles"] == list(sp.WEB_TEMPLATE_BUNDLES)
    # Installing the packs turns the layers back on without manual file edits.
    fake_install(sp.profile_dir(home))
    second = sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop"), install_bundles=False
    )
    assert second["degraded"] is False
    assert second["bundles"] == [*sp.WEB_TEMPLATE_BUNDLES, *sp.TEAM_BUNDLES]


def test_degrade_drops_team_layers_so_the_profile_still_boots(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = sp.plan_studio_profile(home, profile="laptop", with_team=True)
    monkeypatch.setattr(sp.shutil, "which", lambda name: None if name == "pnpm" else "/usr/bin/dsh")
    result = sp.apply_studio_profile(plan, dsh_bin="/usr/bin/dsh")
    assert result["degraded"] is True
    assert result["bundles"] == list(sp.WEB_TEMPLATE_BUNDLES)
    manifest = json.loads((plan.dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["dsh"]["profile"]["bundles"] == list(sp.WEB_TEMPLATE_BUNDLES)
    assert any("pnpm" in action for action in result["actions"])
    assert any("team bundles removed" in action for action in result["actions"])


def test_foreign_layer_and_workspace_are_never_clobbered(home: Path) -> None:
    """A hand-written profile layer, or an `allowBuilds` entry, must survive."""
    directory = sp.profile_dir(home)
    directory.mkdir(parents=True)
    (directory / "cordis.patch.yml").write_text("- id: mine\n", encoding="utf-8")
    workspace = "nodeLinker: hoisted\nallowBuilds:\n  turtle-ui: true\n"
    (directory / "pnpm-workspace.yaml").write_text(workspace, encoding="utf-8")

    result = sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop", with_team=False),
        install_bundles=False,
    )

    assert (directory / "cordis.patch.yml").read_text(encoding="utf-8") == "- id: mine\n"
    assert (directory / "pnpm-workspace.yaml").read_text(encoding="utf-8") == workspace
    assert any("kept" in action for action in result["actions"])
    # …and --force regenerates only what is ours.
    sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop", with_team=False),
        install_bundles=False,
        force=True,
    )
    assert sp.LAYER_MARK in (directory / "cordis.patch.yml").read_text(encoding="utf-8")
    assert (directory / "pnpm-workspace.yaml").read_text(encoding="utf-8") == workspace


def test_dry_run_writes_nothing(home: Path) -> None:
    plan = sp.plan_studio_profile(home, profile="laptop")
    result = sp.apply_studio_profile(plan, dry_run=True)
    assert not plan.dir.exists()
    assert result["degraded"] is False
    assert any(action.startswith("would run") for action in result["actions"])


# ── doctor ──────────────────────────────────────────────────────────────────


def test_doctor_reports_a_missing_dsh_as_fatal(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp.shutil, "which", lambda *_: None)
    report = sp.doctor(home, profile="laptop", probe_handshake=False)
    assert report["fatal"] is True
    dsh = next(check for check in report["checks"] if check["name"] == "dsh")
    assert "npx" in dsh["hint"]


def test_doctor_reports_profile_state(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp.shutil, "which", lambda *_: "/usr/bin/dsh")
    monkeypatch.setattr(sp, "dsh_version", lambda *_: "0.1.5-rc.2")
    monkeypatch.setattr(sp, "dump_config", lambda *a, **k: (0, "tree", ""))
    sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop", with_team=False),
        install_bundles=False,
    )
    report = sp.doctor(home, profile="laptop", probe_handshake=False, with_team=False)
    names = {check["name"] for check in report["checks"]}
    assert {"dsh", "profile", "profile-layer-order", "composition", "state-root"} <= names
    assert report["fatal"] is False


def test_doctor_flags_a_broken_composition(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp.shutil, "which", lambda *_: "/usr/bin/dsh")
    monkeypatch.setattr(sp, "dsh_version", lambda *_: "0.1.5-rc.2")
    sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop", with_team=False),
        install_bundles=False,
    )
    monkeypatch.setattr(
        sp,
        "dump_config",
        lambda *a, **k: (1, "", "Cannot find module '@deepseek-ai/dsh-base'\nNode.js v24"),
    )
    report = sp.doctor(home, profile="laptop", probe_handshake=False, with_team=False)
    assert report["fatal"] is True
    composition = next(c for c in report["checks"] if c["name"] == "composition")
    assert "Cannot find module" in composition["detail"]


def test_probe_mcp_handshakes_with_the_real_server() -> None:
    import sys

    report = sp.probe_mcp_report((sys.executable, "-m", "astroai_lab", "mcp", "serve"))
    assert report.ok is True, report.detail()
    assert report.server.startswith("astroai")
    # The tool inventory is the point of the probe: a server answering with only
    # the cluster half is the failure worth catching.
    assert "job_submit" in report.tools
    assert report.missing_canfar_tools == ()


def test_probe_env_drops_the_working_tree_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "src")
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/somewhere")
    env = sp.probe_env()
    # dsh scrubs the ambient environment; a probe that inherited PYTHONPATH would
    # answer with the working tree's CLI instead of the baked one.
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env
    assert "PATH" in env


def test_probe_reports_a_cli_missing_the_canfar_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = tmp_path / "astroai"
    stub.write_text(
        "#!/bin/sh\n"
        'echo \'{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"astroai",'
        '"version":"0.4.0"}}}\'\n'
        'echo \'{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"job_list"},'
        '{"name":"job_submit"}]}}\'\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    report = sp.probe_mcp_report((str(stub), "mcp", "serve"))
    assert report.ok is True
    assert "session_resources" in report.missing_canfar_tools
    assert "missing" in report.detail()


def test_baked_mcp_command_reads_the_generated_row(home: Path) -> None:
    sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop", with_team=False),
        install_bundles=False,
    )
    patch = home / ".dsh" / "profiles" / "astroai" / "cordis.patch.yml"
    command = sp.baked_mcp_command(patch)
    assert command is not None
    assert command[1:] == ("mcp", "serve")


def test_baked_mcp_command_ignores_a_foreign_layer(tmp_path: Path) -> None:
    foreign = tmp_path / "cordis.patch.yml"
    foreign.write_text("# hand-written\n- insert:\n    - id: mine\n", encoding="utf-8")
    assert sp.baked_mcp_command(foreign) is None
    assert sp.baked_mcp_command(tmp_path / "absent.yml") is None


def test_doctor_probes_the_baked_row_not_the_path(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp.shutil, "which", lambda *_: "/usr/bin/dsh")
    monkeypatch.setattr(sp, "dsh_version", lambda *_: "0.1.5-rc.2")
    monkeypatch.setattr(sp, "dump_config", lambda *a, **k: (0, "tree", ""))
    sp.apply_studio_profile(
        sp.plan_studio_profile(home, profile="laptop", with_team=False),
        install_bundles=False,
    )
    baked = sp.baked_mcp_command(home / ".dsh" / "profiles" / "astroai" / "cordis.patch.yml")
    assert baked is not None
    # Only now does the PATH resolve differently from what was baked in.
    monkeypatch.setattr(sp, "mcp_serve_command", lambda **_: ("/stale/astroai", "mcp", "serve"))
    probed: list[tuple[str, ...]] = []

    def fake_probe(command: tuple[str, ...], **_: object) -> sp.McpProbe:
        probed.append(command)
        return sp.McpProbe(ok=True, server="astroai 0.5.0", tools=sp.CANFAR_MCP_TOOLS)

    monkeypatch.setattr(sp, "probe_mcp_report", fake_probe)
    sp.doctor(home, profile="laptop", probe_handshake=True, with_team=False)
    assert probed
    assert probed[0] == baked
    assert probed[0][0] != "/stale/astroai"


def test_probe_mcp_reports_a_broken_command() -> None:
    ok, detail = sp.probe_mcp(("definitely-not-a-command", "serve"), timeout=5)
    assert ok is False
    assert detail


def test_first_error_line_prefers_the_cause() -> None:
    stderr = "\n   at foo\nError: unknown bundle '@x'\nNode.js v24\n"
    assert sp._first_error_line(stderr) == "Error: unknown bundle '@x'"
    assert sp._first_error_line("") == "(no stderr)"
