"""`canfar lab panel`: headless AstroAI Studio Team review."""

from __future__ import annotations

import shutil
from typing import Annotated, Any

import typer

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.errors import LabError

panel_app = typer.Typer(
    help=(
        "AstroAI Studio Team — chaired multi-persona review (headless).\n\n"
        "Examples:\n"
        '  canfar lab panel run ~/src/astroai/torchsky "C1: …" smoke\n'
        "  canfar lab panel doctor\n"
        "  canfar lab panel routers"
    ),
    rich_markup_mode="rich",
)


@panel_app.callback(invoke_without_command=True)
def panel_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    opts = merge_opts(ctx)
    if opts.json:
        from canfar_lab.agent.support import brand_logo_path

        ui.print_json(
            {
                "product": "AstroAI Panel",
                "help": "canfar lab panel --help",
                "try": ["run", "doctor", "routers"],
                "logo": str(brand_logo_path()) if brand_logo_path() else None,
            }
        )
        return
    ui.print_hint("AstroAI Studio Team — chaired multi-persona review.")
    ui.print_hint('  canfar lab panel run <repo> "C1: metric ≥ threshold on split" [slug]')
    ui.print_hint("  canfar lab panel doctor          # keys + provider refs (no model choice)")
    ui.print_hint("  canfar lab panel routers         # key catalog")


@panel_app.command("run")
def panel_run(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Repo checkout under review.")] = None,
    claims: Annotated[
        str | None,
        typer.Argument(help='Falsifiable claims, e.g. "C1: …; C2: …".'),
    ] = None,
    slug: Annotated[str, typer.Argument(help="Panel slug (default: review).")] = "review",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print plan without executing.")] = (
        False
    ),
) -> None:
    """Run a headless AstroAI Panel on REPO for CLAIMS.

    Model/provider choice stays in dsh Settings; canfar lab only ensures
    credential references.

    Examples:
      canfar lab panel run . "C1: coverage ≥ 0.9 on 2024 holdout" smoke
      canfar lab panel run . "C1: …" --dry-run
    """
    from canfar_lab import panel as _panel

    opts = merge_opts(ctx, dry_run=dry_run)
    if not claims:
        ui.print_error('panel run needs CLAIMS, e.g. "C1: coverage ≥ 0.9 on holdout"')
        ui.print_hint('  canfar lab panel run <repo> "C1: …" [slug]')
        raise typer.Exit(1)
    try:
        result = _panel.run_panel(repo, claims, slug, dry_run=opts.dry_run)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    if opts.dry_run or opts.json:
        ui.print_json(result)
        return
    ui.print_ok(f"Panel {result['panel_id']} → {result['report_dir']}")
    ui.print_hint(f"  canfar lab panel status {result['report_dir']}")


@panel_app.command("status")
def panel_status_cmd(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Panel dir (…/panel/<date>-<slug>).")] = "",
) -> None:
    """Print the verdict table from a finished panel report.

    Example:
      canfar lab panel status panel/2026-09-11-smoke
    """
    from canfar_lab import panel as _panel

    opts = merge_opts(ctx)
    if not target:
        ui.print_error("panel status needs a panel dir")
        ui.print_hint("  canfar lab panel status panel/2026-09-11-smoke")
        raise typer.Exit(1)
    try:
        table = _panel.panel_status(target)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    if opts.json:
        ui.print_json({"panel": target, "verdicts": table})
    else:
        typer.echo(table)


@panel_app.command("doctor")
def panel_doctor(
    ctx: typer.Context,
) -> None:
    """Check dsh, keys, and provider credential refs (no model choice).

    Examples:
      canfar lab panel doctor
      canfar lab --json panel doctor
    """
    from canfar_lab.agent import review_bench as _rb
    from canfar_lab.agent.support import brand_logo_path, routers_status

    opts = merge_opts(ctx)
    keys = _rb.discover_dsh_keys()
    health = _rb.resolve_panel_route(keys=keys)

    issues: list[str] = []
    if not keys:
        issues.append("No provider keys found (OPENCODE_API_KEY / DEEPSEEK_API_KEY / …).")

    dsh_bin = shutil.which("dsh")
    logo = brand_logo_path()
    payload: dict[str, Any] = {
        "product": "AstroAI Panel",
        "ok": not issues,
        "issues": issues,
        "logo": str(logo) if logo else None,
        "dsh_on_path": bool(dsh_bin),
        "dsh_bin": dsh_bin,
        "dsh_version_pin": _rb.DSH_VERSION,
        "pinned": health["pinned"],
        "keys_present": health["keys_present"],
        "routers": routers_status(keys_present=keys),
        "note": "Provider/model choice lives in dsh Settings; canfar lab manages keys only.",
    }
    if opts.json:
        ui.print_json(payload)
        if issues:
            raise typer.Exit(1)
        return

    ui.print_hint("AstroAI Panel doctor (keys only — models live in dsh Settings)")
    ui.print_hint(f"  dsh: {'on PATH' if dsh_bin else 'missing'} (pin {_rb.DSH_VERSION})")
    if health["pinned"]:
        ui.print_hint(
            f"  dsh Settings provider: {health['pinned']} (yours; canfar lab never changes it)"
        )
    keys_list = ", ".join(str(k) for k in health["keys_present"]) or "(none)"
    ui.print_hint(f"  Keys: {keys_list}")
    if not keys:
        ui.print_error("  No keys — run `opencode auth login` or export DEEPSEEK_API_KEY")
        raise typer.Exit(1)
    ui.print_ok("  Keys present; provider refs ensured by `agent setup`")
    ui.print_hint("  More: canfar lab panel routers | canfar lab agent env --with-dsh")


@panel_app.command("routers")
def panel_routers(ctx: typer.Context) -> None:
    """Supported routers from support.yaml + key presence (no model choice).

    Example:
      canfar lab panel routers
    """
    from canfar_lab.agent import review_bench as _rb
    from canfar_lab.agent.support import routers_status

    opts = merge_opts(ctx)
    keys = _rb.discover_dsh_keys()
    health = _rb.resolve_panel_route(keys=keys)
    rows = routers_status(keys_present=keys)
    if opts.json:
        ui.print_json({"routers": rows, "route": health})
        return
    ui.print_hint("AstroAI Panel routers (key catalog — models in dsh Settings)")
    ui.print_hint("  Id                  Key                   Present  dsh route")
    ui.print_hint("  ──────────────────  ────────────────────  ───────  ────────────")
    for row in rows:
        present = "✓" if row["key_present"] else "-"
        ui.print_hint(f"  {row['id']:<18}  {row['key']:<20}  {present:<7}  {row['dsh_route']}")
        if row.get("notes"):
            ui.print_hint(f"        {row['notes']}")
