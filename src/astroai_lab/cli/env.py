"""env group: shell environment export (infra).

The flat save/resume commands are the primary interface; listing is
`astroai save --list`. Image builds copy the packaged profile.sh /
hooks.sh at build time — astroai stays an in-session tool only.
"""

from __future__ import annotations

import shlex
from typing import Annotated

import typer

from astroai_lab import ui
from astroai_lab.cli.context import merge_opts
from astroai_lab.shell.session_env import export_json, export_shell

env_app = typer.Typer(help="Session environment export.")


def _ray_exports() -> dict[str, str]:
    """Best-effort Ray Jobs address for the shell (empty when none).

    Same discovery as jobs/dashboard: env, live ray-manager (``canfar ps``),
    then persisted ``connect-url``. Profile runs this once at login.
    """
    # ponytail: one canfar listing at profile; persist caches for later
    try:
        from astroai_workload.dashboard import resolve_dashboard_url

        url = resolve_dashboard_url()
    except Exception:  # noqa: BLE001 — env export must never fail on Ray state
        return {}
    if not url:
        return {}
    return {"ASTROAI_RAY_JOBS_ADDRESS": url}


@env_app.command("export")
def env_export(
    ctx: typer.Context,
    ensure: Annotated[
        bool,
        typer.Option("--ensure/--no-ensure", help="Create cache and runtime directories."),
    ] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Print bash export statements for the current AstroAI lab session.

    With `--json`, prints the resolved session environment as a JSON object
    instead (same keys and values, no shell syntax).

    Examples:
        eval "$(astroai env export)"
        astroai env export --json
        astroai --json env export
    """
    opts = merge_opts(ctx, json_output=json_output)
    ray = _ray_exports()
    if opts.json:
        payload = export_json(ensure=ensure)
        payload.update(ray)
        ui.print_json(payload)
    else:
        lines = [export_shell(ensure=ensure)]
        lines.extend(f"export {key}={shlex.quote(value)}" for key, value in sorted(ray.items()))
        typer.echo("\n".join(lines))
