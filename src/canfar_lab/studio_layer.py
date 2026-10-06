"""Studio profile YAML, plan, and apply."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from canfar_lab.errors import LabError
from canfar_lab.studio_bundles import (
    baked_bundles,
    baked_plugins_root,
    bundle_installed,
    desired_bundles,
    ensure_baked_plugins,
    ensure_vendored_plugin,
    uninstalled_bundles,
    vendored_dependencies,
)
from canfar_lab.studio_paths import (
    ASTROAI_EXTRA_BUNDLES,
    DSH_INSTALL_HINT,
    LAYER_MARK,
    MARKET_BUNDLE,
    MCP_BIN_ENV,
    MCP_TOOL_TIMEOUT_MS,
    PLUGIN_SWITCH_RE,
    PROFILE_BASH_TIMEOUT_SEC,
    PROFILE_MANIFEST_FILENAME,
    PROFILE_PATCH_FILENAME,
    PROFILE_WORKSPACE_FILENAME,
    SPILL_CLEANUP_DAYS,
    STUDIO_PROFILE_NAME,
    TEAM_BUNDLES,
    StateRoot,
    StudioProfile,
    dsh_home,
    profile_dir,
    resolve_state_root,
)
from canfar_lab.utils.subprocess import run_cmd


def _yaml_scalar(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _js(expression: str) -> str:
    """A dsh ``!!js`` config expression, evaluated by the loader at boot."""
    return f"!!js {expression}"


@dataclass(frozen=True)
class StudioPlan:
    """Everything a real run would write and run, computed without touching disk."""

    name: str
    dir: Path
    profile: StudioProfile
    state: StateRoot
    with_team: bool
    bundles: tuple[str, ...]
    manifest: dict[str, Any]
    layer_yaml: str
    workspace_yaml: str
    mcp_command: tuple[str, ...]
    files: tuple[Path, ...]
    commands: tuple[tuple[str, ...], ...]
    notes: tuple[str, ...] = ()
    fresh_profile: bool = False
    baked_root: Path | None = None
    baked: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dir": str(self.dir),
            "profile": self.profile,
            "state_root": str(self.state.path),
            "state_durable": self.state.durable,
            "with_team": self.with_team,
            "bundles": list(self.bundles),
            "mcp_command": list(self.mcp_command),
            "files": [str(p) for p in self.files],
            "commands": [list(c) for c in self.commands],
            "notes": list(self.notes),
            "fresh_profile": self.fresh_profile,
            "baked": list(self.baked),
        }


def mcp_serve_command(
    *, astroai_bin: str | None = None, home: Path | None = None
) -> tuple[str, ...]:
    """Argv for ``canfar lab mcp serve`` (the CANFAR job/cluster MCP server).

    Resolution order: an explicit path, then ``ASTROAI_STUDIO_MCP_BIN`` (in the
    environment or in ``~/.astroai/lab/.env``, so a working checkout can pin the
    row without every later ``--prepare`` reverting it to whatever ``PATH``
    happened to hold), then ``canfar-lab`` on ``PATH``, then this interpreter.
    """
    explicit = astroai_bin or mcp_bin_override(home)
    if explicit:
        return (explicit, "mcp", "serve")
    for name in ("canfar-lab",):
        found = shutil.which(name)
        if found:
            return (found, "mcp", "serve")
    return (sys.executable or "python3", "-m", "canfar_lab", "mcp", "serve")


def mcp_bin_override(home: Path | None = None) -> str | None:
    """The pinned Studio MCP binary, from the environment or the lab dotenv."""
    home = home or Path.home()
    value = os.environ.get(MCP_BIN_ENV, "").strip()
    if not value:
        dotenv = home / ".astroai" / "lab" / ".env"
        if dotenv.is_file():
            try:
                from canfar_lab.agent.setup import _read_dotenv_value

                value = (_read_dotenv_value(dotenv, MCP_BIN_ENV) or "").strip()
            except Exception:  # noqa: BLE001 — a pin we cannot read is not fatal
                value = ""
    if not value:
        return None
    expanded = Path(value).expanduser()
    if not expanded.exists():
        return None
    return str(expanded)


def mcp_row_yaml(command: tuple[str, ...]) -> str:
    """The ``dsh-mcp-client`` row, as an ``insert`` entry.

    ``env`` is required by the row schema and is merged over a *scrubbed*
    ambient environment, so the handful of variables the job tools need are
    read at boot with ``!!js`` rather than baked in as literals — no secret and
    no machine-specific path ever lands in the profile directory.
    """
    args = ", ".join(_yaml_scalar(part) for part in command[1:])
    user_expr = _js("process.env.USER ?? ''")
    shell_expr = _js("process.env.SHELL ?? '/bin/bash'")
    scratch_expr = _js("process.env.SCRATCH ?? ''")
    project_expr = _js("process.env.PROJECT ?? ''")
    return "\n".join(
        [
            "# AstroAI hub MCP server: CANFAR job and cluster tools plus this",
            "# session's own resource snapshot, so a Studio chat can launch real",
            "# compute and report on it. An MCP server command is trusted,",
            "# unsandboxed executable code — this one is our own CLI.",
            "- insert:",
            "    - id: mcp-astroai",
            "      name: '@deepseek-ai/dsh-mcp-client'",
            "      config:",
            "        transport: stdio",
            "        serverName: astroai",
            f"        command: {_yaml_scalar(command[0])}",
            f"        args: [{args}]",
            "        env:",
            f"          HOME: {_js('process.env.HOME')}",
            f"          PATH: {_js('process.env.PATH')}",
            f"          USER: {user_expr}",
            f"          SHELL: {shell_expr}",
            f"          SCRATCH: {scratch_expr}",
            f"          PROJECT: {project_expr}",
            "        cwd: " + _js("process.cwd()"),
            f"        toolCallTimeoutMs: {MCP_TOOL_TIMEOUT_MS}",
            "        failOnStartupError: false",
        ]
    )


def studio_layer_yaml(
    *,
    profile: StudioProfile,
    state: StateRoot,
    command: tuple[str, ...],
    with_team: bool = True,
    market: bool = False,
    switches: Iterable[tuple[str, bool]] = (),
    carried: str = "",
) -> str:
    """The profile's own ``cordis.patch.yml``.

    A patch row replaces the targeted row's **whole** config (there is no deep
    merge), so every field kept from the bundle layer is restated here. Each
    row below targets a row that exists in ``dsh-base`` or ``dsh-web-app`` at
    dsh 0.2.1; the doctor re-checks that with ``--dump-config``.
    ``switches`` are plugin on/off rows carried over from the previous file.
    """
    timeout_ms = PROFILE_BASH_TIMEOUT_SEC[profile] * 1000
    layer_path = " → ".join(
        bundle_basename(name) for name in desired_bundles([], with_team=with_team)
    )
    lines = [
        "# AstroAI Studio — the `astroai` profile's own patch layer.",
        "#",
        "# Generated by `canfar lab studio --prepare`; hand edits are kept only until",
        "# the next --prepare regenerates this file. Edit it, then move the change",
        "# into canfar_lab/studio_profile.py so it survives. Plugin on/off rows",
        "# (`- id: X` + `disabled: true|false`) are the exception: they are kept.",
        "#",
        "# Applied after the profile's bundle layers, in order:",
        f"#   {layer_path} → this file → the repo's .dsh/cordis.patch.yml (--patch)",
        "#",
        f"# State root: {state.path}" + (" (durable)" if state.durable else " (this session)"),
    ]
    if state.note:
        lines.append(f"# WARNING: {state.note}")
    lines += [
        "",
        "# ── agent preset registry ──────────────────────────────────────────────",
        "# dsh 0.2 replaced directory roots with declarative `@deepseek-ai/dsh-agent-preset`",
        "# rows. A patch replaces the whole config, so `default` is restated and",
        "# `roots` is not: the registry rejects that field and fails the profile.",
        "- id: agent-preset-registry",
        "  config:",
        "    default: standard",
        "",
        "# ── session storage ────────────────────────────────────────────────────",
        "# Session logs and the full-text index are append-heavy and unbounded, so",
        "# they follow the state root instead of the quota-constrained $HOME.",
        "- id: session-persistence-jsonl",
        "  config:",
        f"    root: {_yaml_scalar(str(state.path / 'sessions'))}",
        "",
        "- id: session-query-sqlite",
        "  config:",
        f"    path: {_yaml_scalar(str(state.path / 'sessions.sqlite'))}",
        "    openAt: first-search",
        "",
        "# Oversized tool results spill to the state root, not to a temp dir that",
        "# vanishes mid-session; retention stays explicit so scratch cannot fill up.",
        "- id: spill-local",
        "  config:",
        f"    root: {_yaml_scalar(str(state.path / 'spill'))}",
        f"    cleanupPeriodDays: {SPILL_CLEANUP_DAYS}",
        "",
        "# ── shell ──────────────────────────────────────────────────────────────",
        f"# {profile}: {PROFILE_BASH_TIMEOUT_SEC[profile]}s of headroom for pixi solves,",
        "# native rebuilds and bench runs (the shipped default is 60s).",
        "- id: bash-sandbox",
        "  config:",
        f"    timeoutMs: {timeout_ms}",
        "",
        "# ── tools ──────────────────────────────────────────────────────────────",
        mcp_row_yaml(command),
        "",
    ]
    if market and profile == "canfar":
        lines += [
            "# ── plugin market ──────────────────────────────────────────────────────",
            "# The session supervisor restarts dsh; a second, detached copy started by",
            "# the market's Restart button would hold the port and be killed with the",
            "# session. Changes that need a restart apply at the next session start.",
            "- id: dsh-market",
            "  name: dshmarket",
            "  config:",
            "    allowRestart: false",
            "",
        ]
    switches = list(switches)
    if switches:
        lines += [
            "# ── plugin switches ────────────────────────────────────────────────────",
            "# Written by the plugin market (enable/disable); kept across --prepare.",
        ]
        for row_id, disabled in switches:
            lines += [f"- id: {row_id}", f"  disabled: {'true' if disabled else 'false'}"]
        lines.append("")
    body = "\n".join(lines)
    if carried.strip():
        body = body.rstrip() + "\n\n" + carried.strip() + "\n"
    return body


def plugin_switches(text: str | None) -> list[tuple[str, bool]]:
    """The ``- id: X`` / ``disabled:`` rows in a patch file, last one per id winning."""
    if not text:
        return []
    found: dict[str, bool] = {}
    for match in PLUGIN_SWITCH_RE.finditer(text.replace("\r\n", "\n")):
        row_id = match.group(1)
        found.pop(row_id, None)
        found[row_id] = match.group(2) == "true"
    return list(found.items())


# dsh 0.1.7+ persists Models and the welcome acknowledgement in the active
# profile patch. A leftover settings.yaml is imported once and renamed.
# --prepare rewrites this file, so those rows are copied forward.
PRESERVED_CONFIG_IDS = ("llm-pi-ai", "ui-settings-general")


def _split_patch(text: str) -> tuple[str, list[str]]:
    lines = text.replace("\r\n", "\n").splitlines(keepends=True)
    preamble: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("- "):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return "".join(preamble), ["".join(block) for block in blocks]


def _block_id(block: str) -> str | None:
    first = block.splitlines()[0] if block else ""
    if not first.startswith("- id:"):
        return None
    return first.split(":", 1)[1].strip().strip("'\"")


def _load_block(block: str) -> dict[str, Any] | None:
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    if isinstance(loaded, list) and loaded and isinstance(loaded[0], dict):
        return loaded[0]
    if isinstance(loaded, dict):
        return loaded
    return None


def _render_row(row_id: str, config: dict[str, Any]) -> str:
    text = yaml.safe_dump([{"id": row_id, "config": config}], sort_keys=False)
    if not text.endswith("\n"):
        text += "\n"
    return text


def preserved_config_rows(text: str | None) -> str:
    """Profile-patch rows dsh owns, copied across a Studio regenerate."""
    if not text or LAYER_MARK not in text:
        return ""
    _, blocks = _split_patch(text)
    kept = [block.strip("\n") for block in blocks if _block_id(block) in PRESERVED_CONFIG_IDS]
    if not kept:
        return ""
    header = (
        "# ── settings carried across --prepare ────────────────────────────────\n"
        "# dsh 0.1.7+ stores Models and the welcome acknowledgement in this file.\n"
        "# Regenerating the layer keeps these rows.\n"
    )
    return header + "\n\n".join(kept) + "\n"


def settings_yaml_imported(home: Path) -> bool:
    """True once dsh has renamed ``settings.yaml`` to ``settings.yaml.imported``."""
    return (dsh_home(home) / "settings.yaml.imported").is_file()


def studio_patch_path(home: Path) -> Path:
    return profile_dir(home) / PROFILE_PATCH_FILENAME


def merge_llm_provider(path: Path, provider_id: str, entry: dict[str, Any] | None) -> bool:
    """Merge one ``llm-pi-ai`` provider into a Studio patch. ``None`` deletes it.

    A patch without the Studio banner is user-owned and is left alone. Returns
    False when nothing changed or the existing row cannot be parsed.
    """
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if LAYER_MARK not in text:
        return False
    preamble, blocks = _split_patch(text)
    index = next((i for i, block in enumerate(blocks) if _block_id(block) == "llm-pi-ai"), None)
    config: dict[str, Any] = {}
    if index is not None:
        row = _load_block(blocks[index])
        if row is None:
            return False
        raw = row.get("config")
        config = dict(raw) if isinstance(raw, dict) else {}
    providers = config.get("providers")
    providers = dict(providers) if isinstance(providers, dict) else {}
    if entry is None:
        if provider_id not in providers:
            return False
        del providers[provider_id]
    elif providers.get(provider_id) == entry:
        return False
    else:
        providers[provider_id] = entry
    config["providers"] = providers
    rendered = _render_row("llm-pi-ai", config)
    if index is None:
        blocks.append(rendered)
    else:
        blocks[index] = rendered
    new = preamble + "".join(block if block.endswith("\n") else block + "\n" for block in blocks)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def merge_patch_field(path: Path, row_id: str, field: str, value: str) -> bool:
    """Set one config field on a Studio-owned patch row."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if LAYER_MARK not in text:
        return False
    preamble, blocks = _split_patch(text)
    index = next((i for i, block in enumerate(blocks) if _block_id(block) == row_id), None)
    config: dict[str, Any] = {}
    if index is not None:
        row = _load_block(blocks[index])
        if row is None:
            return False
        raw = row.get("config")
        config = dict(raw) if isinstance(raw, dict) else {}
    if config.get(field) == value:
        return False
    config[field] = value
    rendered = _render_row(row_id, config)
    if index is None:
        blocks.append(rendered)
    else:
        blocks[index] = rendered
    new = preamble + "".join(block if block.endswith("\n") else block + "\n" for block in blocks)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def llm_provider_ids_in_patch(home: Path) -> list[str]:
    text = _read_text(studio_patch_path(home))
    if not text:
        return []
    _, blocks = _split_patch(text)
    for block in blocks:
        if _block_id(block) != "llm-pi-ai":
            continue
        row = _load_block(block)
        if not row:
            return []
        providers = (row.get("config") or {}).get("providers") or {}
        if isinstance(providers, dict):
            return sorted(str(name) for name in providers)
        return []
    return []


