"""`canfar lab agent keys` commands."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from canfar_lab import ui
from canfar_lab.cli.context import get_opts
from canfar_lab.errors import LabError


def _keys_failed(opts: Any, exc: LabError) -> typer.Exit:
    if opts.json:
        ui.print_json({"ok": False, "error": exc.message, "hint": exc.hint})
    else:
        ui.print_error(str(exc))
    return typer.Exit(1)


keys_app = typer.Typer(
    help=(
        "Model API keys shared by agent CLIs, marimo, and the Studio assistant.\n\n"
        "Values are written to ~/.astroai/lab/.env and dsh credentials (0600) and\n"
        "are never printed. `set` reads the value from stdin, never from argv."
    ),
)


@keys_app.command("list")
def keys_list_cmd(ctx: typer.Context) -> None:
    """Supported keys, where each is set, and which agents read it.

    Examples:
        canfar-lab agent keys list
        canfar-lab --json agent keys list
    """
    from canfar_lab.agent import keys as agent_keys

    opts = get_opts(ctx)
    try:
        rows = agent_keys.status()
    except LabError as exc:
        raise _keys_failed(opts, exc) from exc
    if opts.json:
        ui.print_json({"keys": rows})
        return
    ui.print_hint("  Provider         Key                   Set  Used by")
    ui.print_hint("  ───────────────  ────────────────────  ───  ────────────────────")
    for row in rows:
        mark = "✓" if row["present"] else "-"
        used = ", ".join(row["used_by"][:4]) + ("…" if len(row["used_by"]) > 4 else "")
        ui.print_hint(f"  {row['label']:<15}  {row['key']:<20}  {mark:<3}  {used}")
    ui.print_hint("Set one: canfar-lab agent keys set OPENROUTER_API_KEY  (paste, then Enter)")


@keys_app.command("set")
def keys_set_cmd(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Key name, e.g. OPENROUTER_API_KEY.")],
) -> None:
    """Store a key; the value comes from stdin (or a hidden prompt on a TTY).

    Examples:
        canfar-lab agent keys set OPENROUTER_API_KEY
        printf %s "$KEY" | canfar-lab agent keys set ANTHROPIC_API_KEY
    """
    import sys

    from canfar_lab.agent import keys as agent_keys

    opts = get_opts(ctx)
    value = typer.prompt(name, hide_input=True) if sys.stdin.isatty() else sys.stdin.readline()
    try:
        result = agent_keys.set_key(None, name, value)
    except LabError as exc:
        raise _keys_failed(opts, exc) from exc
    if opts.json:
        ui.print_json(result)
        return
    ui.print_ok(f"{name} saved (new terminals and the assistant pick it up)")


@keys_app.command("unset")
def keys_unset_cmd(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Key name to remove.")],
) -> None:
    """Remove a key from the shared dotenv and dsh credentials.

    Examples:
        canfar-lab agent keys unset DEEPSEEK_API_KEY
    """
    from canfar_lab.agent import keys as agent_keys

    opts = get_opts(ctx)
    try:
        result = agent_keys.unset_key(None, name)
    except LabError as exc:
        raise _keys_failed(opts, exc) from exc
    if opts.json:
        ui.print_json(result)
        return
    ui.print_ok(f"{name} removed" if result["changed"] else f"{name} was not set")
