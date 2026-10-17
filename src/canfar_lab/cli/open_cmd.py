"""``canfar-lab open``: Open files or tools in Studio browser interfaces."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.session_urls import contrib_url, local_base_url


def run_open(
    ctx: typer.Context,
    target: str | None = None,
    path: str | None = None,
) -> None:
    """Core logic to resolve tool/file URLs and display/open them."""
    opts = merge_opts(ctx)
    session_id = (
        os.environ.get("SKAHA_SESSIONID") or os.environ.get("skaha_sessionid") or ""  # noqa: SIM112
    ).strip()
    base_url = contrib_url(session_id) if session_id else local_base_url()

    tool = "agent"
    file_path = None

    if target:
        low = target.lower()
        if low in ("jupyter", "notebook", "lab"):
            tool = "jupyter"
            file_path = path
        elif low in ("vscode", "code"):
            tool = "vscode"
            file_path = path
        elif low in ("marimo",):
            tool = "marimo"
            file_path = path
        elif low in ("terminal", "term"):
            tool = "terminal"
        elif low in ("agent", "dsh"):
            tool = "agent"
        elif low in ("hub", "compute"):
            tool = "hub"
        else:
            file_path = target
            ext = Path(target).suffix.lower()
            if ext == ".ipynb":
                tool = "jupyter"
            elif ext == ".py":
                tool = "marimo" if "marimo" in target.lower() else "vscode"
            else:
                tool = "vscode"

    if tool == "jupyter":
        if file_path:
            p = Path(file_path).expanduser().resolve()
            rel = p.name
            try:
                work = Path(os.environ.get("WORK", "/scratch/src")).resolve()
                rel = str(p.relative_to(work))
            except ValueError:
                pass
            url = f"{base_url}/jupyter/lab/tree/{rel}"
        else:
            url = f"{base_url}/jupyter/lab"
    elif tool == "vscode":
        url = f"{base_url}/vscode/"
    elif tool == "marimo":
        url = f"{base_url}/marimo/"
    elif tool == "terminal":
        url = f"{base_url}/terminal/"
    elif tool == "hub":
        url = f"{base_url}/hub/"
    else:
        url = f"{base_url}/"

    if opts.json:
        payload = {"tool": tool, "url": url, "file": file_path, "session_id": session_id or None}
        ui.print_json(payload)
        return

    ui.print_ok(f"Studio {tool.title()} URL:")
    typer.echo(f"  {url}")


def register(app: typer.Typer) -> None:
    @app.command("open", help="Open files, notebooks, or interfaces in Studio browser tabs.")
    def open_cli(
        ctx: typer.Context,
        target: Annotated[
            str | None,
            typer.Argument(help="Tool name (jupyter, vscode, marimo, terminal) or file path."),
        ] = None,
        path: Annotated[
            str | None,
            typer.Argument(help="Optional file path to open within the tool."),
        ] = None,
    ) -> None:
        """Open or get direct browser URL for Studio tools and files.

        Examples:
          canfar lab open jupyter
          canfar lab open jupyter notebooks/analysis.ipynb
          canfar lab open vscode src/main.py
          canfar lab open marimo app.py
          canfar lab open terminal
        """
        run_open(ctx, target, path)


open_app = typer.Typer(
    help="Open files, notebooks, or interfaces in Studio browser tabs.",
    rich_markup_mode="rich",
    invoke_without_command=True,
)


@open_app.callback(invoke_without_command=True)
def open_entry(
    ctx: typer.Context,
    target: Annotated[
        str | None,
        typer.Argument(help="Tool name or file path."),
    ] = None,
    path: Annotated[
        str | None,
        typer.Argument(help="Optional file path."),
    ] = None,
) -> None:
    run_open(ctx, target, path)
