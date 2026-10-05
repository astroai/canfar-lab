"""Lean `canfar lab agent` CLI surface.

Canonical verbs: list, install, remove, wipe, setup, config, update,
verify, plugins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from canfar_lab import ui
from canfar_lab.agent import install as agent_install
from canfar_lab.agent import plugins as agent_plugins
from canfar_lab.agent import setup as agent_setup_mod
from canfar_lab.cli.context import get_opts
from canfar_lab.errors import LabError

agent_app = typer.Typer(
    help=(
        "AI coding agents: list/install/remove CLIs, configs, plugins.\n\n"
        "CLIs install to $SCRATCH/.local/bin; settings stay on $HOME.\n"
        "Skills: npx skills add …  (not managed by AstroAI).\n\n"
        "Quick map:\n"
        "  list          agents (Bin/Cfg/Where/Ver; --description, --supported, --ui)\n"
        "  install       CLI binary onto $SCRATCH/.local/bin\n"
        "  remove        managed CLI (use --clean-home for leftover /arc home copies)\n"
        "  setup         first-run scaffold (--recommended, --project for a repo)\n"
        "  routers       AstroAI-supported LLM routers (shared with panel)\n"
        "  config        read/write that agent's settings file on $HOME\n"
        "  update        refresh CLI and bundled agent configs\n"
        "  verify        health check (--fix, --clean)\n"
        "  env           shared credential state (--with-dsh)\n"
        "  keys          model API keys: list / set NAME (stdin) / unset NAME\n"
        "  plugins       MCP/rules/tools (Kind/On/Def/Agents; --description)"
    ),
)


@agent_app.callback(invoke_without_command=True)
def agent_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        opts = get_opts(ctx)
        if opts.json:
            ui.print_json(
                {
                    "help": "canfar lab agent --help",
                    "try": ["list", "install", "setup", "verify"],
                }
            )
            return
        ui.print_hint("AI agent CLIs go on $SCRATCH/.local/bin; skills via npx skills.")
        ui.print_hint("  canfar lab agent list")
        ui.print_hint("  npx skills add astroai/canfar-skills")
        ui.print_hint("  canfar lab agent --help")


# ---------------------------------------------------------------------------
# Shell-completion callables
# ---------------------------------------------------------------------------


def _tool_completer(ctx, incomplete: str) -> list[str]:
    incomplete = incomplete or ""
    try:
        from canfar_lab.agent.install import TOOL_UTILITIES

        names = [
            str(row["name"])
            for row in agent_install.list_tools_status()
            if str(row["name"]) not in TOOL_UTILITIES
        ]
        from canfar_lab.agent.registry import registry_ids

        names += sorted(registry_ids())
    except Exception:  # noqa: BLE001 — completion must never crash the CLI
        return []
    return sorted({n for n in names if n.startswith(incomplete)})


def _bundle_completer(ctx, incomplete: str) -> list[str]:
    incomplete = incomplete or ""
    try:
        names = list(agent_setup_mod.agent_list_bundles())
        from canfar_lab.agent.registry import registry_ids

        names += sorted(registry_ids())
    except Exception:  # noqa: BLE001
        return []
    return [n for n in names if n.startswith(incomplete)]


def _agent_completer(ctx, incomplete: str) -> list[str]:
    incomplete = incomplete or ""
    try:
        from canfar_lab.agent.registry import registry_ids

        names = sorted(registry_ids())
    except Exception:  # noqa: BLE001
        return []
    return [n for n in names if n.startswith(incomplete)]


def _plugin_completer(ctx, incomplete: str) -> list[str]:
    incomplete = incomplete or ""
    try:
        ids = sorted(agent_plugins.plugin_ids())
    except Exception:  # noqa: BLE001
        return []
    return [i for i in ids if i.startswith(incomplete)]


def _plugin_kind_completer(ctx, incomplete: str) -> list[str]:
    return [k for k in agent_plugins.PLUGIN_KINDS if k.startswith(incomplete or "")]


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@agent_app.command("list")
def agent_list_cmd(
    ctx: typer.Context,
    description: Annotated[
        bool,
        typer.Option(
            "--description/--no-description",
            help="Show one-line summary under each agent.",
        ),
    ] = False,
    supported: Annotated[
        bool,
        typer.Option(
            "--supported",
            help="Only show agents listed in support.yaml recommended set.",
        ),
    ] = False,
    ui_endpoints: Annotated[
        bool,
        typer.Option("--ui", help="Show active container UI endpoints."),
    ] = False,
) -> None:
    """Installed, logged in, where it lives, and version."""
    if ui_endpoints:
        _print_interact(get_opts(ctx))
        return
    _emit_agent_list(ctx, show_description=description, supported_only=supported)


@agent_app.command("routers")
def agent_routers_cmd(ctx: typer.Context) -> None:
    """AstroAI-supported LLM routers (same key catalog as ``panel routers``)."""
    from canfar_lab.agent import review_bench as _rb
    from canfar_lab.agent.support import routers_status

    opts = get_opts(ctx)
    keys = _rb.discover_dsh_keys()
    health = _rb.resolve_panel_route(keys=keys)
    rows = routers_status(keys_present=keys)
    if opts.json:
        ui.print_json({"routers": rows, "route": health})
        return
    ui.print_hint("AstroAI-supported routers (key catalog — models in dsh Settings)")
    ui.print_hint("  Id                  Key                   Present  dsh route")
    ui.print_hint("  ──────────────────  ────────────────────  ───────  ────────────")
    for row in rows:
        present = "✓" if row["key_present"] else "-"
        ui.print_hint(f"  {row['id']:<18}  {row['key']:<20}  {present:<7}  {row['dsh_route']}")
        if row.get("notes"):
            ui.print_hint(f"        {row['notes']}")
    ui.print_hint("  Same catalog: canfar lab panel routers | doctor")


@agent_app.command("layout")
def agent_layout_cmd(
    ctx: typer.Context,
    boot: Annotated[
        bool,
        typer.Option(
            "--boot",
            help="Session-boot fast path: restore durable ~/.dsh only (no heavy relocates).",
        ),
    ] = False,
) -> None:
    """Re-link agent runtime trees onto $SCRATCH; restore durable ~/.dsh dirs.

    Safe to run every session boot. Scratch dies with the session, so stamped
    ``agent setup`` must not be the only path that repairs dangling links —
    otherwise Studio Connect 401s after the first successful setup.

    Use ``--boot`` from session entrypoints so Studio can bind :5000 before
    multi-hundred-MB force-relocates finish (those run as a full layout in bg).
    """
    opts = get_opts(ctx)
    from canfar_lab.core.home_layout import ensure_agent_runtime_on_scratch

    actions = ensure_agent_runtime_on_scratch(Path.home(), dry_run=opts.dry_run, boot=boot)
    if opts.json:
        ui.print_json({"ok": True, "actions": actions, "dry_run": opts.dry_run, "boot": boot})
        return
    if not actions:
        ui.print_ok(
            "Agent runtime layout already on scratch"
            if not boot
            else "Boot layout ok (durable ~/.dsh)"
        )
        return
    for a in actions:
        ui.print_hint(f"runtime: {a}")
    ui.print_ok(f"Agent runtime layout updated ({len(actions)} action(s))")


@agent_app.command("config")
def agent_config_cmd(
    ctx: typer.Context,
    agent: Annotated[
        str,
        typer.Argument(help="Registered agent id.", autocompletion=_agent_completer),
    ],
    pairs: Annotated[
        list[str] | None,
        typer.Argument(help="key=value pairs to write (dotted keys allowed)."),
    ] = None,
    key: Annotated[
        str | None,
        typer.Option("--key", "-k", help="Show one dotted key value instead of the whole file."),
    ] = None,
    unset: Annotated[
        list[str] | None,
        typer.Option("--unset", "-u", help="Remove a dotted key (repeatable)."),
    ] = None,
) -> None:
    """Show or edit a registered agent's config file."""
    from canfar_lab.agent import agent_config as agent_config_mod

    opts = get_opts(ctx)
    set_items: dict[str, Any] = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise typer.BadParameter(f"expected key=value, got {raw!r}")
        k, _, v = raw.partition("=")
        set_items[k.strip()] = agent_config_mod.parse_value(v)
    unsets = list(unset or [])

    try:
        if set_items or unsets:
            actions = agent_config_mod.edit_agent_config(
                agent, set_items=set_items, unsets=unsets, dry_run=opts.dry_run
            )
        elif key:
            value, found = agent_config_mod.get_config_value(agent, key)
            if not found:
                raise LabError(f"{agent} has no key {key!r}")
            if opts.json:
                ui.print_json({"agent": agent, "key": key, "value": value})
            else:
                ui.print_ok(f"{key} = {agent_config_mod.fmt_value(value)}")
            return
        else:
            path, data = agent_config_mod.read_agent_config(agent)
            if opts.json:
                ui.print_json(
                    {
                        "agent": agent,
                        "path": str(path),
                        "format": agent_config_mod.config_format(agent),
                        "data": data,
                    }
                )
            else:
                ui.print_hint(f"{agent} config — {path}")
                typer.echo(path.read_text(encoding="utf-8").rstrip() or "(empty)")
            return
    except LabError as exc:
        if opts.json:
            ui.print_json({"ok": False, "agent": agent, "errors": [str(exc)]})
        else:
            ui.print_error(str(exc))
        raise typer.Exit(1) from exc

    if opts.json:
        ui.print_json(
            {
                "agent": agent,
                "actions": actions,
                "dry_run": opts.dry_run,
                "ok": not any(a["status"] in ("error",) for a in actions),
            }
        )
        return
    prefix = "would" if opts.dry_run else ""
    for a in actions:
        if a["status"] == "set":
            ui.print_ok(f"set {a['key']} = {a['detail']}")
        elif a["status"] == "unset":
            ui.print_ok(f"unset {a['key']}")
        elif a["status"] == "would_set":
            ui.print_ok(f"{prefix} set {a['key']} = {a['detail']}")
        elif a["status"] == "would_unset":
            ui.print_ok(f"{prefix} unset {a['key']}")
        else:
            ui.print_hint(f"{a['key']}: {a['detail']}")
    ui.print_ok("Config updated")


