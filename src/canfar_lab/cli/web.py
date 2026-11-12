"""``canfar lab web``: serve the Lab tab (agents, save/restore, cluster)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer


def web_cmd(
    port: Annotated[int, typer.Option("--port", help="Loopback port.")] = 4792,
    host: Annotated[
        str, typer.Option("--host", help="Loopback address: 127.0.0.1 or ::1.")
    ] = "127.0.0.1",
    token_file: Annotated[
        Path | None,
        typer.Option(
            "--token-file",
            help="Write the bearer token here (0600). Default: the session runtime dir.",
        ),
    ] = None,
    restore: Annotated[
        bool,
        typer.Option("--restore", help="At start, reinstall the agents used in earlier sessions."),
    ] = False,
    restore_code: Annotated[
        bool,
        typer.Option(
            "--restore-code",
            help="At start, restore the latest snapshot when $SCRATCH/src is empty.",
        ),
    ] = False,
) -> None:
    """Serve the Lab tab on loopback for the dsh image proxy.

    The token comes from $CANFAR_LAB_WEB_TOKEN, or is generated and written to
    --token-file. The proxy sends it as a bearer header. On a laptop, open the
    printed ?token= URL once.
    """
    from canfar_lab.web.server import default_token_file, serve

    serve(
        host=host,
        port=port,
        token_file=token_file or default_token_file(),
        restore=restore,
        restore_code=restore_code,
    )


def register(app: typer.Typer) -> None:
    app.command("web")(web_cmd)