def bundle_basename(name: str) -> str:
    """``@deepseek-ai/dsh-web-app`` → ``web-app`` (for comments and reports)."""
    return name.rsplit("/", 1)[-1]


def manifest_document(
    bundles: Iterable[str],
    *,
    profile_name: str = STUDIO_PROFILE_NAME,
    base: dict[str, Any] | None = None,
    pins: dict[str, str] | None = None,
    defaults: dict[str, str] | None = None,
) -> dict:
    """The profile manifest: our bundle list over whatever dsh and pnpm wrote.

    ``dsh plugin`` records installed packs in ``dependencies`` and rewrites
    ``dsh.profile.bundles`` itself, so this **merges** rather than replaces —
    regenerating the manifest from scratch would silently drop the dependency
    pins that keep a bundle resolvable. On a fresh profile it produces exactly
    what dsh's own ``initializeProfileFromDefault`` writes. ``pins`` always win
    (the vendored ``file:`` paths move with the install); ``defaults`` only
    fill a gap, so a plugin the person updated keeps their version.
    """
    doc: dict[str, Any] = dict(base or {})
    doc.setdefault("name", f"dsh-profile-{profile_name}")
    doc.setdefault("private", True)
    dependencies = doc.get("dependencies")
    dependencies = dict(dependencies) if isinstance(dependencies, dict) else {}
    for name, spec in (defaults or {}).items():
        dependencies.setdefault(name, spec)
    dependencies.update(pins or {})
    doc["dependencies"] = dependencies
    section = doc.get("dsh")
    section = dict(section) if isinstance(section, dict) else {}
    profile = section.get("profile")
    profile = dict(profile) if isinstance(profile, dict) else {}
    profile["bundles"] = list(bundles)
    profile.setdefault("patchReload", "live")
    section["profile"] = profile
    doc["dsh"] = section
    return doc


