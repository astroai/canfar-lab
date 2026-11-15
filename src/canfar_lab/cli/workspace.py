"""``canfar lab workspace``: where code and caches live (kept across sessions)."""

from __future__ import annotations

from typing import Annotated

import typer

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.errors import LabError

workspace_app = typer.Typer(
    name="workspace",
    help="Choose where code lives ($SCRATCH/src, ~/src or your own folder) and where caches go.",
    no_args_is_help=True,
)


def _fail(exc: LabError, as_json: bool) -> None:
    if as_json:
        ui.print_json({"ok": False, "error": exc.message, "hint": exc.hint})
    else:
        ui.print_error(str(exc))
    raise typer.Exit(1) from exc


@workspace_app.command("show")
def show_cmd(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show the code and cache folders and the automatic save settings."""
    from canfar_lab.session import workspace

    opts = merge_opts(ctx, json_output=json_output)
    state = workspace.describe()
    if opts.json:
        ui.print_json({"ok": True, **state})
        return
    auto = state["autosave"]
    ui.print_ok(f"code: {state['code_path']} ({state['code_dir']['mode']})")
    if state["ephemeral"]:
        saves = f"every {auto['interval_min']} min, keep {auto['keep']}"
        ui.print_hint(f"  automatic saves: {saves if auto['enabled'] else 'off'}")
        ui.print_hint(f"  restore at start: {state['restore_at_start']}")
    ui.print_hint(f"  caches: {state['cache_path'] or '-'}")
    if state["restart_needed"]:
        ui.print_warn(f"  tools still run in {state['live_code_path']}: restart them to switch")


@workspace_app.command("path")
def path_cmd(
    ensure: Annotated[bool, typer.Option("--ensure", help="Create the folder if missing.")] = False,
) -> None:
    """Print the code folder (what the session uses as $SRCDIR)."""
    from canfar_lab.session import workspace

    path = workspace.code_dir()
    if ensure:
        path.mkdir(parents=True, exist_ok=True)
    typer.echo(str(path))


@workspace_app.command("set")
def set_cmd(
    ctx: typer.Context,
    mode: Annotated[str, typer.Argument(help="scratch, home or custom.")],
    path: Annotated[str | None, typer.Argument(help="Folder, for custom.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Choose where code lives. Takes effect for new terminals and the next session."""
    from canfar_lab.session import workspace

    opts = merge_opts(ctx, json_output=json_output)
    try:
        result = workspace.set_code_dir(mode, path)
    except LabError as exc:
        _fail(exc, opts.json)
        return
    if opts.json:
        ui.print_json(result)
        return
    ui.print_ok(f"Code folder: {result['new']}")
    if result["changed"]:
        ui.print_hint(f"  projects in {result['old']} were not moved")
