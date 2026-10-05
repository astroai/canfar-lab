"""`canfar lab agent plugins` commands."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from canfar_lab import ui
from canfar_lab.agent import plugins as agent_plugins
from canfar_lab.cli.agent_cmd import _plugin_completer, _plugin_kind_completer
from canfar_lab.cli.agent_format import _print_plugin_results, _print_plugins
from canfar_lab.cli.context import get_opts
from canfar_lab.errors import LabError

plugins_app = typer.Typer(
    help="Plugins: MCP, rules, and tools applied onto agents (skills via npx skills).",
    invoke_without_command=True,
)


@plugins_app.callback(invoke_without_command=True)
def plugins_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        opts = get_opts(ctx)
        if opts.json:
            ui.print_json(
                {
                    "help": "canfar lab agent plugins --help",
                    "try": ["list", "install", "remove"],
                }
            )
            return
        ui.print_hint("Plugins are MCP, Cursor rules, and CLI tools applied onto agents.")
        ui.print_hint("Skills install separately: npx skills add astroai/canfar-skills")
        ui.print_hint("  canfar lab agent plugins list")
        ui.print_hint("  canfar lab agent plugins --help")


@plugins_app.command("list")
def plugins_list_cmd(
    ctx: typer.Context,
    kind: Annotated[
        str | None,
        typer.Option(
            "--kind",
            "-k",
            help="Filter: mcp, tool, rule.",
            autocompletion=_plugin_kind_completer,
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Only plugins applied to this agent."),
    ] = None,
    description: Annotated[
        bool,
        typer.Option(
            "--description/--no-description",
            help="Show one-line summary under each plugin.",
        ),
    ] = False,
) -> None:
    """Every plugin: kind / applied / setup-default / agents."""
    _print_plugins(
        get_opts(ctx).json,
        kind=kind,
        agent=agent,
        show_description=description,
    )


@plugins_app.command("install")
def plugins_install_cmd(
    ctx: typer.Context,
    plugins: Annotated[
        list[str],
        typer.Argument(help="Plugin id(s).", autocompletion=_plugin_completer),
    ],
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Scope to one agent."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
) -> None:
    """Install plugin(s) on every installed agent that supports them.

    Examples:
      canfar lab agent plugins install ray-manager-mcp
      canfar lab agent plugins install skore-cli ponytail-rule
    """
    opts = get_opts(ctx)
    names = list(plugins)
    errors: list[str] = []
    payloads: list[dict[str, Any]] = []
    for plugin in names:
        try:
            results = agent_plugins.install_plugin(
                plugin,
                agent=agent,
                force=force or opts.yes,
                dry_run=opts.dry_run,
            )
        except LabError as exc:
            errors.append(str(exc))
            payloads.append({"ok": False, "plugin": plugin, "actions": [], "errors": [str(exc)]})
            if not opts.json:
                ui.print_error(str(exc))
            continue
        failed = [r.detail for r in results if r.status == "failed"]
        payloads.append(
            {
                "ok": not failed,
                "plugin": plugin,
                "actions": [r.__dict__ for r in results],
                "errors": failed,
                "dry_run": opts.dry_run,
            }
        )
        errors.extend(failed)
        if not opts.json:
            _print_plugin_results(results, verb="install", dry_run=opts.dry_run)

    if opts.json:
        if len(payloads) == 1:
            ui.print_json(payloads[0])
        else:
            ui.print_json(
                {
                    "ok": not errors,
                    "plugins": names,
                    "results": payloads,
                    "errors": errors,
                    "dry_run": opts.dry_run,
                }
            )
    if errors:
        raise typer.Exit(1)


@plugins_app.command("update")
def plugins_update_cmd(
    ctx: typer.Context,
    plugin: Annotated[str, typer.Argument(autocompletion=_plugin_completer)],
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Scope to one agent."),
    ] = None,
) -> None:
    """Refresh a plugin: re-apply to every installed agent that supports it."""
    opts = get_opts(ctx)
    try:
        results = agent_plugins.update_plugin(plugin, agent=agent, dry_run=opts.dry_run)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    if opts.json:
        ui.print_json(
            {
                "ok": not any(r.status == "failed" for r in results),
                "plugin": plugin,
                "actions": [r.__dict__ for r in results],
                "errors": [r.detail for r in results if r.status == "failed"],
                "dry_run": opts.dry_run,
            }
        )
        if any(r.status == "failed" for r in results):
            raise typer.Exit(1)
        return
    _print_plugin_results(results, verb="update", dry_run=opts.dry_run)


@plugins_app.command("remove")
def plugins_remove_cmd(
    ctx: typer.Context,
    plugin: Annotated[str, typer.Argument(autocompletion=_plugin_completer)],
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Scope to one agent."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show actions without executing.")
    ] = False,
) -> None:
    """Remove a plugin from every agent (or one ``--agent``)."""
    from canfar_lab.cli.context import merge_opts

    opts = merge_opts(ctx, dry_run=dry_run)
    try:
        results = agent_plugins.remove_plugin(plugin, agent=agent, dry_run=opts.dry_run)
    except LabError as exc:
        if opts.json:
            ui.print_json({"ok": False, "plugin": plugin, "actions": [], "errors": [str(exc)]})
        else:
            ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    if opts.json:
        ui.print_json(
            {
                "ok": not any(r.status == "failed" for r in results),
                "plugin": plugin,
                "actions": [r.__dict__ for r in results],
                "errors": [r.detail for r in results if r.status == "failed"],
                "dry_run": opts.dry_run,
            }
        )
        if any(r.status == "failed" for r in results):
            raise typer.Exit(1)
        return
    _print_plugin_results(results, verb="remove", dry_run=opts.dry_run)
