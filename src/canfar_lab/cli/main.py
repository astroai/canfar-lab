"""Command-line interface for canfar-lab."""

from __future__ import annotations

from typing import Annotated

import typer

from canfar_lab.cli import clean as clean_mod
from canfar_lab.cli import init_clone_env, open_cmd
from canfar_lab.cli import status as status_mod
from canfar_lab.cli.agent_cmd import agent_app
from canfar_lab.cli.banner import show_banner
from canfar_lab.cli.config import config_app
from canfar_lab.cli.context import GlobalOpts, merge_opts
from canfar_lab.cli.env import env_app
from canfar_lab.cli.help_cmd import command_path_completer, help_cmd_body
from canfar_lab.cli.kernel import kernel_app
from canfar_lab.cli.panel import panel_app
from canfar_lab.cli.studio import studio_app
from canfar_lab.cli.sync import sync_app
from canfar_lab.version import display_version
from canfar_workload.cli import register as register_workload

app = typer.Typer(
    name="lab",
    help="In-session workbench for CANFAR Science Platform.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
    epilog="Platform client: [bold]canfar[/bold] — https://opencadc.github.io/canfar/",
)

init_clone_env.register(app)
status_mod.register(app)
clean_mod.register(app)
app.add_typer(env_app, name="env")
app.add_typer(config_app, name="config")
app.add_typer(kernel_app, name="kernel")
app.add_typer(agent_app, name="agent")
app.add_typer(studio_app, name="studio")
open_cmd.register(app)
app.add_typer(panel_app, name="panel")
app.add_typer(sync_app, name="sync")
register_workload(app, jobs_as="jobs")

lab_app = app


@app.callback()
def main(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Non-interactive; skip confirmations.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show actions without executing.")
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Minimal output.")] = False,
    version: Annotated[bool | None, typer.Option("--version", "-V", help="Show version.")] = None,
) -> None:
    """In-session workbench for environments and AI agents."""
    ctx.obj = GlobalOpts(json=json_output, yes=yes, dry_run=dry_run, quiet=quiet)
    if version:
        typer.echo(f"canfar-lab {display_version()}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        show_banner(json_output=json_output)
        raise typer.Exit()


@app.command("help")
def help_cmd(
    ctx: typer.Context,
    command: Annotated[
        str | None,
        typer.Option(
            "--command",
            "-c",
            help="Show help for one command path, e.g. 'agent list'.",
            autocompletion=command_path_completer(app),
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show --help for the app and every subcommand.

    Equivalent to running `canfar lab --help` on the app and each command
    in registration order. Use `--command <path>` (or `-c`) to show a single
    command's help; the full dump pages through `less` on interactive
    terminals. With `--json`, prints a command inventory (no `-c`) or
    structured help for one command.

    Examples:
        canfar lab help
        canfar lab help -c agent
        canfar lab help --command "agent list"
        canfar lab help --json
        canfar lab help -c status --json
    """
    opts = merge_opts(ctx, json_output=json_output)
    help_cmd_body(app, command, json_output=opts.json)


def main_entry() -> None:
    app()


if __name__ == "__main__":
    main_entry()
