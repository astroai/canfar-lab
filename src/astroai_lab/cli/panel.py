"""`astroai panel` (alias `astroai review`): AstroAI Panel."""

from __future__ import annotations

import os
import shutil
from typing import Annotated, Any

import typer

from astroai_lab import ui
from astroai_lab.cli.context import merge_opts
from astroai_lab.errors import LabError

panel_app = typer.Typer(
    help=(
        "AstroAI Studio Team — chaired multi-persona review "
        "(headless everywhere, web where reachable).\n\n"
        "Examples:\n"
        '  astroai panel run ~/src/astroai/torchsky "C1: …" smoke\n'
        "  astroai panel web . --port 3080\n"
        "  astroai panel doctor\n"
        "  astroai panel routers"
    ),
    rich_markup_mode="rich",
)


@panel_app.callback(invoke_without_command=True)
def panel_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    opts = merge_opts(ctx)
    if opts.json:
        from astroai_lab.agent.support import brand_logo_path

        ui.print_json(
            {
                "product": "AstroAI Panel",
                "help": "astroai panel --help",
                "try": ["run", "web", "doctor", "routers"],
                "logo": str(brand_logo_path()) if brand_logo_path() else None,
            }
        )
        return
    ui.print_hint("AstroAI Studio Team — chaired multi-persona review.")
    ui.print_hint('  astroai panel run <repo> "C1: metric ≥ threshold on split" [slug]')
    ui.print_hint("  astroai panel web <repo> --port 3080")
    ui.print_hint("  astroai panel doctor          # keys + provider refs (no model choice)")
    ui.print_hint("  astroai panel routers         # key catalog")


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

    Model/provider choice stays in dsh Settings; astroai only ensures
    credential references.

    Examples:
      astroai panel run . "C1: coverage ≥ 0.9 on 2024 holdout" smoke
      astroai panel run . "C1: …" --dry-run
    """
    from astroai_lab import panel as _panel

    opts = merge_opts(ctx, dry_run=dry_run)
    if not claims:
        ui.print_error('panel run needs CLAIMS, e.g. "C1: coverage ≥ 0.9 on holdout"')
        ui.print_hint('  astroai panel run <repo> "C1: …" [slug]')
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
    ui.print_hint(f"  astroai panel status {result['report_dir']}")


@panel_app.command("status")
def panel_status_cmd(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Panel dir (…/panel/<date>-<slug>).")] = "",
) -> None:
    """Print the verdict table from a finished panel report.

    Example:
      astroai panel status panel/2026-09-11-smoke
    """
    from astroai_lab import panel as _panel

    opts = merge_opts(ctx)
    if not target:
        ui.print_error("panel status needs a panel dir")
        ui.print_hint("  astroai panel status panel/2026-09-11-smoke")
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


@panel_app.command("web")
def panel_web(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Repo checkout to serve.")] = None,
    port: Annotated[int, typer.Option("--port", help="Web UI port.")] = 3080,
) -> None:
    """Serve the dsh web UI for REPO (laptop; warns on Skaha sessions).

    Example:
      astroai panel web . --port 3080
    """
    opts = merge_opts(ctx)
    _panel_web(repo, port, opts.dry_run)


@panel_app.command("doctor")
def panel_doctor(
    ctx: typer.Context,
) -> None:
    """Check dsh, keys, and provider credential refs (no model choice).

    Examples:
      astroai panel doctor
      astroai --json panel doctor
    """
    from astroai_lab.agent import review_bench as _rb
    from astroai_lab.agent.support import brand_logo_path, routers_status

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
        "note": "Provider/model choice lives in dsh Settings; astroai manages keys only.",
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
            f"  dsh Settings provider: {health['pinned']} (yours; astroai never changes it)"
        )
    keys_list = ", ".join(str(k) for k in health["keys_present"]) or "(none)"
    ui.print_hint(f"  Keys: {keys_list}")
    if not keys:
        ui.print_error("  No keys — run `opencode auth login` or export DEEPSEEK_API_KEY")
        raise typer.Exit(1)
    ui.print_ok("  Keys present; provider refs ensured by `agent setup`")
    ui.print_hint("  More: astroai panel routers | astroai agent env --with-dsh")


@panel_app.command("models")
def panel_models(ctx: typer.Context) -> None:
    """Deprecated: astroai no longer presets models (use dsh Settings)."""
    opts = merge_opts(ctx)
    payload = {
        "ok": False,
        "deprecated": True,
        "hint": "astroai does not preset models; choose provider/model in dsh Settings → Models.",
    }
    if opts.json:
        ui.print_json(payload)
    else:
        ui.print_warn("`astroai panel models` is removed — models live in dsh Settings → Models.")
    raise typer.Exit(2)


@panel_app.command("routers")
def panel_routers(ctx: typer.Context) -> None:
    """Supported routers from support.yaml + key presence (no model choice).

    Example:
      astroai panel routers
    """
    from astroai_lab.agent import review_bench as _rb
    from astroai_lab.agent.support import routers_status

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


def _on_skaha() -> bool:
    names = {key.upper() for key in os.environ}
    return "SKAHA_SESSIONID" in names or "SKAHA_HOSTNAME" in names


def _panel_web(repo: str | None, port: int, dry_run: bool) -> None:
    from astroai_lab import panel as _panel
    from astroai_lab import studio as _studio
    from astroai_lab.utils.subprocess import run

    ui.print_warn(
        "`astroai panel web` is soft-deprecated — prefer `astroai studio` "
        "(same dsh UI; Team preset for chaired review)."
    )
    repo_path = _panel.resolve_repo(repo)
    patch = repo_path / ".dsh" / "cordis.patch.yml"
    if not patch.is_file():
        _panel.scaffold_repo_dsh(repo_path)
    if _on_skaha():
        ui.print_warn(
            "Web UI is not reachable behind /proxy on a Skaha contributed "
            "session — use the `astroai/studio` image or `astroai panel run`."
        )
        if dry_run:
            ui.print_json({"repo": str(repo_path), "web": False})
            return
        raise typer.Exit(1)
    dsh_bin = _studio.dsh_binary()
    if dsh_bin is None:
        ui.print_error(str(_studio.dsh_missing_error()))
        raise typer.Exit(1)
    cmd = [dsh_bin, "--profile", "web"]
    if patch.is_file():
        cmd += ["--patch", str(patch)]
    cmd += ["--no-open", "--port", str(port)]
    if dry_run:
        ui.print_json({"repo": str(repo_path), "cmd": cmd})
        return
    ui.print_hint(f"AstroAI Panel web → http://127.0.0.1:{port}  (repo {repo_path})")
    ui.print_hint("  Prefer: astroai studio")
    run(cmd, cwd=repo_path)
