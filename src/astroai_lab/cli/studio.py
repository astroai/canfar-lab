"""``astroai studio``: dsh-based coding portal (laptop + CANFAR)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

import typer

from astroai_lab import ui
from astroai_lab.cli.context import merge_opts
from astroai_lab.errors import LabError

studio_app = typer.Typer(
    help=(
        "AstroAI Studio — dsh coding portal "
        "(laptop localhost or CANFAR studio session).\n\n"
        "Examples:\n"
        "  astroai studio\n"
        "  astroai studio ~/src/astroai/torchfits --port 3080\n"
        "  astroai studio --prepare\n"
        "  astroai studio --doctor\n"
        "  astroai studio --profile canfar --prepare"
    ),
    rich_markup_mode="rich",
    invoke_without_command=True,
)


def _on_skaha() -> bool:
    # Match panel.py: platform may set lowercase skaha_sessionid.
    names = {key.upper() for key in os.environ}
    return "SKAHA_SESSIONID" in names or "SKAHA_HOSTNAME" in names


@studio_app.callback(invoke_without_command=True)
def studio_main(
    ctx: typer.Context,
    repo: Annotated[
        str | None,
        typer.Argument(help="Repo / workspace directory (default: cwd)."),
    ] = None,
    port: Annotated[int, typer.Option("--port", help="dsh web port.")] = 3080,
    profile: Annotated[
        Literal["laptop", "canfar"] | None,
        typer.Option("--profile", help="Resource profile (default: auto-detect)."),
    ] = None,
    prepare: Annotated[
        bool,
        typer.Option(
            "--prepare",
            help="Only provision review-bench, dotenv, dsh settings, the Studio profile.",
        ),
    ] = False,
    doctor: Annotated[
        bool,
        typer.Option("--doctor", help="Pre-flight dsh, profile, skills, providers, MCP; exit."),
    ] = False,
    no_team: Annotated[
        bool,
        typer.Option(
            "--no-team",
            help="Skip the experimental Agent Teams layers (stock web composition).",
        ),
    ] = False,
    no_install: Annotated[
        bool,
        typer.Option(
            "--no-install",
            help="Never install missing bundles (offline / image-baked setups).",
        ),
    ] = False,
    mcp_bin: Annotated[
        str | None,
        typer.Option(
            "--mcp-bin",
            help="`astroai` binary the Studio MCP row runs (default: the one on PATH).",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite managed studio/dsh scaffolding."),
    ] = False,
    skills: Annotated[
        bool,
        typer.Option("--skills", help="Print skills.sh / agentskills onboarding and exit."),
    ] = False,
) -> None:
    """Launch AstroAI Studio (dsh web) or prepare/diagnose its configuration.

    On a Skaha *non-studio* session (e.g. vscode), prefer the dedicated
    ``astroai/studio`` image — ``dsh web`` behind ``/proxy/PORT`` is broken.
    """
    if ctx.invoked_subcommand is not None:
        return

    from astroai_lab import panel as _panel
    from astroai_lab import studio as _studio
    from astroai_lab import studio_profile as _sp
    from astroai_lab.utils.subprocess import run

    opts = merge_opts(ctx)

    if skills:
        text = _studio.skills_onboarding_hint()
        if opts.json:
            ui.print_json({"skills": text})
        else:
            typer.echo(text)
        return

    resolved_profile = profile or _studio.detect_profile()
    with_team = not no_team
    pinned_bin: str | None = None
    if mcp_bin:
        candidate = Path(mcp_bin).expanduser()
        if not candidate.is_file():
            ui.print_error(f"--mcp-bin is not a file: {candidate}")
            raise typer.Exit(1)
        pinned_bin = str(candidate)

    if doctor:
        report = _sp.doctor(
            home=Path.home(),
            profile=resolved_profile,
            port=port,
            with_team=with_team,
        )
        if opts.json:
            ui.print_json(report)
        else:
            for check in report["checks"]:
                mark = "ok  " if check["ok"] else "FAIL"
                ui.print_hint(f"  [{mark}] {check['name']}: {check['detail']}")
                if not check["ok"] and check.get("hint"):
                    ui.print_hint(f"         → {check['hint']}")
            if report["ok"]:
                ui.print_ok(f"Studio doctor: all checks passed ({resolved_profile})")
            elif report["fatal"]:
                ui.print_error("Studio doctor: blocking problems above")
            else:
                ui.print_warn("Studio doctor: non-blocking problems above")
        if not report["ok"] and report["fatal"]:
            raise typer.Exit(1)
        return

    try:
        prep = _studio.prepare_studio(
            profile=resolved_profile,
            dry_run=opts.dry_run,
            force=force,
            with_team=with_team,
            install_bundles=not no_install,
            mcp_bin=pinned_bin,
        )
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc

    if prepare:
        if opts.json or opts.dry_run:
            ui.print_json(prep)
            return
        ui.print_ok(
            f"Studio prepared (profile={prep['profile']}, dsh profile={prep['dsh_profile']})"
        )
        for action in prep.get("actions") or []:
            ui.print_hint(f"  {action}")
        if prep.get("degraded"):
            ui.print_warn("  Team layers are not active — see the actions above.")
        ui.print_hint(f"  state: {prep.get('state_root')}")
        ui.print_hint(f"  {prep.get('skills_hint', '')}".rstrip())
        return

    try:
        repo_path = _studio.resolve_repo(repo)
        dsh_bin = _studio.dsh_binary()
        if dsh_bin is None:
            raise _studio.dsh_missing_error()
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc

    # Scaffold per-repo .dsh patch when missing (same as panel).
    patch = repo_path / ".dsh" / "cordis.patch.yml"
    if not patch.is_file():
        _panel.scaffold_repo_dsh(repo_path)

    # Warn on Skaha when not already inside the studio image / dedicated session.
    if _on_skaha() and os.environ.get("ASTROAI_SESSION_KIND", "").lower() != "studio":
        ui.print_warn(
            "On Skaha, use the contributed `astroai/studio` image (Connect URL). "
            "`dsh web` behind vscode/notebook `/proxy/PORT/` is not supported — "
            "see https://github.com/astroai/canfar-containers/blob/main/docs/STUDIO.md "
            "(or docs/studio.md in this repo). Launching anyway for loopback-only use."
        )

    cmd = _studio.studio_web_cmd(repo_path, port=port, profile=resolved_profile, dsh_bin=dsh_bin)
    studio_env = _studio.studio_env(profile=resolved_profile)
    env = {**os.environ, **studio_env}
    if opts.dry_run or opts.json:
        ui.print_json(
            {
                "repo": str(repo_path),
                "profile": resolved_profile,
                "dsh_profile": _sp.STUDIO_PROFILE_NAME,
                "cmd": cmd,
                "env": studio_env,
                "prepare": prep,
            }
        )
        return

    ui.print_hint(
        f"AstroAI Studio ({resolved_profile}) → http://127.0.0.1:{port}  (repo {repo_path})"
    )
    ui.print_hint(
        f"  harness profile: {_sp.STUDIO_PROFILE_NAME} — state in {prep.get('state_root')}"
    )
    ui.print_hint("  Team review: New session → preset «AstroAI Studio Team»")
    if with_team:
        ui.print_hint("  Agent Teams: ask for a team in the chat (roster + shared task board)")
    ui.print_hint(f"  {_studio.skills_onboarding_hint().splitlines()[1].strip()}")
    run(cmd, cwd=repo_path, env=env)
