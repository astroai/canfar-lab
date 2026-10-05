"""Printers for `canfar lab agent` list and plugins."""

from __future__ import annotations

from pathlib import Path

import typer

from canfar_lab import ui
from canfar_lab.agent import interact as agent_interact_mod
from canfar_lab.agent import plugins as agent_plugins
from canfar_lab.cli.context import get_opts

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
    supported_only: bool = False,
    recommended: frozenset[str] | None = None,
) -> None:
    from canfar_lab.version import display_version

    recommended = recommended or frozenset()
    ui.print_hint(f"  canfar-lab {display_version()}")
    if supported_only:
        ui.print_hint("  Agent         Sup  Bin  Cfg  Where    Ver")
        ui.print_hint("  ────────────  ───  ───  ───  ───────  ────────")
    else:
        ui.print_hint("  Agent         Bin  Cfg  Where    Ver")
        ui.print_hint("  ────────────  ───  ───  ───────  ────────")
    for row in report["agents"]:
        name = row.get("id") or row.get("agent") or "?"
        is_supported = name in recommended
        if supported_only and not is_supported:
            continue
        binary_ok = bool(row.get("binary_ok", row.get("binary")))
        b = "✓" if binary_ok else "-"
        # Cfg: logged in or settings on home (declared file or upstream state dirs).
        config_installed = bool(row.get("config_present", False))
        if not config_installed and row.get("config_declared"):
            config_installed = bool(row.get("config_ok", row.get("config")))
        c = "✓" if config_installed else "-"
        ver = (row.get("version") or "-")[:12]
        name_disp = name
        src_raw = row.get("binary_source") or ("managed" if row.get("managed") else "-")
        if not binary_ok:
            src = "-"
        elif src_raw == "managed" or row.get("managed"):
            src = "scratch"
        elif src_raw == "home" or row.get("home_install"):
            src = "home"
        elif src_raw == "other":
            src = "image"
        else:
            src = src_raw
        name_cell = f"[bold]{name_disp:<13}[/bold]" if binary_ok else f"{name_disp:<13}"
        b_cell = f"[bold]{b:<3}[/bold]" if binary_ok else f"{b:<3}"
        c_cell = f"[bold]{c:<3}[/bold]" if config_installed else f"{c:<3}"
        if supported_only:
            s = "✓" if is_supported else "-"
            ui.print_markup(f"  {name_cell} {s:<3} {b_cell} {c_cell} {src:<7} {ver}")
        else:
            mark = " *" if is_supported else ""
            ui.print_markup(f"  {name_cell} {b_cell} {c_cell} {src:<7} {ver}{mark}")
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
    ui.print_hint("  Skills:  npx skills add astroai/canfar-skills")
    ui.print_hint("  More:  agent list --description   ·   agent plugins list")
    ui.print_hint("  Also:  agent list --supported   ·   agent setup --recommended")
    ui.print_hint(
        "  Cfg: logged in or has settings on home   "
        "Where: scratch=$SCRATCH  home=$HOME leftover  image=already in the image"
    )
    if recommended and not supported_only:
        ui.print_hint("  *: AstroAI-recommended (support.yaml)")


def _print_plugins(
    as_json: bool,
    *,
    kind: str | None = None,
    agent: str | None = None,
    show_description: bool = False,
) -> None:
    from canfar_lab.agent.agent_targets import mcp_hosts, skill_hosts
    from canfar_lab.version import display_version

    skill = list(skill_hosts())
    mcp = list(mcp_hosts())
    rows = agent_plugins.list_plugins(kind=kind, agent=agent)
    if as_json:
        ui.print_json(rows)
        return
    if not rows:
        if kind or agent:
            ui.print_hint("No plugins match the filter.")
            ui.print_hint("  canfar lab agent plugins list")
        else:
            ui.print_hint("Plugins: none in the registry (data/agent/plugins/*.yaml)")
        return
    id_w = max(len("Plugin"), max(len(str(row["id"])) for row in rows))
    kind_w = max(7, max(len(str(row["kind"])) for row in rows))
    gap = "  "
    ui.print_hint(f"  canfar-lab {display_version()}")
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
    ui.print_hint("  Try:  agent plugins install ray-manager-mcp")
    ui.print_hint("  Skills:  npx skills add astroai/canfar-skills")
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

    return (not opts.json) and os.environ.get("CANFAR_LAB_PROBE_VERSION", "1") not in (
        "0",
        "false",
        "no",
    )


def _emit_agent_list(
    ctx: typer.Context,
    *,
    show_description: bool = False,
    supported_only: bool = False,
) -> None:
    from canfar_lab.agent.setup_state import build_agent_report, read_setup_state
    from canfar_lab.agent.support import load_support

    opts = get_opts(ctx)
    home = Path.home()
    report = build_agent_report(home, probe_ver=_want_version_probe(opts))
    state = read_setup_state(home)
    recommended = frozenset(load_support().recommended_agents)
    if opts.json:
        if supported_only:
            report = {
                **report,
                "agents": [a for a in report["agents"] if a.get("id") in recommended],
                "supported": sorted(recommended),
            }
        else:
            report = {**report, "supported": sorted(recommended)}
        ui.print_json(report)
        if not report.get("ok"):
            raise typer.Exit(1)
        return
    _print_status_table(
        report,
        stamp=state.stamp,
        failed=state.failed,
        show_description=show_description,
        supported_only=supported_only,
        recommended=recommended,
    )
