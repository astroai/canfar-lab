import contextlib
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

import typer

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.core.paths import resolve_paths
from canfar_lab.core.session_resources import collect_resources
from canfar_lab.core.storage import (
    arc_project_dict,
    arc_project_statuses,
    collect_status_quotas,
    cwd_arc_project,
    home_breakdown,
    top_cpu_processes,
)
from canfar_lab.core.vospace_status import vault_status_dict
from canfar_lab.errors import LabError
from canfar_lab.utils.subprocess import run_capture
from canfar_lab.utils.timing import PhaseTimer
from canfar_lab.version import version_info

# Keep `status` responsive at session start even if the canfar CLI is slow
# or its auth server stalls; a timeout degrades to "Not authenticated".
CANFAR_CMD_TIMEOUT_SEC = 5.0


def _status_timer(verbose: bool) -> PhaseTimer:
    if not verbose:
        return PhaseTimer()

    def _emit(name: str, dt: float, total: float) -> None:
        print(f"status: {name} {dt:.2f}s (total {total:.2f}s)", file=sys.stderr, flush=True)

    return PhaseTimer(_emit)


def _canfar_snapshot() -> tuple[str | None, list[str] | None]:
    if shutil.which("canfar") is None:
        return None, None
    auth: str | None = "Not authenticated"
    sessions: list[str] | None = None

    def _auth() -> str:
        return run_capture(["canfar", "auth", "show"], timeout=CANFAR_CMD_TIMEOUT_SEC)

    def _ps() -> list[str]:
        return run_capture(["canfar", "ps"], timeout=CANFAR_CMD_TIMEOUT_SEC).splitlines()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="lab-canfar") as pool:
        f_auth = pool.submit(_auth)
        f_ps = pool.submit(_ps)
        try:
            auth = f_auth.result(timeout=CANFAR_CMD_TIMEOUT_SEC + 0.5)
        except (LabError, TimeoutError):
            auth = "Not authenticated"
        with contextlib.suppress(LabError, TimeoutError):
            sessions = f_ps.result(timeout=CANFAR_CMD_TIMEOUT_SEC + 0.5)
    return auth, sessions


def register(app: typer.Typer) -> None:
    @app.command()
    def status(
        ctx: typer.Context,
        json_output: Annotated[
            bool, typer.Option("--json", help="Machine-readable output.")
        ] = False,
        verbose: Annotated[
            bool,
            typer.Option(
                "--verbose",
                "-v",
                help="Print how long each check took.",
            ),
        ] = False,
        show_all: Annotated[
            bool,
            typer.Option(
                "--all",
                help="Also show groups, every team project, and all disk quotas.",
            ),
        ] = False,
    ) -> None:
        """Show CPU, memory, home space, and your sessions.

        Default view is this session and your home disk. `--all` adds groups
        and every team project. `--json` is always complete.

        Examples:
            canfar lab status
            canfar lab status --all
            canfar lab status --json
            canfar lab status -v
            canfar lab --json status
        """
        opts = merge_opts(ctx, json_output=json_output)
        timer = _status_timer(verbose and not opts.quiet)
        paths = resolve_paths()
        full = show_all or opts.json
        if full:
            active_project, arc_projects, gms, vault = arc_project_statuses(timer=timer)
        else:
            with timer.phase("team project"):
                active_project = cwd_arc_project()
            arc_projects = [active_project] if active_project is not None else []
            gms, vault = None, None
        with timer.phase("quotas"):
            quotas = collect_status_quotas(
                home=paths.home,
                scratch=paths.scratch_dir,
                projects=arc_projects,
            )
        seen_quota_labels = {q.label for q in quotas}
        arc_names = {p.name.casefold() for p in arc_projects}
        for proj in arc_projects:
            proj_vault = proj.vault
            if (
                proj_vault is not None
                and proj_vault.found
                and (q := proj_vault.quota_line(current=proj.is_cwd))
                and q.label not in seen_quota_labels
            ):
                quotas.append(q)
                seen_quota_labels.add(q.label)
        if vault is not None:
            for node in vault.nodes:
                if not node.found or node.name.casefold() in arc_names:
                    continue
                if (q := node.quota_line()) and q.label not in seen_quota_labels:
                    quotas.append(q)
                    seen_quota_labels.add(q.label)
        with timer.phase("home"):
            home_rows = home_breakdown(paths.home)
        with timer.phase("processes"):
            procs = top_cpu_processes()
        with timer.phase("resources"):
            resources = collect_resources()

        with timer.phase("canfar"):
            canfar_auth, canfar_sessions = _canfar_snapshot()

        if opts.json:
            ui.print_json(
                {
                    "version": version_info(),
                    "quotas": [q.__dict__ for q in quotas],
                    "home": home_rows,
                    "resources": resources.to_dict(),
                    "arc_project": (arc_project_dict(active_project) if active_project else None),
                    "arc_projects": [arc_project_dict(p) for p in arc_projects],
                    "gms_groups": ({"groups": gms.groups, "source": gms.source} if gms else None),
                    "vault": vault_status_dict(vault),
                    "processes": procs,
                    "canfar_auth": canfar_auth,
                    "canfar_sessions": canfar_sessions,
                }
            )
        else:
            ui.status_human(
                quotas,
                home_rows,
                active_project,
                arc_projects,
                procs,
                canfar_auth=canfar_auth,
                canfar_sessions=canfar_sessions,
                gms_groups=gms,
                vault=vault,
                resources=resources.to_dict(),
                full=show_all,
            )
