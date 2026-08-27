"""Lean `astroai agent` CLI surface.

Canonical verbs: list, install, remove, wipe, setup, config, update,
verify, plugins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from astroai_lab import ui
from astroai_lab.agent import clean_agent as agent_clean_mod
from astroai_lab.agent import fix as agent_fix_mod
from astroai_lab.agent import install as agent_install
from astroai_lab.agent import interact as agent_interact_mod
from astroai_lab.agent import plugins as agent_plugins
from astroai_lab.agent import setup as agent_setup_mod
from astroai_lab.cli.context import get_opts
from astroai_lab.core.paths import user_bin_dir
from astroai_lab.errors import LabError

agent_app = typer.Typer(
    help=(
        "AI coding agents: list/install/remove CLIs, configs, plugins.\n\n"
        "CLIs install to $SCRATCH ($ASTROAI_LAB_BIN_DIR); settings stay on $HOME.\n\n"
        "Quick map:\n"
        "  list          agents (Bin/Cfg/Where/Ver; --description, --ui)\n"
        "  install       CLI binary onto scratch\n"
        "  remove        managed CLI (--clean-home for $HOME copies)\n"
        "  setup         first-run scaffold (--project for a repo)\n"
        "  config        read/write that agent's settings file on $HOME\n"
        "  update        refresh CLI and bundled agent configs\n"
        "  verify        health check (--fix, --clean)\n"
        "  plugins       extras (Kind/On/Def/Agents; --description)"
    ),
)


@agent_app.callback(invoke_without_command=True)
def agent_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        opts = get_opts(ctx)
        if opts.json:
            ui.print_json(
                {
                    "help": "astroai agent --help",
                    "try": ["list", "install", "setup", "verify"],
                }
            )
            return
        ui.print_hint("AI agent CLIs go on $SCRATCH; settings stay on $HOME.")
        ui.print_hint("  astroai agent list")
        ui.print_hint("  astroai agent --help")


# ---------------------------------------------------------------------------
# Shell-completion callables
# ---------------------------------------------------------------------------


def _tool_completer(ctx, incomplete: str) -> list[str]:
    incomplete = incomplete or ""
    try:
        from astroai_lab.agent.install import TOOL_UTILITIES

        names = [
            str(row["name"])
            for row in agent_install.list_tools_status()
            if str(row["name"]) not in TOOL_UTILITIES
        ]
        from astroai_lab.agent.registry import registry_ids

        names += sorted(registry_ids())
    except Exception:  # noqa: BLE001 — completion must never crash the CLI
        return []
    return sorted({n for n in names if n.startswith(incomplete)})


def _bundle_completer(ctx, incomplete: str) -> list[str]:
    incomplete = incomplete or ""
    try:
        names = list(agent_setup_mod.agent_list_bundles())
        from astroai_lab.agent.registry import registry_ids

        names += sorted(registry_ids())
    except Exception:  # noqa: BLE001
        return []
    return [n for n in names if n.startswith(incomplete)]


def _agent_completer(ctx, incomplete: str) -> list[str]:
    incomplete = incomplete or ""
    try:
        from astroai_lab.agent.registry import registry_ids

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
# Shared printers
# ---------------------------------------------------------------------------


def _print_interact(opts) -> None:
    info = agent_interact_mod.inspect_interact_endpoints()
    if opts.json:
        ui.print_json(info)
        return
    ui.print_hint(f"Interactive Session Diagnostics ({info['session_kind'].upper()})")
    ui.print_hint(
        "  Active Agent CLIs: "
        + (", ".join(info["installed_agents"]) if info["installed_agents"] else "None")
    )
    ui.print_hint("")
    ui.print_hint("Endpoints & Access Points:")
    for ep in info["endpoints"]:
        mark = "✓ ONLINE" if ep["active"] else "- OFFLINE"
        ui.print_hint(f"  [{mark}] {ep['name']} ({ep['url_hint']})")
        ui.print_hint(f"          {ep['description']}")


def _print_status_table(
    report: dict,
    *,
    stamp: str | None = None,
    failed: str | None = None,
    show_description: bool = False,
) -> None:
    from astroai_lab.version import display_version

    ui.print_hint(f"  astroai {display_version()}")
    ui.print_hint("  Agent         Bin  Cfg  Where    Ver")
    ui.print_hint("  ────────────  ───  ───  ───────  ────────")
    for row in report["agents"]:
        binary_ok = bool(row.get("binary_ok", row.get("binary")))
        b = "✓" if binary_ok else "-"
        # Cfg: logged in or settings on home (declared file or upstream state dirs).
        config_installed = bool(row.get("config_present", False))
        if not config_installed and row.get("config_declared"):
            config_installed = bool(row.get("config_ok", row.get("config")))
        c = "✓" if config_installed else "-"
        ver = (row.get("version") or "-")[:12]
        name = row.get("id") or row.get("agent") or "?"
        name_disp = name
        src_raw = row.get("binary_source") or ("managed" if row.get("managed") else "-")
        if not binary_ok:
            src = "-"
        elif row.get("home_install") and not row.get("managed"):
            src = "home"
        elif src_raw == "managed":
            src = "scratch"
        elif src_raw == "other":
            src = "image"
        else:
            src = src_raw
        name_cell = f"[bold]{name_disp:<13}[/bold]" if binary_ok else f"{name_disp:<13}"
        b_cell = f"[bold]{b:<3}[/bold]" if binary_ok else f"{b:<3}"
        c_cell = f"[bold]{c:<3}[/bold]" if config_installed else f"{c:<3}"
        ui.print_markup(f"  {name_cell} {b_cell} {c_cell} {src:<7} {ver}")
        if show_description:
            summary = (row.get("summary") or "").strip()
            if summary:
                ui.print_hint(f"               {summary}")
    issues = report.get("issues") or []
    if issues:
        ui.print_hint("")
        for issue in issues:
            ui.print_warn(f"  {issue}")
    if stamp:
        ui.print_hint("")
        ui.print_hint(f"  Last setup: {stamp}")
    if failed:
        ui.print_warn(f"  Last failure: {failed}")
    ui.print_hint("")
    ui.print_hint("  Try:  agent install kilo && agent setup kilo && agent verify")
    ui.print_hint("  More:  agent list --description   ·   agent plugins list")
    ui.print_hint(
        "  Cfg: logged in or has settings on home   "
        "Where: scratch=$SCRATCH  home=$HOME  image=already in the image"
    )


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
    ui_endpoints: Annotated[
        bool,
        typer.Option("--ui", help="Show active container UI endpoints."),
    ] = False,
) -> None:
    """Installed, logged in, where it lives, and version."""
    if ui_endpoints:
        _print_interact(get_opts(ctx))
        return
    _emit_agent_list(ctx, show_description=description)


def _print_plugins(
    as_json: bool,
    *,
    kind: str | None = None,
    agent: str | None = None,
    show_description: bool = False,
) -> None:
    from astroai_lab.agent.agent_targets import mcp_hosts, skill_hosts
    from astroai_lab.version import display_version

    skill = list(skill_hosts())
    mcp = list(mcp_hosts())
    rows = agent_plugins.list_plugins(kind=kind, agent=agent)
    if as_json:
        ui.print_json(rows)
        return
    if not rows:
        if kind or agent:
            ui.print_hint("No plugins match the filter.")
            ui.print_hint("  astroai agent plugins list")
        else:
            ui.print_hint("Plugins: none in the registry (data/agent/plugins/*.yaml)")
        return
    id_w = max(len("Plugin"), max(len(str(row["id"])) for row in rows))
    kind_w = max(7, max(len(str(row["kind"])) for row in rows))
    gap = "  "
    ui.print_hint(f"  astroai {display_version()}")
    ui.print_hint(
        f"  {'Plugin':<{id_w}}{gap}{'Kind':<{kind_w}}{gap}{'On':<3}{gap}{'Def':<3}{gap}Agents"
    )
    ui.print_hint(f"  {'─' * id_w}{gap}{'─' * kind_w}{gap}{'─' * 3}{gap}{'─' * 3}{gap}{'─' * 14}")
    for row in rows:
        installed = bool(row["any_installed"])
        is_default = bool(row.get("default"))
        on = "✓" if installed else "-"
        default = "✓" if is_default else "-"
        agents = [str(a) for a in row["agents"]]
        if agents == skill:
            agents_cell = "skill-hosts"
        elif agents == mcp:
            agents_cell = "mcp-hosts"
        else:
            agents_cell = ",".join(agents)
        name = str(row["id"])
        kind_cell = str(row["kind"])
        name_cell = f"[bold]{name:<{id_w}}[/bold]" if installed else f"{name:<{id_w}}"
        on_cell = f"[bold]{on:<3}[/bold]" if installed else f"{on:<3}"
        def_cell = f"[bold]{default:<3}[/bold]" if is_default else f"{default:<3}"
        ui.print_markup(
            f"  {name_cell}{gap}{kind_cell:<{kind_w}}{gap}"
            f"{on_cell}{gap}{def_cell}{gap}{agents_cell}"
        )
        if show_description:
            summary = (row.get("summary") or "").strip()
            if summary:
                # Same hanging indent as `agent list --description` so long
                # summaries wrap instead of sitting under a 30-char id column.
                ui.print_hint(f"               {summary}")
    ui.print_hint("")
    ui.print_hint("  Try:  agent plugins install ponytail")
    ui.print_hint("  More:  agent plugins list --description   ·   agent list")
    ui.print_hint("  On: applied to an agent   Def: included in agent setup")
    ui.print_hint(f"  skill-hosts: {','.join(skill)}")
    ui.print_hint(f"  mcp-hosts:   {','.join(mcp)}")


def _print_plugin_results(results, *, verb: str, dry_run: bool) -> None:
    failures = [r for r in results if r.status == "failed"]
    for r in results:
        scope = r.agent or "all"
        prefix = "would" if dry_run else ""
        if r.status == "failed":
            ui.print_error(f"{r.plugin}: {r.detail}")
        elif r.status in ("would_install", "would_remove"):
            ui.print_ok(f"{prefix} {r.plugin} ({scope}): {r.status} — {r.detail}")
        elif r.status in ("installed", "removed"):
            ui.print_ok(f"{r.plugin} ({scope}): {r.status} — {r.detail}")
        elif r.status == "skipped":
            ui.print_hint(f"{r.plugin} ({scope}): skip — {r.detail}")
        elif r.status == "no-op":
            ui.print_hint(f"{r.plugin} ({scope}): {r.detail}")
        else:
            ui.print_hint(f"{r.plugin} ({scope}): {r.status} — {r.detail}")
    if failures:
        raise typer.Exit(1)
    ui.print_ok(f"{verb} complete")


def _want_version_probe(opts) -> bool:
    """Human list probes versions; JSON/automation skip unless overridden."""
    import os

    return (not opts.json) and os.environ.get("ASTROAI_LAB_PROBE_VERSION", "1") not in (
        "0",
        "false",
        "no",
    )


def _emit_agent_list(ctx: typer.Context, *, show_description: bool = False) -> None:
    from astroai_lab.agent.setup_state import build_agent_report, read_setup_state

    opts = get_opts(ctx)
    home = Path.home()
    report = build_agent_report(home, probe_ver=_want_version_probe(opts))
    state = read_setup_state(home)
    if opts.json:
        ui.print_json(report)
        if not report.get("ok"):
            raise typer.Exit(1)
        return
    _print_status_table(
        report,
        stamp=state.stamp,
        failed=state.failed,
        show_description=show_description,
    )


@agent_app.command("setup")
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
    """Write MCP, rules, and skills configs (or --project for per-repo scaffold)."""
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

    from astroai_lab.agent.registry import (
        list_installed_registry_agents,
        registry_ids,
        setup_registry_agent,
    )

    names = list(bundle) if bundle else []
    registry = registry_ids()
    if all_agents:
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

    if agent_ids or all_agents:
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
            ui.print_hint("  astroai agent verify        # confirm configs are healthy")
            ui.print_hint("  astroai agent config <id>   # show/edit an agent's config")
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
    ui.print_hint("  astroai agent install kilo|goose|cline|opencode")
    ui.print_hint("  astroai agent plugins install ponytail")
    if result.exit_code:
        raise typer.Exit(result.exit_code)


@agent_app.command("update")
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
    """Refresh agent MCP, rules, and bundled skills."""
    if agent:
        _run_registry_agent_update(ctx, agent, reinstall=reinstall)
        return
    _run_agent_sync(ctx)


def _run_registry_agent_update(ctx: typer.Context, agent: str, *, reinstall: bool) -> None:
    from astroai_lab.agent.registry import update_registry_agent

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
            from astroai_lab.agent.setup_state import record_setup_failed

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
    from astroai_lab.agent.registry import (
        fix_registry_agent,
        list_installed_registry_agents,
    )
    from astroai_lab.agent.setup_state import agent_setup_lock

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
    from astroai_lab.agent import agent_config as agent_config_mod

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


@agent_app.command("verify")
def agent_verify_cmd(
    ctx: typer.Context,
    agent: Annotated[
        str | None,
        typer.Argument(
            help="With --fix: repair this agent id only.",
            autocompletion=_agent_completer,
        ),
    ] = None,
    auto_fix: Annotated[
        bool,
        typer.Option(
            "--fix",
            "-f",
            help="Auto-repair shared setup and installed agent configs, then re-check.",
        ),
    ] = False,
    all_agents: Annotated[
        bool,
        typer.Option(
            "--all",
            help="With --fix: same as bare --fix (shared setup + every installed agent).",
        ),
    ] = False,
    clean: Annotated[
        bool,
        typer.Option("--clean", help="Clean stale locks/markers/empty configs (no health check)."),
    ] = False,
    stale_locks: Annotated[
        bool, typer.Option("--stale-locks", help="With --clean: remove stale lock files.")
    ] = True,
    failed: Annotated[
        bool, typer.Option("--failed", help="With --clean: clear failed setup marker.")
    ] = True,
    empty_configs: Annotated[
        bool, typer.Option("--empty-configs", help="With --clean: remove empty config files.")
    ] = True,
    logs: Annotated[
        bool, typer.Option("--logs", help="With --clean: remove setup log file.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show actions without executing.")
    ] = False,
) -> None:
    """Check agent setup (configs, syntax, launch). Use --fix to repair, --clean for stale state."""
    from astroai_lab.agent import inventory as agent_inventory
    from astroai_lab.agent.setup_state import read_setup_state
    from astroai_lab.cli.context import merge_opts

    opts = merge_opts(ctx, dry_run=dry_run)
    home = Path.home()

    if clean:
        if auto_fix or agent or all_agents:
            raise typer.BadParameter("--clean cannot be combined with --fix, <agent>, or --all")
        from astroai_lab.agent.setup_state import agent_setup_lock

        with agent_setup_lock(home):
            results = agent_clean_mod.clean_agent_state(
                stale_locks=stale_locks,
                failed_marker=failed,
                empty_configs=empty_configs,
                logs=logs,
                dry_run=opts.dry_run,
            )
        if opts.json:
            ui.print_json([r.__dict__ for r in results])
            return
        if not results:
            ui.print_ok("Agent state clean — no stale locks or broken markers found")
            return
        for r in results:
            prefix = "would remove" if opts.dry_run else "removed"
            ui.print_ok(f"  {r.target}: {prefix} ({r.detail})")
        return

    if agent:
        if not auto_fix:
            raise typer.BadParameter("<agent> / --all require --fix")
        _run_registry_repair(ctx, agent, all_agents=False)
        return

    if all_agents and not auto_fix:
        raise typer.BadParameter("<agent> / --all require --fix")

    # --fix and --fix --all share the same path (shared setup + every
    # installed agent). --fix <id> is handled above.
    if auto_fix or all_agents:
        repair = agent_fix_mod.repair_installed_agents(home=home, dry_run=opts.dry_run)
        if not opts.json:
            for action in repair.get("actions") or []:
                ui.print_ok(f"  {action}")
            for err in repair.get("errors") or []:
                ui.print_error(f"  {err}")
            for r in repair.get("setup") or []:
                if r.fixed:
                    prefix = "would fix" if opts.dry_run else "repaired"
                    ui.print_ok(f"  {r.target}: {prefix} — {r.detail}")

    issues = agent_inventory.verify_setup(home, probe_binaries=True)
    state = read_setup_state(home)
    from astroai_lab.agent.reconcile import drift_issues

    drift = drift_issues(home)
    payload = {
        "ok": not issues,
        "issues": issues,
        "drift": drift,
        "setup": state.to_dict(),
    }
    if opts.json:
        ui.print_json(payload)
        if issues:
            raise typer.Exit(1)
        return
    if drift:
        ui.print_warn("Installed state has drifted from this lab version:")
        for d in drift:
            ui.print_warn(f"  {d}")
        ui.print_hint("Tip: Run `astroai agent verify --fix` to reconcile.")
    if issues:
        ui.print_error("Agent setup incomplete:\n  " + "\n  ".join(issues))
        ui.print_hint("Tip: Run `astroai agent verify --fix`.")
        raise typer.Exit(1)
    if not drift and state.stamp:
        ui.print_hint(f"  last run: {state.stamp}")
    if not drift:
        ui.print_ok("Agent setup OK")


def _install_one_agent(tool: str, *, dry_run: bool) -> None:
    from astroai_lab.agent.registry import (
        get_registry_agent,
        install_registry_agent,
        setup_registry_agent,
    )

    agent = get_registry_agent(tool)
    if tool in agent_install.TOOLS:
        agent_install.install_tool(tool, dry_run=dry_run)
    elif agent is not None:
        install_registry_agent(tool, dry_run=dry_run)
    else:
        raise LabError(f"Unknown tool: {tool}", hint="astroai agent list")

    # Always seed config/skills after a real install when the tool is registered
    # (TOOLS-backed agents like claude/cursor used to skip this).
    if dry_run or agent is None:
        return
    setup = setup_registry_agent(tool, dry_run=False)
    if setup["errors"]:
        detail = "; ".join(setup["errors"])
        raise LabError(
            f"Installed {tool}, but setup failed: {detail}",
            hint=f"Retry: astroai agent setup {tool}",
        )


def _post_install_hint(tool: str) -> None:
    """Next-step hint after a successful install (config path when we know it)."""
    from astroai_lab.agent.agent_targets import expand_home
    from astroai_lab.agent.registry import get_registry_agent

    agent = get_registry_agent(tool)
    if agent is None:
        return
    cfg = (agent.get("config") or {}).get("path")
    if not cfg:
        ui.print_hint(f"  astroai agent setup {tool}   # skills / plugins")
        return
    path = expand_home(str(cfg), Path.home())
    if path.is_file():
        ui.print_hint(f"  config: {path}")
        if str((agent.get("config") or {}).get("format")) == "markdown":
            ui.print_hint(f"  astroai agent config {tool}   # show notes")
        else:
            ui.print_hint(f"  astroai agent config {tool}")
    else:
        ui.print_hint(f"  astroai agent setup {tool}   # create {path}")


@agent_app.command("install")
def agent_install_cmd(
    ctx: typer.Context,
    tools: Annotated[
        list[str] | None,
        typer.Argument(help="Agent name(s) (see `agent list`).", autocompletion=_tool_completer),
    ] = None,
) -> None:
    """Install AI coding CLI(s) to $ASTROAI_LAB_BIN_DIR (scratch/team, not $HOME).

    Examples:
      astroai agent install kilo
      astroai agent install agy omp pi freebuff
    """
    opts = get_opts(ctx)
    names = list(tools or [])
    if not names:
        if opts.json:
            ui.print_json(
                {
                    "help": "astroai agent install NAME [NAME…]",
                    "try": ["list"],
                }
            )
            return
        ui.print_hint("Install needs an agent name.")
        ui.print_hint("  astroai agent list")
        ui.print_hint("  astroai agent install NAME [NAME…]")
        return

    results: list[dict[str, Any]] = []
    for tool in names:
        try:
            if not opts.json and not opts.quiet and tool == "hermes" and not opts.dry_run:
                ui.print_hint(
                    "Hermes bootstraps uv, Python, Node, and clones the agent repo — "
                    "often 5–15 minutes on CANFAR. Installer output streams below."
                )
            _install_one_agent(tool, dry_run=opts.dry_run)
        except LabError as exc:
            results.append(
                {
                    "ok": False,
                    "tool": tool,
                    "actions": [],
                    "errors": [str(exc)],
                    "warnings": [],
                }
            )
            if not opts.json:
                ui.print_error(str(exc))
            continue
        payload = {
            "ok": True,
            "tool": tool,
            "actions": [f"install:{tool}"],
            "errors": [],
            "warnings": [],
            "bin_dir": str(user_bin_dir()) if not opts.dry_run else None,
            "dry_run": opts.dry_run,
        }
        results.append(payload)
        if not opts.json:
            if opts.dry_run:
                ui.print_ok(f"dry-run: would install {tool}")
            else:
                ui.print_ok(f"Installed {tool} → {user_bin_dir()}")
                _post_install_hint(tool)

    failed = [r for r in results if not r["ok"]]
    if failed and not opts.json and len(names) > 1:
        ok_count = len(results) - len(failed)
        failed_names = ", ".join(r["tool"] for r in failed)
        ui.print_error(
            f"{len(failed)}/{len(names)} install(s) failed ({ok_count} succeeded): {failed_names}"
        )
        ui.print_hint("  Fix each error above, then re-run failed names only.")
    if opts.json:
        if len(results) == 1:
            ui.print_json(results[0])
        else:
            ui.print_json(
                {
                    "ok": not failed,
                    "tools": names,
                    "results": results,
                    "errors": [e for r in failed for e in r["errors"]],
                    "dry_run": opts.dry_run,
                }
            )
    if failed:
        raise typer.Exit(1)


@agent_app.command("remove")
def agent_remove_cmd(
    ctx: typer.Context,
    tool: Annotated[
        str,
        typer.Argument(help="Tool/agent name.", autocompletion=_tool_completer),
    ],
    purge: Annotated[
        bool,
        typer.Option("--purge", help="Also remove the agent's home dir (~/.hermes, ~/.openclaw)."),
    ] = False,
    clean_home: Annotated[
        bool,
        typer.Option(
            "--clean-home",
            help="Also remove a user-owned CLI under $HOME (/arc/home), which lab does not manage.",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show actions without executing.")
    ] = False,
) -> None:
    """Uninstall a managed agent CLI (scratch). Use --clean-home for $HOME copies."""
    from astroai_lab.agent.registry import remove_registry_agent
    from astroai_lab.cli.context import merge_opts

    opts = merge_opts(ctx, dry_run=dry_run)
    try:
        if tool in agent_install.TOOLS:
            results = [
                r.__dict__
                for r in agent_install.uninstall_tool(
                    tool, purge=purge, clean_home=clean_home, dry_run=opts.dry_run
                )
            ]
        else:
            results = remove_registry_agent(
                tool, purge=purge, clean_home=clean_home, dry_run=opts.dry_run
            )
    except LabError as exc:
        if opts.json:
            ui.print_json(
                {
                    "ok": False,
                    "tool": tool,
                    "actions": [],
                    "errors": [str(exc)],
                }
            )
        else:
            ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    if opts.json:
        ui.print_json(
            {
                "ok": True,
                "tool": tool,
                "purge": purge,
                "clean_home": clean_home,
                "dry_run": opts.dry_run,
                "actions": results,
                "errors": [],
            }
        )
        return
    if not results:
        ui.print_ok(f"{tool}: nothing to remove")
        return
    prefix = "would remove" if opts.dry_run else "removed"
    for r in results:
        status = r["status"]
        if status == "error":
            ui.print_error(f"  {r['target']}: {r['detail']}")
        elif status == "would_remove":
            ui.print_hint(f"  {r['target']}: {prefix} ({r['detail']})")
        else:
            ui.print_ok(f"  {r['target']}: {prefix} ({r['detail']})")


@agent_app.command("wipe")
def agent_wipe_cmd(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show actions without executing.")
    ] = False,
) -> None:
    """Factory reset: remove EVERY agent binary, config, plugin, and setup state."""
    from astroai_lab.agent.wipe import wipe_agent_state
    from astroai_lab.cli.context import merge_opts

    opts = merge_opts(ctx, yes=yes, dry_run=dry_run)

    if opts.json and not opts.yes and not opts.dry_run:
        ui.print_json(
            {
                "ok": False,
                "dry_run": False,
                "actions": [],
                "errors": [
                    "agent wipe --json requires --yes (no interactive prompt in machine mode)"
                ],
                "counts": {"removed": 0, "would_remove": 0, "errors": 1},
            }
        )
        raise typer.Exit(1)

    if not opts.dry_run and not opts.yes and not opts.json:
        ui.print_warn("This PERMANENTLY removes every agent configuration:")
        ui.print_warn("  • every installed agent CLI (binary + config + plugins + home dirs)")
        ui.print_warn("  • ~/.astroai/lab setup state (stamps, locks, logs)")
        ui.print_warn("  • Cursor skills, rules, and MCP configs (~/.cursor)")
        ui.print_warn("Saved environments, projects, and CANFAR config are NOT touched.")
        if not typer.confirm("Proceed with the full wipe?", default=False):
            ui.print_hint("Wipe cancelled.")
            raise typer.Exit(0)

    results = wipe_agent_state(dry_run=opts.dry_run)
    errors = [r for r in results if r["status"] == "error"]
    removed = [r for r in results if r["status"] == "removed"]
    would = [r for r in results if r["status"] == "would_remove"]

    if opts.json:
        ui.print_json(
            {
                "ok": not errors,
                "dry_run": opts.dry_run,
                "actions": results,
                "errors": [r["detail"] for r in errors],
                "counts": {
                    "removed": len(removed),
                    "would_remove": len(would),
                    "errors": len(errors),
                },
            }
        )
        if errors:
            raise typer.Exit(1)
        return

    prefix = "would remove" if opts.dry_run else "removed"
    for r in results:
        if r["status"] == "error":
            ui.print_error(f"  {r['target']}: {r['detail']}")
        else:
            ui.print_ok(f"  {r['target']}: {prefix} ({r['detail']})")
    if errors:
        ui.print_error(f"Wipe finished with {len(errors)} error(s)")
        raise typer.Exit(1)
    if not results:
        ui.print_ok("Nothing to wipe — agent layer already clean")
        return
    if opts.dry_run:
        ui.print_ok(f"Would remove {len(would)} item(s) — run without --dry-run to apply")
        return
    ui.print_ok("Agent layer wiped — restart from scratch with: astroai agent setup")


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------

plugins_app = typer.Typer(
    help="Plugins: skills, MCP, rules, and tools applied onto agents.",
    invoke_without_command=True,
)
agent_app.add_typer(plugins_app, name="plugins")


@plugins_app.callback(invoke_without_command=True)
def plugins_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        opts = get_opts(ctx)
        if opts.json:
            ui.print_json(
                {
                    "help": "astroai agent plugins --help",
                    "try": ["list", "install", "remove"],
                }
            )
            return
        ui.print_hint("Plugins are extras (skills, MCP, rules, tools) applied onto agents.")
        ui.print_hint("  astroai agent plugins list")
        ui.print_hint("  astroai agent plugins --help")


@plugins_app.command("list")
def plugins_list_cmd(
    ctx: typer.Context,
    kind: Annotated[
        str | None,
        typer.Option(
            "--kind",
            "-k",
            help="Filter: skill, bundle, mcp, tool, rule, config, addon.",
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
      astroai agent plugins install ponytail
      astroai agent plugins install ponytail astroai-ray
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
    from astroai_lab.cli.context import merge_opts

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
