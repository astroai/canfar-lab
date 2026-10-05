from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.core.kernel import list_kernels, register_kernel, unregister_kernel
from canfar_lab.errors import LabError


def _kernel_name_completer(ctx, incomplete: str) -> list[str]:
    """Offer registered kernel names for `kernel unregister` / `--name`.

    Matches typer's autocompletion signature (ctx, incomplete) -> list[str];
    never raises (completion must not crash the CLI when jupyter is absent).
    """
    incomplete = incomplete or ""
    try:
        names = [row["name"] for row in list_kernels()]
    except Exception:  # noqa: BLE001 — completion must never crash the CLI
        return []
    return [n for n in names if n.startswith(incomplete)]


kernel_app = typer.Typer(help="Jupyter kernel registration (notebook sessions).")


@kernel_app.command("ensure")
def kernel_ensure(
    name: Annotated[
        str,
        typer.Option("--name", help="Kernel name.", autocompletion=_kernel_name_completer),
    ] = "astroai",
) -> None:
    """Create/refresh a scratch-safe notebook kernel (no pixi project needed).

    Examples:
        canfar lab kernel ensure
        canfar lab kernel ensure --name student
    """
    from canfar_lab.core.kernel import ensure_scratch_safe_kernel

    try:
        kname = ensure_scratch_safe_kernel(name=name)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    ui.print_ok(f"Scratch-safe kernel ready: {kname}")


@kernel_app.command("register")
def kernel_register(
    path: Annotated[Path | None, typer.Argument(help="Project path (default: cwd).")] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
) -> None:
    """Register project as Jupyter kernel.

    Examples:
        canfar lab kernel register
        canfar lab kernel register $WORK/mylab --name mylab
    """
    project = path or Path.cwd()
    try:
        kname = register_kernel(project, name=name)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    ui.print_ok(f"Kernel registered: {kname}")


@kernel_app.command("list")
def kernel_list(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List registered kernels.

    Examples:
        canfar lab kernel list
        canfar lab kernel list --json
        canfar lab --json kernel list
    """
    opts = merge_opts(ctx, json_output=json_output)
    rows = list_kernels()
    if opts.json:
        ui.print_json(rows)
    else:
        for row in rows:
            ui.print_hint(f"  {row['name']}: {row['path']}")


@kernel_app.command("unregister")
def kernel_unregister(
    name: Annotated[str, typer.Argument(autocompletion=_kernel_name_completer)],
) -> None:
    """Remove a registered kernel.

    Examples:
        canfar lab kernel unregister mylab
    """
    try:
        unregister_kernel(name)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    ui.print_ok(f"Unregistered {name}")
