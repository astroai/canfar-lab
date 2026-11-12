"""``canfar lab desk``: local browser dsh with the Desk dock.

The cockpit is the *latest* dsh (resolved through :mod:`canfar_lab.dsh_release`,
default ``newest`` across npm dist-tags) served on loopback behind a small
stdlib proxy. The floating desk is off until ``CANFAR_LAB_DESK=1``; then it
shows Sync to CANFAR and Launch Studio. See ``docs/desk-sync.md`` §7.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer

from canfar_lab import dsh_release, ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.config.settings import get_settings
from canfar_lab.desk import apps as apps_mod
from canfar_lab.desk import serve as serve_mod
from canfar_lab.desk import state as state_mod
from canfar_lab.errors import LabError
from canfar_lab.studio import (
    DEFAULT_PORT,
    dsh_binary,
    prepare_studio,
    resolve_repo,
    studio_env,
    studio_web_cmd,
)
from canfar_lab.sync import bus as bus_mod
from canfar_lab.utils.subprocess import run_cmd

desk_app = typer.Typer(
    name="desk",
    help=(
        "Local browser dsh. The desk bar starts collapsed; click Desk for Sync "
        "to CANFAR and Launch Studio, or set CANFAR_LAB_DESK=1 to open it.\n\n"
        "Examples:\n"
        "  canfar lab desk\n"
        "  canfar lab desk ~/src --dsh-version newest\n"
        "  canfar lab desk --doctor"
    ),
    rich_markup_mode="rich",
    invoke_without_command=True,
)


def _install_dsh(version: str) -> None:
    npm = dsh_release.npm_binary()
    if npm is None:
        raise LabError(
            "npm is needed to install dsh",
            hint="Install Node.js/npm, or point --dsh-bin at an existing dsh.",
        )
    cmd = dsh_release.install_command(version)
    ui.print_info(f"Installing dsh {version}: {' '.join(cmd)}")
    run_cmd([npm, *cmd[1:]])


@desk_app.callback(invoke_without_command=True)
def desk_main(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Workspace directory (default: cwd).")] = None,
    port: Annotated[
        int | None, typer.Option("--port", help="Cockpit port (default: desk_port).")
    ] = None,
    dsh_port: Annotated[int, typer.Option("--dsh-port", help="Local dsh port.")] = DEFAULT_PORT,
    dsh_version: Annotated[
        str | None,
        typer.Option(
            "--dsh-version",
            help="pin|alpha|next|latest|newest|<version> (default from config).",
        ),
    ] = None,
    no_install: Annotated[
        bool, typer.Option("--no-install-dsh", help="Never npm-install dsh.")
    ] = False,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open the browser.")] = False,
    doctor: Annotated[bool, typer.Option("--doctor", help="Pre-flight report, then exit.")] = False,
) -> None:
    """Serve the latest dsh locally with the Desk dock wired to CANFAR."""
    opts = merge_opts(ctx)
    settings = get_settings()
    ws_root = resolve_repo(repo)
    cockpit_port = port or settings.desk_port
    spec = dsh_version or settings.dsh_version
    release = dsh_release.resolve(spec)
    box = bus_mod.mailbox_dir(override=settings.sync_mailbox)
    binary = dsh_binary()
    installed = dsh_release.installed_version(binary)
    needs_install = binary is None or dsh_release.has_update(installed, release)

    if doctor:
        checks: list[dict[str, str]] = []

        def check(name: str, ok: bool, detail: str, *, fatal: bool = False) -> None:
            checks.append(
                {"name": name, "ok": "yes" if ok else "no", "detail": detail, "fatal": str(fatal)}
            )

        check("workspace", ws_root.is_dir(), str(ws_root))
        check(
            "dsh",
            binary is not None,
            f"{binary or 'missing'} (installed {installed or 'unknown'})",
            fatal=True,
        )
        check(
            "dsh version",
            not needs_install,
            f"spec {spec} → {release.version} ({release.source})"
            + (" — install on first run" if needs_install else ""),
        )
        check("npm", dsh_release.npm_binary() is not None, dsh_release.npm_binary() or "missing")
        apps_state = apps_mod.available(settings.apps_editor, settings.apps_terminal)
        check("editor", apps_state["editor"], settings.apps_editor)
        check("terminal", apps_state["terminal"], settings.apps_terminal)
        check("mailbox", box.is_dir() or box.parent.is_dir(), str(box))
        auth = state_mod.auth_state()
        check(
            "auth",
            bool(auth.get("authenticated")),
            auth.get("message") or f"server={auth.get('server')} idp={auth.get('idp')}",
        )
        report = {
            "ok": all(c["ok"] == "yes" for c in checks if c["fatal"] == "True"),
            "root": str(ws_root),
            "dsh": {
                "spec": spec,
                "release": {
                    "version": release.version,
                    "source": release.source,
                    "note": release.note,
                },
                "installed": installed,
                "needs_install": needs_install,
            },
            "checks": checks,
            "skills": "npx skills add astroai/canfar-skills",
        }
        if opts.json:
            ui.print_json(report)
        else:
            for c in checks:
                mark = "[green]ok[/green]" if c["ok"] == "yes" else "[yellow]--[/yellow]"
                console_markup = f"  {mark} {c['name']:<14} {c['detail']}"
                ui.print_markup(console_markup)
            if report["ok"]:
                ui.print_ok("Desk pre-flight passed")
            else:
                ui.print_warn("Desk pre-flight found blockers")
                raise typer.Exit(1)
        return

    prep = prepare_studio(
        profile="laptop",
        dry_run=opts.dry_run or bool(opts.json),
        install_bundles=True,
        mcp_bin=None,
    )
    plan = {
        "root": str(ws_root),
        "profile": "laptop",
        "desk_port": cockpit_port,
        "dsh_port": dsh_port,
        "dsh": {
            "spec": spec,
            "release": {
                "version": release.version,
                "source": release.source,
                "note": release.note,
            },
            "installed": installed,
            "install": needs_install,
        },
        "mailbox": str(box),
        "prepare": prep,
    }
    if opts.dry_run or opts.json:
        ui.print_json(plan)
        return

    if needs_install and not no_install:
        _install_dsh(release.version)
        binary = dsh_binary()
        installed = dsh_release.installed_version(binary)
    if binary is None:
        raise LabError(
            "dsh is not installed and was not installed (--no-install-dsh?)",
            hint="npm install -g @deepseek-ai/dsh@<version>, or drop --no-install-dsh",
        )

    cmd = studio_web_cmd(ws_root, port=dsh_port, profile="laptop", dsh_bin=binary)
    env = {**os.environ, **studio_env(profile="laptop")}
    url = f"http://127.0.0.1:{cockpit_port}/"
    ui.print_info(
        f"dsh {installed or release.version} → http://127.0.0.1:{dsh_port}  (root {ws_root})"
    )
    ui.print_ok(f"Desk cockpit → {url}")
    code = serve_mod.serve_with_dock(
        cmd=cmd,
        cwd=ws_root,
        env=env,
        public_port=cockpit_port,
        backend_port=dsh_port,
        root=ws_root,
        settings=settings,
        dsh_bin=binary,
        dsh_spec=spec,
        release=release,
        opener=None if no_open else apps_mod.open_browser,
    )
    if code:
        raise typer.Exit(code)


__all__ = ["desk_app"]
