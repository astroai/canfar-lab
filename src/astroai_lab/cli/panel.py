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
        "  astroai panel models\n"
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
                "try": ["run", "web", "doctor", "models", "routers"],
                "logo": str(brand_logo_path()) if brand_logo_path() else None,
            }
        )
        return
    ui.print_hint("AstroAI Studio Team — chaired multi-persona review.")
    ui.print_hint('  astroai panel run <repo> "C1: metric ≥ threshold on split" [slug]')
    ui.print_hint("  astroai panel web <repo> --port 3080")
    ui.print_hint("  astroai panel doctor          # route + keys + pin health")
    ui.print_hint("  astroai panel models|routers  # catalog vs preset")


@panel_app.command("run")
def panel_run(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Repo checkout under review.")] = None,
    claims: Annotated[
        str | None,
        typer.Argument(help='Falsifiable claims, e.g. "C1: …; C2: …".'),
    ] = None,
    slug: Annotated[str, typer.Argument(help="Panel slug (default: review).")] = "review",
    web: Annotated[bool, typer.Option("--web", help="Serve the web UI (laptop only).")] = False,
    port: Annotated[int, typer.Option("--port", help="Web UI port.")] = 3080,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print plan without executing.")] = (
        False
    ),
) -> None:
    """Run a headless AstroAI Panel on REPO for CLAIMS (or --web where reachable).

    Examples:
      astroai panel run . "C1: coverage ≥ 0.9 on 2024 holdout" smoke
      astroai panel run --web --port 3080
      astroai panel run . "C1: …" --dry-run
    """
    from astroai_lab import panel as _panel

    opts = merge_opts(ctx, dry_run=dry_run)
    if web:
        _panel_web(repo, port, opts.dry_run)
        return
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
    note = result.get("fallback_note")
    if note:
        ui.print_warn(str(note))
    ui.print_ok(f"Panel {result['panel_id']} → {result['report_dir']} ({result['route']})")
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
    repair: Annotated[
        bool,
        typer.Option(
            "--repair",
            help="If the settings pin has no key, retarget to the preferred available router.",
        ),
    ] = False,
) -> None:
    """Check dsh, keys, pin health, and preset vs catalog.

    Examples:
      astroai panel doctor
      astroai --json panel doctor
      astroai panel doctor --repair
    """
    from astroai_lab.agent import review_bench as _rb
    from astroai_lab.agent.support import brand_logo_path, load_support, routers_status

    opts = merge_opts(ctx)
    catalog = load_support()
    keys = _rb.discover_dsh_keys()
    health = _rb.resolve_panel_route(keys=keys)
    if repair and health["pin_orphaned"] and health["preferred"] and not opts.dry_run:
        _rb.ensure_dsh_settings(dry_run=False, force_provider=str(health["preferred"]))
        health = _rb.resolve_panel_route(keys=keys)
        ui.print_ok(f"Repaired settings pin → {health['effective']}")

    effective = health["effective"]
    preset_router = "opencode-go"  # shipped agent.cordis.yml targets OpenCode Go ids
    pins = _rb.extract_preset_role_models()
    remaps = _rb.panel_role_pins(str(effective) if effective else preset_router)
    unknown_on_shipped: list[str] = []
    shipped = catalog.router_by_id(preset_router)
    allowed_shipped = set(shipped.panel_models) if shipped else set()
    for role, model in pins.items():
        if allowed_shipped and model not in allowed_shipped:
            unknown_on_shipped.append(f"{role}:{model}")

    issues: list[str] = []
    if not keys:
        issues.append("No provider keys found (OPENCODE_API_KEY / DEEPSEEK_API_KEY / …).")
    elif health["pin_orphaned"]:
        issues.append(
            f"Settings pin {health['pinned']} has no key; "
            f"effective route is {effective or '(none)'}."
        )
    if unknown_on_shipped:
        issues.append(
            "Preset model ids unknown on opencode-go catalog: " + ", ".join(unknown_on_shipped)
        )

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
        "preferred": health["preferred"],
        "pinned": health["pinned"],
        "effective": effective,
        "pin_orphaned": health["pin_orphaned"],
        "keys_present": health["keys_present"],
        "routers": routers_status(keys_present=keys),
        "preset_pins": pins,
        "catalog_remaps_for_effective": remaps,
        "unknown_on_shipped_preset": unknown_on_shipped,
        "headless_tip": (
            "Headless: prefer deepseek-official. OpenCode Go may need a session "
            "header — `panel run` auto-falls back when that fails."
        ),
    }
    if opts.json:
        ui.print_json(payload)
        if issues and not keys:
            raise typer.Exit(1)
        return

    ui.print_hint("AstroAI Panel doctor")
    ui.print_hint(f"  dsh: {'on PATH' if dsh_bin else 'missing'} (pin {_rb.DSH_VERSION})")
    ui.print_hint(
        f"  Route: effective={effective or '-'}  "
        f"preferred={health['preferred'] or '-'}  "
        f"pinned={health['pinned'] or '-'}"
    )
    if health["pin_orphaned"]:
        ui.print_warn(
            f"  Pin orphan: {health['pinned']} (no key) — "
            f"headless uses {effective}. Fix: `astroai panel doctor --repair`"
        )
    keys_list = ", ".join(str(k) for k in health["keys_present"]) or "(none)"
    ui.print_hint(f"  Keys: {keys_list}")
    if not keys:
        ui.print_error("  No keys — run `opencode auth login` or export DEEPSEEK_API_KEY")
    elif unknown_on_shipped:
        ui.print_warn(f"  Preset vs opencode-go catalog: {', '.join(unknown_on_shipped)}")
    else:
        ui.print_ok("  Shipped preset pins match the opencode-go catalog")
    if effective and effective != preset_router:
        ui.print_hint(
            f"  Note: preset ids are opencode-go-oriented; "
            f"active {effective} remaps flash roles (see `panel models`)."
        )
    ui.print_hint(f"  Tip: {payload['headless_tip']}")
    ui.print_hint("  More: astroai panel models | routers")
    if not keys:
        raise typer.Exit(1)
    if health["pin_orphaned"]:
        raise typer.Exit(2)


