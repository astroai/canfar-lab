from __future__ import annotations

from pathlib import Path

from astroai_lab.core.git import git_status
from astroai_lab.core.paths import quota_used_pct, resolve_paths
from astroai_lab.core.project import detect_project, list_saves
from astroai_lab.core.storage import cwd_arc_project
from astroai_lab.version import display_version, version_info


def show_banner(*, json_output: bool = False) -> None:
    from astroai_lab import ui

    paths = resolve_paths()
    git = git_status()
    saves = list_saves(paths.save_dir)
    cwd = Path.cwd()
    project_kind = detect_project(cwd)
    home_pct = quota_used_pct(paths.home)
    active_arc = cwd_arc_project(cwd)

    if json_output:
        ui.print_json(
            {
                "version": version_info(),
                "work_dir": str(paths.work_dir),
                "scratch_dir": str(paths.scratch_dir) if paths.scratch_dir else None,
                "save_dir": str(paths.save_dir),
                "saves_count": len(saves),
                "git_dirty": git.uncommitted if git.in_repo else None,
                "project": project_kind.value if project_kind else None,
                "home_quota_pct": home_pct,
                "arc_project": (
                    {
                        "name": active_arc.name,
                        "path": str(active_arc.path),
                        "access": active_arc.access,
                        "quota_pct": active_arc.quota.pct if active_arc.quota else None,
                        "quota_free": active_arc.quota.free if active_arc.quota else None,
                    }
                    if active_arc
                    else None
                ),
            }
        )
        return

    ui.print_info(f"[bold]astroai {display_version()}[/bold] — AstroAI session workbench")
    ui.print_hint(f"  work:    {paths.work_dir}  (code / notebooks — ephemeral)")
    ui.print_hint(
        f"  scratch: {paths.scratch_dir or '(not mounted)'}  (fast I/O + caches — ephemeral)"
    )
    ui.print_hint(f"  home:    {paths.home}  (tiny durable: auth, MCP, env saves)")
    ui.print_hint(f"  saves:   {len(saves)} in {paths.save_dir}")
    if active_arc is not None:
        q = active_arc.quota
        if q is not None:
            ui.print_hint(
                f"  team:    {active_arc.path} [{active_arc.access}] "
                f"({q.free} free of {q.total}, {q.pct}% used)"
            )
        else:
            ui.print_hint(f"  team:    {active_arc.path} [{active_arc.access}]")
    if home_pct is not None and home_pct >= 80:
        ui.print_warn(f"  home quota: {home_pct}% — see `astroai status` for details")
    if git.in_repo and git.uncommitted:
        ui.print_warn("  uncommitted changes — `git add -A && git commit -m 'session work'`")
    if project_kind:
        ui.print_hint(f"  project: {project_kind.value} in {cwd.name}")
        ui.print_hint("  next: `astroai save` before closing")
    else:
        ui.print_hint("  notebook path: `astroai kernel ensure` then open starter.ipynb")
        ui.print_hint("  project path:  `astroai init mylab`  ·  `astroai clone owner/repo`")
    ui.print_hint("  cluster: `astroai cluster start`  ·  `astroai run train.py --cpus 2`")
    ui.print_hint("  help: `astroai help`  ·  overview: `astroai status`")