def read_manifest(path: Path) -> dict[str, Any]:
    """Parse an existing profile manifest; ``{}`` when absent or unreadable."""
    text = _read_text(path)
    if text is None:
        return {}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return doc if isinstance(doc, dict) else {}


def workspace_document() -> str:
    """``pnpm-workspace.yaml`` for the profile dir (hoisted, as dsh expects)."""
    return "nodeLinker: hoisted\n"


def read_bundles(path: Path) -> list[str]:
    """Best-effort read of ``dsh.profile.bundles``; ``[]`` when unreadable."""
    text = _read_text(path / PROFILE_MANIFEST_FILENAME)
    if text is None:
        return []
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return []
    bundles = ((doc.get("dsh") or {}).get("profile") or {}).get("bundles")
    if not isinstance(bundles, list):
        return []
    return [str(item) for item in bundles]


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def plan_studio_profile(
    home: Path,
    *,
    profile: StudioProfile,
    with_team: bool = True,
    scratch: Path | None = None,
    env: dict[str, str] | None = None,
    astroai_bin: str | None = None,
) -> StudioPlan:
    """Compute the whole Studio profile without touching disk.

    An existing profile keeps its bundle list (installed, possibly user-edited)
    and only gains the Team layers and the required order; a fresh one starts
    from the shipped ``web`` template's list.
    """
    directory = profile_dir(home)
    existing = read_bundles(directory)
    fresh = not existing
    baked_root = baked_plugins_root(env)
    baked = baked_bundles(baked_root)
    bundles = desired_bundles(existing, with_team=with_team, baked=baked)

    state = resolve_state_root(home, profile=profile, scratch=scratch, env=env)
    command = mcp_serve_command(astroai_bin=astroai_bin, home=home)
    previous = _read_text(directory / PROFILE_PATCH_FILENAME)
    ours = bool(previous and LAYER_MARK in previous)
    layer = studio_layer_yaml(
        profile=profile,
        state=state,
        command=command,
        with_team=with_team,
        market=MARKET_BUNDLE in bundles,
        switches=plugin_switches(previous) if ours else (),
        carried=preserved_config_rows(previous) if ours else "",
    )

    notes: list[str] = []
    if state.note:
        notes.append(state.note)
    if not with_team:
        notes.append(
            "Team layers are off (--no-team): the roster, durable mailbox and "
            "shared task board stay unavailable."
        )
    # Baked plugins are copied by apply, never fetched.
    uninstalled = [name for name in uninstalled_bundles(directory, bundles) if name not in baked]

    files = (
        directory / PROFILE_MANIFEST_FILENAME,
        directory / PROFILE_PATCH_FILENAME,
        directory / PROFILE_WORKSPACE_FILENAME,
    )
    commands: list[tuple[str, ...]] = []
    if uninstalled:
        # Only `dsh plugin add` can install a bundle, and only pnpm knows how.
        commands.append(("dsh", "plugin", "--profile", STUDIO_PROFILE_NAME, "add", *uninstalled))
    return StudioPlan(
        name=STUDIO_PROFILE_NAME,
        dir=directory,
        profile=profile,
        state=state,
        with_team=with_team,
        bundles=tuple(bundles),
        manifest=manifest_document(
            bundles,
            base=read_manifest(directory / PROFILE_MANIFEST_FILENAME),
            pins=vendored_dependencies(),
            defaults=baked,
        ),
        layer_yaml=layer,
        workspace_yaml=workspace_document(),
        mcp_command=command,
        files=files,
        commands=tuple(commands),
        notes=tuple(notes),
        fresh_profile=fresh,
        baked_root=baked_root,
        baked=tuple(baked),
    )


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