@agent_app.command("env")
def agent_env_cmd(
    ctx: typer.Context,
    with_dsh: Annotated[
        bool,
        typer.Option("--with-dsh", help="Include dsh provider routes (never prints secrets)."),
    ] = False,
) -> None:
    """Show shared agent credential state (presence only, never values)."""
    from canfar_lab.agent import review_bench as _rb
    from canfar_lab.agent.setup import discover_openrouter_key, openrouter_dotenv_path

    opts = get_opts(ctx)
    home = Path.home()
    dotenv = openrouter_dotenv_path(home)
    payload: dict[str, object] = {
        "dotenv": str(dotenv),
        "dotenv_mode": oct(dotenv.stat().st_mode & 0o777) if dotenv.is_file() else None,
        "openrouter_key": bool(discover_openrouter_key(home)),
    }
    if with_dsh:
        keys = _rb.discover_dsh_keys(home)
        payload["dsh_keys"] = sorted(keys)
        payload["dsh_providers_ensured"] = _rb.ensure_dsh_settings(home, dry_run=True)
    if opts.json:
        ui.print_json(payload)
        return
    ui.print_hint(f"shared dotenv — {dotenv} ({payload['dotenv_mode']})")
    ui.print_ok(f"OPENROUTER_API_KEY present: {payload['openrouter_key']}")
    if with_dsh:
        names = payload["dsh_keys"]
        assert isinstance(names, list)
        ui.print_ok(f"dsh keys present: {', '.join(names) if names else '(none)'}")
        ui.print_ok(f"dsh providers ensured: {payload['dsh_providers_ensured']}")
        ui.print_hint("Persist with: canfar lab agent setup (writes .env 0600 + provider refs)")


from canfar_lab.cli.agent_format import (  # noqa: E402
    _emit_agent_list,
    _print_interact,
)
from canfar_lab.cli.agent_install import (  # noqa: E402
    _install_one_agent,  # noqa: F401  re-export; setup calls this via agent_cmd
    agent_install_cmd,
    agent_remove_cmd,
    agent_verify_cmd,
    agent_wipe_cmd,
)
from canfar_lab.cli.agent_keys import keys_app  # noqa: E402
from canfar_lab.cli.agent_plugins import plugins_app  # noqa: E402
from canfar_lab.cli.agent_setup import agent_setup_cmd, agent_update_cmd  # noqa: E402

agent_app.command("setup")(agent_setup_cmd)
agent_app.command("update")(agent_update_cmd)
agent_app.command("verify")(agent_verify_cmd)
agent_app.command("install")(agent_install_cmd)
agent_app.command("remove")(agent_remove_cmd)
agent_app.command("wipe")(agent_wipe_cmd)
agent_app.add_typer(keys_app, name="keys")
agent_app.add_typer(plugins_app, name="plugins")