@panel_app.command("models")
def panel_models(ctx: typer.Context) -> None:
    """Shipped preset pins vs catalog remaps for the effective router.

    Example:
      astroai panel models
      astroai --json panel models
    """
    from astroai_lab.agent import review_bench as _rb
    from astroai_lab.agent.support import load_support

    opts = merge_opts(ctx)
    catalog = load_support()
    keys = _rb.discover_dsh_keys()
    health = _rb.resolve_panel_route(keys=keys)
    route = str(health["effective"] or "opencode-go")
    pins = _rb.extract_preset_role_models()
    catalog_pins = _rb.panel_role_pins(route)
    shipped = catalog.router_by_id("opencode-go")
    allowed_shipped = set(shipped.panel_models) if shipped else set()
    rows: list[dict[str, Any]] = []
    for role in catalog.panel_roles:
        preset = pins.get(role)
        want = catalog_pins.get(role)
        remap = bool(preset and want and preset != want)
        unknown = bool(preset and allowed_shipped and preset not in allowed_shipped)
        rows.append(
            {
                "role": role,
                "preset": preset,
                "for_route": want,
                "remap": remap,
                "unknown": unknown,
            }
        )
    active_router = catalog.router_by_id(route)
    payload = {
        "route": route,
        "preferred": health["preferred"],
        "pinned": health["pinned"],
        "pin_orphaned": health["pin_orphaned"],
        "key_present": any(catalog.key_to_route().get(k, (None, None))[0] == route for k in keys),
        "panel_default": (
            active_router.panel_default
            if active_router is not None
            else (shipped.panel_default if shipped is not None else None)
        ),
        "roles": rows,
    }
    if opts.json:
        ui.print_json(payload)
        return
    ui.print_hint(f"AstroAI Panel models (effective router: {route})")
    if health["pin_orphaned"]:
        ui.print_warn(f"  Settings pin {health['pinned']} has no key — showing remaps for {route}")
    ui.print_hint("  Role                 Shipped preset                 For this router")
    ui.print_hint("  ───────────────────  ─────────────────────────────  ────────────────")
    for row in rows:
        mark = ""
        if row["unknown"]:
            mark = " ?"
        elif row["remap"]:
            mark = " →"
        ui.print_hint(
            f"  {row['role']:<19}  {(row['preset'] or '-'):<29}  {(row['for_route'] or '-')}{mark}"
        )
    ui.print_hint("  → remap when this router is active   ? unknown on shipped opencode-go catalog")
    if health["pin_orphaned"]:
        ui.print_hint("  Fix pin: astroai panel doctor --repair")


@panel_app.command("routers")
def panel_routers(ctx: typer.Context) -> None:
    """Supported routers from support.yaml + key presence.

    Example:
      astroai panel routers
    """
    from astroai_lab.agent import review_bench as _rb
    from astroai_lab.agent.support import routers_status

    opts = merge_opts(ctx)
    keys = _rb.discover_dsh_keys()
    health = _rb.resolve_panel_route(keys=keys)
    rows = routers_status(keys_present=keys)
    for row in rows:
        row["preferred"] = row["id"] == health["preferred"]
        row["pinned"] = row["id"] == health["pinned"]
        row["effective"] = row["id"] == health["effective"]
    if opts.json:
        ui.print_json({"routers": rows, "route": health})
        return
    ui.print_hint("AstroAI Panel routers (preference order)")
    ui.print_hint("  Mark     Id                  Key                   Present  Default")
    ui.print_hint("  ───────  ──────────────────  ────────────────────  ───────  ────────────")
    for row in rows:
        marks = []
        if row["effective"]:
            marks.append("*")
        if row["preferred"] and not row["effective"]:
            marks.append("P")
        if row["pinned"]:
            marks.append("pin" if not health["pin_orphaned"] else "orphan")
        mark = ",".join(marks) if marks else "-"
        present = "✓" if row["key_present"] else "-"
        ui.print_hint(
            f"  {mark:<7}  {row['id']:<18}  {row['key']:<20}  {present:<7}  {row['panel_default']}"
        )
        if row.get("notes"):
            ui.print_hint(f"        {row['notes']}")
    ui.print_hint("  * effective   P preferred (unused)   pin/orphan = ~/.dsh settings pin")
    if health["pin_orphaned"]:
        ui.print_warn("  Orphan pin — `astroai panel doctor --repair`")


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
        raise typer.Exit(2)
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