def apply_studio_profile(
    plan: StudioPlan,
    *,
    dry_run: bool = False,
    install_bundles: bool = True,
    dsh_bin: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write the profile files, then install any declared bundle that is absent.

    Returns ``{actions, degraded, bundles}`` where ``bundles`` is the list the
    profile ends up with. A failed install must not leave a profile that cannot
    boot: the Team layers are dropped, the manifest is rewritten without them,
    and the caller is told — a silent half-install would break every launch.
    """
    actions: list[str] = []
    degraded = False
    if not dry_run:
        plan.dir.mkdir(parents=True, exist_ok=True)

    # Vendored extras install even when `--no-install` skips pnpm (CANFAR Connect path).
    for name in ASTROAI_EXTRA_BUNDLES:
        action = ensure_vendored_plugin(plan.dir, name, dry_run=dry_run)
        if action:
            actions.append(action)
    action = ensure_baked_plugins(plan.dir, plan.baked_root, plan.baked, dry_run=dry_run)
    if action:
        actions.append(action)

    # `pnpm-workspace.yaml` is create-only: a user may have added an
    # `allowBuilds` entry there to permit a git-hosted plugin's build, and
    # rewriting the file would silently break their installs.
    workspace = plan.dir / PROFILE_WORKSPACE_FILENAME
    if not workspace.exists() and not dry_run:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        workspace.write_text(plan.workspace_yaml, encoding="utf-8")
        actions.append(f"wrote {workspace}")

    # The patch layer is guarded: a profile's `cordis.patch.yml` is also where a
    # person writes their own layer, so a file that is not ours is left alone.
    # The manifest is never guarded — it is machine-written, and its bundle list
    # is exactly what a later install has to repair.
    layer_path = plan.dir / PROFILE_PATCH_FILENAME
    if _is_foreign(layer_path) and not force:
        actions.append(
            f"kept {layer_path} (not generated by Studio — re-run with `--force` to replace)"
        )
    elif _write_if_changed(layer_path, plan.layer_yaml, dry_run=dry_run):
        actions.append(f"wrote {layer_path}")

    manifest_path = plan.dir / PROFILE_MANIFEST_FILENAME
    if _write_if_changed(
        manifest_path, json.dumps(plan.manifest, indent=2) + "\n", dry_run=dry_run
    ):
        actions.append(f"wrote {manifest_path}")

    if plan.fresh_profile:
        actions.append(f"created profile {plan.name} from the shipped `web` template")

    if plan.commands:
        if install_bundles:
            degraded = not _install_bundles(plan, actions, dry_run=dry_run, dsh_bin=dsh_bin)
        elif not dry_run:
            # `--no-install` means "do not fetch", never "leave a profile that
            # cannot boot": an image-baked setup resolves every bundle already.
            unresolved = uninstalled_bundles(plan.dir, plan.bundles)
            if unresolved:
                degraded = True
                actions.append(
                    "declared but not installed: "
                    + ", ".join(unresolved)
                    + " — install them, or re-run without `--no-install`"
                )
        if degraded:
            _drop_team_layers(plan, actions, dry_run=dry_run)

    for note in plan.notes:
        actions.append(f"note: {note}")
    # Every declared bundle resolves unless the install failed and the Team
    # layers were dropped from the manifest.
    effective = (
        [name for name in plan.bundles if name not in TEAM_BUNDLES]
        if degraded
        else list(plan.bundles)
    )
    return {"actions": actions, "degraded": degraded, "bundles": effective}


def _install_bundles(
    plan: StudioPlan,
    actions: list[str],
    *,
    dry_run: bool,
    dsh_bin: str | None,
) -> bool:
    """Install the missing bundles; True when every one now resolves."""
    if dry_run:
        actions.append("would run: " + " ".join(plan.commands[0]))
        return True
    dsh = dsh_bin
    if dsh is None:
        from canfar_lab import studio as studio_mod

        dsh = studio_mod.dsh_binary()
    if dsh is None:
        actions.append(
            "TEAM LAYERS UNAVAILABLE: no `dsh` executable — run "
            f"`{DSH_INSTALL_HINT}` and then `canfar lab studio --prepare` again"
        )
        return False
    missing = uninstalled_bundles(plan.dir, list(plan.bundles))

    # `dsh plugin` forwards to pnpm inside the profile directory and reconciles
    # dsh.profile.bundles itself. pnpm is not optional: it is also what keeps the
    # profile's node_modules from shadowing the installation's own in-box
    # bundles, so a plain `npm install` in the profile dir is *not* an
    # equivalent fallback (it pulls a version-skewed public copy of dsh-base).
    launched = " ".join(("dsh", *plan.commands[0][1:]))
    if shutil.which("pnpm") is None:
        actions.append(
            "TEAM LAYERS UNAVAILABLE: no `pnpm` on PATH (`dsh plugin` needs it). "
            "Enable it with `corepack enable pnpm` and then re-run `canfar lab studio --prepare`"
        )
        return False
    try:
        run_cmd([dsh, *plan.commands[0][1:]], capture=True, timeout=1800)
    except LabError as exc:
        actions.append(f"TEAM LAYERS UNAVAILABLE: {exc}\n    retry with: {launched}")
        return False
    unresolved = [name for name in missing if not bundle_installed(plan.dir, name)]
    if unresolved:
        actions.append(
            "TEAM LAYERS UNAVAILABLE: still unresolved after install: " + ", ".join(unresolved)
        )
        return False
    actions.append("installed bundles via pnpm: " + ", ".join(missing))
    return True


def _drop_team_layers(plan: StudioPlan, actions: list[str], *, dry_run: bool) -> None:
    """Rewrite the manifest without the Team bundles so the profile still boots.

    Their ``dependencies`` entries go too: dsh reconciles the bundle list from
    installed dependencies, so a leftover pin would silently re-add a pack that
    just failed to install.
    """
    path = plan.dir / PROFILE_MANIFEST_FILENAME
    current = read_manifest(path) or plan.manifest
    bundles = [
        name
        for name in (current.get("dsh", {}).get("profile", {}).get("bundles") or plan.bundles)
        if name not in TEAM_BUNDLES
    ]
    doc = manifest_document(bundles, base=current)
    dependencies = dict(doc.get("dependencies") or {})
    for name in TEAM_BUNDLES:
        dependencies.pop(name, None)
    doc["dependencies"] = dependencies
    if _write_if_changed(path, json.dumps(doc, indent=2) + "\n", dry_run=dry_run):
        actions.append("team bundles removed from dsh.profile.bundles (profile still boots)")


def _is_foreign(path: Path) -> bool:
    """True when a file exists but was not generated by us."""
    text = _read_text(path)
    return text is not None and text.strip() != "" and LAYER_MARK not in text


def _write_if_changed(path: Path, body: str, *, dry_run: bool) -> bool:
    if _read_text(path) == body:
        return False
    if dry_run:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True
