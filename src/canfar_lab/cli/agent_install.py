"""Install, remove, wipe, and verify for `canfar lab agent`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from canfar_lab import ui
from canfar_lab.agent import clean_agent as agent_clean_mod
from canfar_lab.agent import fix as agent_fix_mod
from canfar_lab.agent import install as agent_install
from canfar_lab.cli.agent_cmd import _agent_completer, _tool_completer
from canfar_lab.cli.context import get_opts
from canfar_lab.core.paths import user_bin_dir
from canfar_lab.errors import LabError


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
    from canfar_lab.agent import inventory as agent_inventory
    from canfar_lab.agent.setup_state import read_setup_state
    from canfar_lab.cli.context import merge_opts

    opts = merge_opts(ctx, dry_run=dry_run)
    home = Path.home()

    if clean:
        if auto_fix or agent or all_agents:
            raise typer.BadParameter("--clean cannot be combined with --fix, <agent>, or --all")
        from canfar_lab.agent.setup_state import agent_setup_lock

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
        from canfar_lab.cli.agent_setup import _run_registry_repair as _repair

        _repair(ctx, agent, all_agents=False)
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
    from canfar_lab.agent.reconcile import drift_issues

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
        ui.print_hint("Tip: Run `canfar lab agent verify --fix` to reconcile.")
    if issues:
        ui.print_error("Agent setup incomplete:\n  " + "\n  ".join(issues))
        ui.print_hint("Tip: Run `canfar lab agent verify --fix`.")
        raise typer.Exit(1)
    if not drift and state.stamp:
        ui.print_hint(f"  last run: {state.stamp}")
    if not drift:
        ui.print_ok("Agent setup OK")


def _install_one_agent(tool: str, *, dry_run: bool, setup: bool = True) -> None:
    from canfar_lab.agent.registry import (
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
        raise LabError(f"Unknown tool: {tool}", hint="canfar lab agent list")

    # Always seed config after a real install when the tool is registered
    # (TOOLS-backed agents like claude/cursor used to skip this).
    if dry_run or agent is None or not setup:
        return
    result = setup_registry_agent(tool, dry_run=False)
    if result["errors"]:
        detail = "; ".join(result["errors"])
        raise LabError(
            f"Installed {tool}, but setup failed: {detail}",
            hint=f"Retry: canfar lab agent setup {tool}",
        )


def _agents_to_restore() -> list[str]:
    """Remembered agents whose CLI is not on this session's $SCRATCH."""
    from canfar_lab.agent.install import BINARY_SOURCE_LEGACY, BINARY_SOURCE_MISSING
    from canfar_lab.agent.registry import get_registry_agent
    from canfar_lab.agent.setup_state import remembered_agents

    missing = []
    for tool in remembered_agents():
        if get_registry_agent(tool) is None and tool not in agent_install.TOOLS:
            continue
        source = agent_install.classify_binary(agent_install.tool_binary(tool))["source"]
        if source in (BINARY_SOURCE_MISSING, BINARY_SOURCE_LEGACY):
            missing.append(tool)
    return missing


def _post_install_hint(tool: str) -> None:
    """Next-step hint after a successful install (config path when we know it)."""
    from canfar_lab.agent.agent_targets import expand_home
    from canfar_lab.agent.registry import get_registry_agent

    agent = get_registry_agent(tool)
    if agent is None:
        return
    cfg = (agent.get("config") or {}).get("path")
    if not cfg:
        ui.print_hint(f"  canfar lab agent setup {tool}   # MCP / plugins")
        return
    path = expand_home(str(cfg), Path.home())
    if path.is_file():
        ui.print_hint(f"  config: {path}")
        if str((agent.get("config") or {}).get("format")) == "markdown":
            ui.print_hint(f"  canfar lab agent config {tool}   # show notes")
        else:
            ui.print_hint(f"  canfar lab agent config {tool}")
    else:
        ui.print_hint(f"  canfar lab agent setup {tool}   # create {path}")


def agent_install_cmd(
    ctx: typer.Context,
    tools: Annotated[
        list[str] | None,
        typer.Argument(help="Agent name(s) (see `agent list`).", autocompletion=_tool_completer),
    ] = None,
    restore: Annotated[
        bool,
        typer.Option(
            "--restore",
            help="Reinstall agents installed in earlier sessions whose CLI is gone "
            "(a new session starts with an empty $SCRATCH; configs stay on home).",
        ),
    ] = False,
) -> None:
    """Install AI coding CLI(s) to $SCRATCH/.local/bin (fast local disk).

    Examples:
      canfar lab agent install kilo
      canfar lab agent install agy omp pi freebuff
      canfar lab agent install --restore
    """
    opts = get_opts(ctx)
    names = list(tools or [])
    if restore:
        names = [*names, *(n for n in _agents_to_restore() if n not in names)]
        if not names:
            if opts.json:
                ui.print_json({"ok": True, "tools": [], "results": [], "errors": []})
            elif not opts.quiet:
                ui.print_ok("Nothing to restore: every remembered agent is installed.")
            return
    if not names:
        if opts.json:
            ui.print_json(
                {
                    "help": "canfar lab agent install NAME [NAME…]",
                    "try": ["list"],
                }
            )
            return
        ui.print_hint("Install needs an agent name.")
        ui.print_hint("  canfar lab agent list")
        ui.print_hint("  canfar lab agent install NAME [NAME…]")
        return

    results: list[dict[str, Any]] = []
    for tool in names:
        try:
            if not opts.json and not opts.quiet and tool == "hermes" and not opts.dry_run:
                ui.print_hint(
                    "Hermes bootstraps uv, Python, Node, and clones the agent repo — "
                    "often 5–15 minutes on CANFAR. Installer output streams below."
                )
            _install_one_agent(tool, dry_run=opts.dry_run, setup=not restore)
            if not opts.dry_run:
                from canfar_lab.agent.setup_state import remember_agent

                remember_agent(None, tool, installed=True)
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
            help="Also remove leftover /arc home CLIs (~/.local/bin, ~/.<agent>/bin).",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show actions without executing.")
    ] = False,
) -> None:
    """Uninstall a managed agent CLI from $SCRATCH/.local/bin (CANFAR_LAB_BIN_DIR)."""
    from canfar_lab.agent.registry import remove_registry_agent
    from canfar_lab.cli.context import merge_opts

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
        if not opts.dry_run:
            from canfar_lab.agent.setup_state import remember_agent

            remember_agent(None, tool, installed=False)
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
    from canfar_lab.agent.wipe import wipe_agent_state
    from canfar_lab.cli.context import merge_opts

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
    ui.print_ok("Agent layer wiped — restart from scratch with: canfar lab agent setup")


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------
