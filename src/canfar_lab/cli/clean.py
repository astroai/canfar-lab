"""`canfar lab clean` — free home space; package caches, then optional saves."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.core.disk_usage import naturalsize
from canfar_lab.core.home_clean import apply_clean, plan_clean
from canfar_lab.core.paths import resolve_paths


def register(app: typer.Typer) -> None:
    @app.command()
    def clean(
        ctx: typer.Context,
        json_output: Annotated[
            bool, typer.Option("--json", help="Machine-readable output.")
        ] = False,
        yes: Annotated[
            bool, typer.Option("--yes", "-y", help="Delete caches without asking.")
        ] = False,
        dry_run: Annotated[
            bool, typer.Option("--dry-run", help="Show what would be removed.")
        ] = False,
        saves: Annotated[
            bool,
            typer.Option("--saves", help="Also delete saved environments."),
        ] = False,
        config: Annotated[
            bool,
            typer.Option("--config", help="Also delete lab preferences."),
        ] = False,
    ) -> None:
        """Free space on home: whatever is in ~/.cache, then optional saves.

        Lists ``~/.cache`` as it exists now (not a fixed tool list). Those
        files come back the next time you install a package. Saved
        environments and lab preferences are removed only with `--saves` /
        `--config`, or if you confirm. Agent logins: `canfar lab agent wipe`.

        Examples:
            canfar lab clean
            canfar lab clean --yes
            canfar lab clean --yes --saves
            canfar lab clean --dry-run
        """
        opts = merge_opts(ctx, json_output=json_output, yes=yes, dry_run=dry_run)
        paths = resolve_paths()
        plan = plan_clean(paths.home, paths.save_dir)
        do_saves = saves
        do_config = config
        interactive = sys.stdin.isatty() and not opts.json and not opts.dry_run and not opts.yes

        if interactive:
            _print_plan(plan)
            do_caches = bool(plan["caches"]) and typer.confirm(
                "Delete package caches? They will be rebuilt as needed.", default=True
            )
            if plan["saves"] and not do_saves:
                do_saves = typer.confirm("Delete all saved environments?", default=False)
            if plan["config"] is not None and not do_config:
                do_config = typer.confirm("Reset lab preferences?", default=False)
        else:
            do_caches = bool(plan["caches"]) and (opts.yes or opts.dry_run)
            if not opts.json:
                _print_plan(plan)

        if not opts.yes and not opts.dry_run and not interactive:
            if not opts.json:
                ui.print_hint("  Nothing deleted. Re-run with `--yes` to delete caches.")
                if plan["saves"]:
                    ui.print_hint("  Saved environments: `canfar lab clean --yes --saves`")
                if plan["config"] is not None:
                    ui.print_hint("  Lab preferences: `canfar lab clean --yes --config`")
                ui.print_hint("  Agent logins: `canfar lab agent wipe`")
            else:
                ui.print_json({**plan, "actions": [], "ok": True, "dry_run": True})
            return

        actions = apply_clean(
            plan,
            caches=do_caches,
            saves=do_saves,
            config=do_config,
            dry_run=opts.dry_run,
        )
        errors = [a for a in actions if a.get("status") == "error"]
        if opts.json:
            ui.print_json(
                {
                    **plan,
                    "actions": actions,
                    "ok": not errors,
                    "dry_run": opts.dry_run,
                }
            )
            if errors:
                raise typer.Exit(1)
            return
        prefix = "would remove" if opts.dry_run else "removed"
        if not actions:
            ui.print_ok("Nothing to clean")
            return
        for row in actions:
            if row["status"] == "error":
                ui.print_error(f"  {row['path']}: {row.get('detail', 'error')}")
            else:
                ui.print_ok(f"  {prefix} {row['path']}")
        if errors:
            raise typer.Exit(1)
        if opts.dry_run:
            ui.print_hint("  Re-run with `--yes` to delete them.")
        if not do_saves and plan["saves"] and not opts.dry_run:
            ui.print_hint("  Saved environments kept. Pass `--saves` to delete them.")
        if not do_config and plan["config"] is not None and not opts.dry_run:
            ui.print_hint("  Lab preferences kept. Pass `--config` to reset them.")
        ui.print_hint("  Agent logins: `canfar lab agent wipe`")


def _print_plan(plan: dict) -> None:
    caches = plan["caches"]
    if caches:
        ui.print_info(f"Home caches (safe to delete): {naturalsize(plan['cache_bytes'])}")
        for row in caches:
            ui.print_hint(f"  {row['size']:>8}  {row['path']}")
    else:
        ui.print_hint("No caches found on home.")
    saves = plan["saves"]
    if saves:
        ui.print_info(f"Saved environments: {naturalsize(plan['save_bytes'])}")
        for row in saves:
            ui.print_hint(f"  {row['name']:<16} {row['size']:>8}  {row['saved_at']}")
    cfg = plan["config"]
    if cfg is not None:
        ui.print_info(f"Lab preferences: {cfg['path']} ({cfg['size']})")
