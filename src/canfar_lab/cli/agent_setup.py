"""Setup, update, and registry repair for `canfar lab agent`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from canfar_lab import ui
from canfar_lab.agent import setup as agent_setup_mod
from canfar_lab.cli import agent_cmd as agent_cmd_mod
from canfar_lab.cli.agent_cmd import _agent_completer, _bundle_completer
from canfar_lab.cli.context import get_opts
from canfar_lab.errors import LabError


def agent_setup_cmd(
    ctx: typer.Context,
    bundle: Annotated[
        list[str] | None,
        typer.Argument(
            help="Agent id(s) or a setup name (marimo, cursor, …). "
            "With --project, first arg is the target directory.",
            autocompletion=_bundle_completer,
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
    all_agents: Annotated[
        bool,
        typer.Option("--all", help="Registry-driven setup for every installed agent."),
    ] = False,
    recommended: Annotated[
        bool,
        typer.Option(
            "--recommended",
            help="Install+setup the AstroAI recommended agent set (support.yaml).",
        ),
    ] = False,
    post_install: Annotated[
        bool,
        typer.Option(
            "--post-install",
            help="Run the agent's interactive setup.post_install (e.g. openclaw onboard).",
        ),
    ] = False,
    project: Annotated[
        bool,
        typer.Option(
            "--project",
            help="Scaffold AGENTS.md + .cursor/ in a repo "
            "(DIR = first arg or --path; not the `project` config bundle).",
        ),
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option("--path", help="Project directory for --project (default: cwd)."),
    ] = None,
) -> None:
    """Write MCP and rules configs (or --project for per-repo scaffold)."""
    opts = get_opts(ctx)

    if project:
        names = list(bundle) if bundle else []
        if path is not None:
            project_dir = path.expanduser().resolve()
            if names:
                ui.print_warn(f"--path set; ignoring positional args: {', '.join(names)}")
        elif names:
            project_dir = Path(names[0]).expanduser().resolve()
            if len(names) > 1:
                ui.print_warn(f"--project ignores extra args: {', '.join(names[1:])}")
        else:
            project_dir = Path.cwd().resolve()
        try:
            result = agent_setup_mod.agent_setup(
                mode="project",
                project_dir=project_dir,
                force=force or opts.yes,
                dry_run=opts.dry_run,
            )
        except LabError as exc:
            if opts.json:
                ui.print_json(
                    {
                        "ok": False,
                        "partial": False,
                        "mode": "project",
                        "actions": [],
                        "errors": [str(exc)],
                        "warnings": [],
                        "stamp": None,
                    }
                )
            else:
                ui.print_error(str(exc))
            raise typer.Exit(1) from exc
        if opts.json:
            ui.print_json(result.to_dict())
            if result.exit_code:
                raise typer.Exit(result.exit_code)
            return
        if result.ok:
            ui.print_ok(f"Project templates installed in {project_dir}")
        else:
            for err in result.errors:
                ui.print_error(err)
            raise typer.Exit(result.exit_code)
        return

    from canfar_lab.agent.registry import (
        list_installed_registry_agents,
        registry_ids,
        setup_registry_agent,
    )
    from canfar_lab.agent.support import load_support

    names = list(bundle) if bundle else []
    registry = registry_ids()
    if recommended:
        catalog = load_support()
        names = list(dict.fromkeys([*catalog.recommended_agents, *catalog.panel_agents]))
        # Skip marimo unless OpenRouter is available (marimo AI needs it).
        if "marimo" in names and not agent_setup_mod.discover_openrouter_key():
            names = [n for n in names if n != "marimo"]
            ui.print_hint("Skipping marimo (no OPENROUTER_API_KEY).")
        if not opts.dry_run:
            for tool in names:
                try:
                    agent_cmd_mod._install_one_agent(tool, dry_run=False)
                except LabError as exc:
                    ui.print_warn(f"install {tool}: {exc}")
        agent_ids = [n for n in names if n in registry]
        bundle_names = [n for n in names if n not in registry]
        if names and not opts.json:
            ui.print_hint(f"Recommended set: {', '.join(names)}")
    elif all_agents:
        agent_ids = [a["id"] for a in list_installed_registry_agents()]
        bundle_names: list[str] = []
        if names:
            ui.print_warn(f"--all ignores bundle names: {', '.join(names)}")
    else:
        agent_ids = [n for n in names if n in registry]
        bundle_names = [n for n in names if n not in registry]

    agent_actions: list[str] = []
    agent_errors: list[str] = []
    for agent_id in agent_ids:
        try:
            res = setup_registry_agent(
                agent_id,
                force=force or opts.yes,
                dry_run=opts.dry_run,
                post_install=post_install,
            )
        except LabError as exc:
            agent_errors.append(f"{agent_id}: {exc}")
            continue
        agent_actions.extend(res["actions"])
        agent_errors.extend(res["errors"])

    if agent_ids or all_agents or recommended:
        bundle_result = None
        if bundle_names:
            try:
                bundle_result = agent_setup_mod.agent_setup(
                    mode="install",
                    bundles=bundle_names,
                    force=force or opts.yes,
                    dry_run=opts.dry_run,
                )
            except LabError as exc:
                agent_errors.append(f"bundles: {exc}")
        if bundle_result is not None:
            payload = bundle_result.to_dict()
            payload["actions"] = agent_actions + payload["actions"]
            payload["errors"] = agent_errors + payload["errors"]
        else:
            payload = {
                "ok": not agent_errors,
                "partial": bool(agent_actions) and bool(agent_errors),
                "mode": "install",
                "actions": agent_actions,
                "errors": agent_errors,
                "warnings": [],
                "stamp": None,
            }
        ok = payload["ok"] and not agent_errors
        partial = payload["partial"] or (bool(agent_actions) and bool(agent_errors))
        payload["ok"] = ok
        payload["partial"] = partial
        exit_code = 0 if ok and not partial else (2 if (partial or payload["actions"]) else 1)
        if opts.json:
            ui.print_json(payload)
            if exit_code:
                raise typer.Exit(exit_code)
            return
        for err in payload["errors"]:
            ui.print_error(err)
        if ok and not partial:
            ui.print_ok("Agent setup complete")
        elif partial:
            ui.print_warn(
                f"Partial setup — {len(payload['actions'])} ok, {len(payload['errors'])} failed"
            )
        else:
            ui.print_error("Agent setup failed")
        if all_agents and not agent_ids:
            ui.print_hint("  No installed registry agents — install one: agent install <id>")
        if agent_ids:
            ui.print_hint("  canfar lab agent verify        # confirm configs are healthy")
            ui.print_hint("  canfar lab agent config <id>   # show/edit an agent's config")
        if exit_code:
            raise typer.Exit(exit_code)
        return

    try:
        result = agent_setup_mod.agent_setup(
            mode="install",
            bundles=list(bundle) if bundle else None,
            force=force or opts.yes,
            dry_run=opts.dry_run,
        )
    except LabError as exc:
        if opts.json:
            ui.print_json(
                {
                    "ok": False,
                    "partial": False,
                    "mode": "install",
                    "actions": [],
                    "errors": [str(exc)],
                    "warnings": [],
                    "stamp": None,
                }
            )
        else:
            ui.print_error(str(exc))
        raise typer.Exit(1) from exc

    if opts.json:
        ui.print_json(result.to_dict())
        if result.exit_code:
            raise typer.Exit(result.exit_code)
        return

    for w in result.warnings:
        ui.print_warn(w)
    for err in result.errors:
        ui.print_error(err)
    if result.ok and not result.partial:
        ui.print_ok("Agent setup complete")
    elif result.partial:
        ui.print_warn(f"Partial setup — {len(result.actions)} ok, {len(result.errors)} failed")
    else:
        ui.print_error("Agent setup failed")
    ui.print_hint("  canfar lab agent install kilo|goose|cline|opencode")
    ui.print_hint("  npx skills add astroai/canfar-skills")
    ui.print_hint("  canfar lab agent plugins install ray-manager-mcp")
    if result.exit_code:
        raise typer.Exit(result.exit_code)


def agent_update_cmd(
    ctx: typer.Context,
    agent: Annotated[
        str | None,
        typer.Argument(
            help="Registered agent id (registry-driven update).",
            autocompletion=_agent_completer,
        ),
    ] = None,
    reinstall: Annotated[
        bool,
        typer.Option("--reinstall", help="Force CLI reinstall even when the binary is up to date."),
    ] = False,
) -> None:
    """Refresh agent MCP, rules, and plugin defaults."""
    if agent:
        _run_registry_agent_update(ctx, agent, reinstall=reinstall)
        return
    _run_agent_sync(ctx)


def _run_registry_agent_update(ctx: typer.Context, agent: str, *, reinstall: bool) -> None:
    from canfar_lab.agent.registry import update_registry_agent

    opts = get_opts(ctx)
    try:
        result = update_registry_agent(agent, force_reinstall=reinstall, dry_run=opts.dry_run)
    except LabError as exc:
        if opts.json:
            ui.print_json({"ok": False, "agent": agent, "actions": [], "errors": [str(exc)]})
        else:
            ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    if opts.json:
        ui.print_json(result)
        if not result["ok"]:
            raise typer.Exit(2 if result["partial"] else 1)
        return
    prefix = "would" if opts.dry_run else ""
    for action in result["actions"]:
        ui.print_ok(f"{prefix} {action}")
    for err in result["errors"]:
        ui.print_error(err)
    if not result["ok"]:
        raise typer.Exit(2 if result["partial"] else 1)
    ui.print_ok(f"Agent {agent} updated")


def _run_agent_sync(ctx: typer.Context) -> None:
    opts = get_opts(ctx)
    try:
        agent_setup_mod.agent_sync(dry_run=opts.dry_run)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    verify_failed = False
    if not opts.dry_run:
        try:
            agent_setup_mod.agent_verify()
        except LabError as exc:
            verify_failed = True
            ui.print_warn(str(exc))
            from canfar_lab.agent.setup_state import record_setup_failed

            record_setup_failed(exit_code=2, detail=str(exc)[:500])
    if opts.dry_run:
        ui.print_ok("dry-run: would refresh agent configs")
        return
    if verify_failed:
        ui.print_warn("Agent config update finished with issues")
        raise typer.Exit(2)
    ui.print_ok("Agent config updated")


def _run_registry_repair(ctx: typer.Context, agent_id: str | None, *, all_agents: bool) -> None:
    """Repair one agent via ``verify --fix <id>``.

    ``--fix --all`` shares the bare ``--fix`` path (shared setup + re-check);
    this helper is only for a scoped agent id.
    """
    from canfar_lab.agent.registry import (
        fix_registry_agent,
        list_installed_registry_agents,
    )
    from canfar_lab.agent.setup_state import agent_setup_lock

    opts = get_opts(ctx)
    home = Path.home()
    ids = [agent_id] if agent_id else [a["id"] for a in list_installed_registry_agents()]
    if not ids:
        if opts.json:
            ui.print_json(
                {
                    "ok": True,
                    "partial": False,
                    "agents": [],
                    "fixed": [],
                    "actions": [],
                    "errors": [],
                }
            )
        else:
            ui.print_hint("No installed registry agents — install one: agent install <id>")
        return

    actions: list[str] = []
    errors: list[str] = []
    fixed: list[str] = []
    with agent_setup_lock(home):
        for aid in ids:
            try:
                result = fix_registry_agent(aid, dry_run=opts.dry_run)
            except LabError as exc:
                errors.append(f"{aid}: {exc}")
                continue
            actions.extend(result["actions"])
            errors.extend(result["errors"])
            if result["ok"]:
                fixed.append(aid)

    payload = {
        "ok": not errors,
        "partial": bool(actions) and bool(errors),
        "agents": ids,
        "fixed": fixed,
        "actions": actions,
        "errors": errors,
    }
    if agent_id:
        payload["agent"] = agent_id
    if opts.json:
        ui.print_json(payload)
        if errors:
            raise typer.Exit(2 if payload["partial"] else 1)
        return
    for action in actions:
        ui.print_ok(f"  {action}")
    for err in errors:
        ui.print_error(f"  {err}")
    if errors:
        raise typer.Exit(2 if payload["partial"] else 1)
    if agent_id:
        ui.print_ok(f"Agent {agent_id} config OK")
    else:
        ui.print_ok(f"Agent configs OK ({len(fixed)} agent(s))")
