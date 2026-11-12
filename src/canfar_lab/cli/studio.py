"""``canfar-lab studio``: Unified 5-in-1 development studio (laptop + CANFAR)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import typer

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.config.settings import get_settings
from canfar_lab.desk import serve as serve_mod
from canfar_lab.errors import LabError

studio_app = typer.Typer(
    help=(
        "AstroAI Studio — Unified 5-in-1 developer workbench "
        "(Agents, Terminal, JupyterLab, Marimo, VS Code).\n\n"
        "Examples:\n"
        "  canfar lab studio\n"
        "  canfar lab studio status\n"
        "  canfar lab studio ~/src/astroai/torchfits --port 3080\n"
        "  canfar lab studio --prepare\n"
        "  canfar lab studio --doctor\n"
        "  canfar lab studio --profile canfar --prepare"
    ),
    rich_markup_mode="rich",
    invoke_without_command=True,
)


def _on_skaha() -> bool:
    names = {key.upper() for key in os.environ}
    return "SKAHA_SESSIONID" in names or "SKAHA_HOSTNAME" in names


def _open_browser(url: str) -> None:
    """Open the Studio URL on macOS. ``dsh`` is started with ``--no-open``."""
    if sys.platform != "darwin":
        return
    subprocess.Popen(
        ["open", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _check_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def studio_listen_plan(profile: str, port: int) -> dict[str, Any]:
    """Laptop pages include the sync dock. A session serves dsh on ``port`` directly."""
    if profile == "laptop":
        return {"desk": True, "url_port": port, "dsh_port": port + 1}
    return {"desk": False, "url_port": port, "dsh_port": port}


def _free_port(start: int) -> int:
    for candidate in range(start, start + 20):
        if not _check_port_open("127.0.0.1", candidate):
            return candidate
    raise LabError(f"no free port from {start} to {start + 19}")


def _desk_on_port(port: int) -> bool:
    """True when ``port`` is the desk proxy, not a bare dsh."""
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/__desk/state", timeout=1.5)
    except httpx.HTTPError:
        return False
    return response.status_code == 403 and "forbidden" in response.text


@studio_app.command("status")
def studio_status_cmd(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show live status of all Studio services (Agents, Terminal, JupyterLab, Marimo, VS Code)."""
    opts = merge_opts(ctx)
    is_json = json_output or opts.json

    # 1. Try querying the in-container proxy status API
    status_data: dict[str, Any] | None = None
    try:
        resp = httpx.get("http://127.0.0.1:5000/api/studio/status", timeout=0.8)
        if resp.status_code == 200:
            parsed = resp.json()
            if isinstance(parsed, dict):
                status_data = parsed
    except (httpx.HTTPError, OSError, ValueError):
        pass

    if status_data is None:
        # Fallback to direct port checks
        services = {
            "agent": {
                "name": "Agents (DSH)",
                "port": 3080,
                "path": "/",
                "up": _check_port_open("127.0.0.1", 3080),
            },
            "terminal": {
                "name": "Terminal (Ghostty)",
                "port": 4793,
                "path": "/terminal/",
                "up": _check_port_open("127.0.0.1", 4793),
            },
            "jupyter": {
                "name": "JupyterLab",
                "port": 8888,
                "path": "/jupyter/",
                "up": _check_port_open("127.0.0.1", 8888),
            },
            "marimo": {
                "name": "Marimo",
                "port": 2718,
                "path": "/marimo/",
                "up": _check_port_open("127.0.0.1", 2718),
            },
            "vscode": {
                "name": "VS Code",
                "port": 8080,
                "path": "/vscode/",
                "up": _check_port_open("127.0.0.1", 8080),
            },
            "hub": {
                "name": "Compute Hub",
                "port": 4792,
                "path": "/hub/",
                "up": _check_port_open("127.0.0.1", 4792),
            },
        }
        laptop = not _on_skaha()
        for key, svc in services.items():
            if laptop and key != "agent":
                svc["session_only"] = True
        scratch_dir = os.environ.get("SCRATCH", "/scratch")
        scratch_free_gb = 0.0
        if os.path.isdir(scratch_dir):
            try:
                usage = shutil.disk_usage(scratch_dir)
                scratch_free_gb = round(usage.free / (1024**3), 1)
            except OSError:
                pass
        session_id = os.environ.get("SKAHA_SESSIONID") or os.environ.get("skaha_sessionid")  # noqa: SIM112
        prefix = f"/session/contrib/{session_id}" if session_id else None
        status_data = {
            "status": "ready" if any(s["up"] for s in services.values()) else "starting",
            "session_id": session_id,
            "prefix": prefix,
            "services": services,
            "resources": {
                "cpus": os.cpu_count() or 1,
                "scratch_free_gb": scratch_free_gb,
            },
            "laptop": laptop,
        }

    if is_json:
        ui.print_json(status_data)
        return

    session_id = str(status_data.get("session_id") or "local")
    prefix = str(status_data.get("prefix") or "")
    ui.print_ok(f"AstroAI Studio Status (Session: {session_id})")

    services_raw = status_data.get("services")
    services_map: dict[str, Any] = services_raw if isinstance(services_raw, dict) else {}

    rows: list[dict[str, str]] = []
    for s_id, s_info_raw in services_map.items():
        s_info: dict[str, Any] = s_info_raw if isinstance(s_info_raw, dict) else {}
        is_up = bool(s_info.get("up", False))
        if s_info.get("session_only") and not is_up:
            status_badge = "[dim]session only[/dim]"
        elif is_up:
            status_badge = "[bold green]ONLINE[/bold green]"
        else:
            status_badge = "[dim]STOPPED[/dim]"
        svc_path = str(s_info.get("path", ""))
        path_str = f"{prefix}{svc_path}" if prefix else svc_path
        rows.append(
            {
                "Service": str(s_info.get("name", s_id)),
                "Port": str(s_info.get("port", "")),
                "URL Path": path_str,
                "Status": status_badge,
            }
        )

    from rich.console import Console
    from rich.table import Table

    laptop = bool(status_data.get("laptop"))
    title = "AstroAI Studio (this computer)" if laptop else "Studio Services (Multiplexed on :5000)"
    table = Table(title=title, show_header=True)
    table.add_column("Service", style="bold")
    table.add_column("Internal Port")
    table.add_column("Public Path")
    table.add_column("Status")

    for r in rows:
        table.add_row(r["Service"], r["Port"], r["URL Path"], r["Status"])

    Console().print(table)

    res_raw = status_data.get("resources")
    res: dict[str, Any] = res_raw if isinstance(res_raw, dict) else {}
    scratch_free = res.get("scratch_free_gb", 0)
    cpus = res.get("cpus", 1)
    if laptop:
        ui.print_hint("Agents listen on http://127.0.0.1:3080.")
        ui.print_hint(
            "Terminal, Jupyter, Marimo, VS Code, and the hub run in the CANFAR studio image."
        )
    else:
        ui.print_hint(f"Resources: {cpus} CPU cores | Scratch free: {scratch_free} GB")


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
            help="`canfar-lab` binary the Studio MCP row runs (default: the one on PATH).",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite managed studio/dsh scaffolding."),
    ] = False,
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Do not open a browser. dsh itself never opens one."),
    ] = False,
    skills: Annotated[
        bool,
        typer.Option("--skills", help="Print skills.sh / agentskills onboarding and exit."),
    ] = False,
    dsh_version: Annotated[
        str | None,
        typer.Option(
            "--dsh-version",
            help="pin|alpha|next|latest|newest|<version> — reported everywhere, "
            "installed with --install-dsh.",
        ),
    ] = None,
    install_dsh: Annotated[
        bool,
        typer.Option("--install-dsh", help="npm-install the resolved dsh when missing/older."),
    ] = False,
) -> None:
    """Launch AstroAI Studio (dsh web) or prepare/diagnose its configuration.

    On a Skaha *non-studio* session (e.g. vscode), prefer the dedicated
    ``astroai/studio`` image — ``dsh web`` behind ``/proxy/PORT`` is broken.
    """
    if ctx.invoked_subcommand is not None:
        return

    from canfar_lab import dsh_release as _dsh
    from canfar_lab import panel as _panel
    from canfar_lab import studio as _studio
    from canfar_lab import studio_profile as _sp
    from canfar_lab.utils.subprocess import run

    opts = merge_opts(ctx)
    dsh_info = _dsh.resolve(dsh_version)

    if repo == "status":
        studio_status_cmd(ctx, json_output=opts.json)
        return

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
        dsh_now = _studio.dsh_binary()
        dsh_installed = _dsh.installed_version(dsh_now)
        report["dsh"] = {
            "spec": dsh_version or "pin",
            "release": {
                "version": dsh_info.version,
                "source": dsh_info.source,
                "note": dsh_info.note,
            },
            "binary": dsh_now,
            "installed": dsh_installed,
            "needs_install": dsh_now is None or _dsh.has_update(dsh_installed, dsh_info),
        }
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
        dsh_installed = _dsh.installed_version(dsh_bin)
        dsh_change = (dsh_version or install_dsh) and _dsh.has_update(dsh_installed, dsh_info)
        if dsh_change and install_dsh and not (opts.dry_run or opts.json):
            npm = _dsh.npm_binary()
            if npm is None:
                raise LabError(
                    "npm is needed to install dsh",
                    hint="Install Node.js/npm or drop --install-dsh.",
                )
            run([npm, *_dsh.install_command(dsh_info.version)[1:]])
            dsh_bin = _studio.dsh_binary()
            dsh_installed = _dsh.installed_version(dsh_bin)
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

    listen = studio_listen_plan(resolved_profile, port)
    dsh_port = int(listen["dsh_port"])
    if listen["desk"] and not (opts.dry_run or opts.json):
        dsh_port = _free_port(dsh_port)
        listen = {**listen, "dsh_port": dsh_port}
    cmd = _studio.studio_web_cmd(
        repo_path, port=dsh_port, profile=resolved_profile, dsh_bin=dsh_bin
    )
    studio_env = _studio.studio_env(profile=resolved_profile)
    env = {**os.environ, **studio_env}
    if opts.dry_run or opts.json:
        ui.print_json(
            {
                "repo": str(repo_path),
                "profile": resolved_profile,
                "dsh_profile": _sp.STUDIO_PROFILE_NAME,
                "listen": listen,
                "cmd": cmd,
                "env": studio_env,
                "dsh": {
                    "spec": dsh_version or "pin",
                    "release": {
                        "version": dsh_info.version,
                        "source": dsh_info.source,
                        "note": dsh_info.note,
                    },
                    "installed": dsh_installed,
                },
                "prepare": prep,
            }
        )
        return

    url = f"http://127.0.0.1:{port}"
    if prep.get("degraded"):
        ui.print_warn("Team layers are not active — see `canfar lab studio --prepare`.")
        for action in prep.get("actions") or []:
            if "UNAVAILABLE" in action or "not installed" in action:
                ui.print_hint(f"  {action}")
    if _check_port_open("127.0.0.1", port):
        if listen["desk"] and not _desk_on_port(port):
            ui.print_warn(
                f"{url} is the previous dsh, without the sync dock. "
                "Quit that terminal, then run this command again."
            )
            raise typer.Exit(1)
        ui.print_ok(f"AstroAI Studio is already running → {url}")
        if not no_open:
            _open_browser(url)
        return
    ui.print_hint(f"AstroAI Studio ({resolved_profile}) → {url}  (repo {repo_path})")
    ui.print_hint(
        f"  harness profile: {_sp.STUDIO_PROFILE_NAME} — state in {prep.get('state_root')}"
    )
    if listen["desk"]:
        ui.print_hint(
            "  Desk starts collapsed — click Desk (top right) for Sync / Launch, "
            "or set CANFAR_LAB_DESK=1 to open the bar."
        )
    ui.print_hint("  Team review: New session → preset «AstroAI Studio Team»")
    if with_team:
        ui.print_hint("  Agent Teams: ask for a team in the chat (roster + shared task board)")
    ui.print_hint(f"  {_studio.skills_onboarding_hint().splitlines()[1].strip()}")
    if listen["desk"]:
        ui.print_ok(f"AstroAI Studio → {url}")
        code = serve_mod.serve_with_dock(
            cmd=cmd,
            cwd=repo_path,
            env=env,
            public_port=port,
            backend_port=dsh_port,
            root=repo_path,
            settings=get_settings(),
            dsh_bin=dsh_bin,
            dsh_spec=dsh_version or "pin",
            release=dsh_info,
            opener=None if no_open else _open_browser,
        )
        if code:
            raise typer.Exit(code)
        return
    if not no_open:
        _open_browser(url)
    run(cmd, cwd=repo_path, env=env)
