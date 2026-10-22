"""Repository and workspace synchronization for CANFAR and local development."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from canfar_lab import ui
from canfar_lab.cli.context import merge_opts
from canfar_lab.config.settings import get_settings
from canfar_lab.sync import bus as bus_mod
from canfar_lab.sync.catalog import (
    DEFAULT_REPOSITORIES,
    DEFAULT_SCIENCE_REPOSITORIES,  # noqa: F401 — re-exported for back-compat/tests
    detect_github_user,
    load_catalog,
)
from canfar_lab.sync.engine import SyncEngine
from canfar_lab.sync.watch import DEFAULT_INTERVAL_SECONDS, watch

console = Console()
err_console = Console(stderr=True)

sync_app = typer.Typer(
    name="sync",
    help="Synchronize science repositories between laptop and CANFAR sessions.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def resolve_workspace_root(explicit: Path | None = None) -> Path:
    """Resolve the source workspace root: $WORK, /scratch/src, or ~/src."""
    if explicit is not None:
        return explicit.resolve()
    if "WORK" in os.environ:
        return Path(os.environ["WORK"]).resolve()
    if Path("/scratch/src").is_dir():
        return Path("/scratch/src").resolve()

    # Walk up from cwd to detect workspace root containing workspace.toml or astroai/
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        has_catalog = (parent / "workspace.toml").is_file()
        has_orgs = (parent / "astroai").is_dir() and (parent / "opencadc").is_dir()
        if has_catalog or has_orgs:
            return parent
    return (Path.home() / "src").resolve()


@dataclass
class RepoSyncStatus:
    name: str
    path: Path
    exists: bool
    branch: str = ""
    dirty: bool = False
    ahead_origin: int = 0
    behind_origin: int = 0
    ahead_upstream: int = 0
    behind_upstream: int = 0
    upstream_configured: bool = False
    error: str | None = None


def _git_run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def inspect_repo(repo_slug: str, root: Path) -> RepoSyncStatus:
    org, name = repo_slug.split("/", 1)
    if (root / org / name).exists():
        target = root / org / name
    elif (root / name).exists() and root.name == org:
        target = root / name
    elif (root / org).is_dir():
        target = root / org / name
    else:
        target = root / org / name

    if not target.is_dir() or not (target / ".git").exists():
        return RepoSyncStatus(name=repo_slug, path=target, exists=False)

    status = RepoSyncStatus(name=repo_slug, path=target, exists=True)

    # Branch
    b_res = _git_run(["branch", "--show-current"], target)
    status.branch = b_res.stdout.strip() or "DETACHED"

    # Dirty
    diff_res = _git_run(["status", "--porcelain"], target)
    status.dirty = bool(diff_res.stdout.strip())

    # Remotes
    remotes_res = _git_run(["remote"], target)
    remotes = set(remotes_res.stdout.split())
    status.upstream_configured = "upstream" in remotes

    # Origin ahead/behind
    if "origin" in remotes and status.branch != "DETACHED":
        ab_res = _git_run(
            ["rev-list", "--left-right", "--count", f"origin/{status.branch}...{status.branch}"],
            target,
        )
        if ab_res.returncode == 0:
            parts = ab_res.stdout.strip().split()
            if len(parts) == 2:
                status.behind_origin = int(parts[0])
                status.ahead_origin = int(parts[1])

    # Upstream ahead/behind (comparing against upstream/main)
    if "upstream" in remotes:
        up_res = _git_run(["rev-list", "--left-right", "--count", "upstream/main...HEAD"], target)
        if up_res.returncode == 0:
            parts = up_res.stdout.strip().split()
            if len(parts) == 2:
                status.behind_upstream = int(parts[0])
                status.ahead_upstream = int(parts[1])

    return status


@sync_app.command("status")
def cmd_status(
    root: Annotated[
        Path | None,
        typer.Option(
            "--root", "-r", help="Workspace root (default: $WORK, /scratch/src, or ~/src)."
        ),
    ] = None,
    all_repos: Annotated[bool, typer.Option("--all", help="Scan all discovered repos.")] = False,
) -> None:
    """Show sync status for core science repositories."""
    ws_root = resolve_workspace_root(root)
    console.print(f"[bold]Workspace Root:[/bold] {ws_root}")
    gh_user = detect_github_user()
    if gh_user:
        console.print(f"[bold]Active User / Fork Owner:[/bold] {gh_user}")

    table = Table(title="Repository Synchronization Status")
    table.add_column("Repository", style="cyan")
    table.add_column("Branch", style="magenta")
    table.add_column("Dirty?", justify="center")
    table.add_column("vs Origin (Forks)", justify="center")
    table.add_column("vs Upstream (Canon)", justify="center")
    table.add_column("Status", style="green")

    repo_list = DEFAULT_REPOSITORIES
    for repo_slug in repo_list:
        st = inspect_repo(repo_slug, ws_root)
        if not st.exists:
            table.add_row(repo_slug, "-", "-", "-", "-", "[dim]Not Cloned[/dim]")
            continue

        dirty_str = "[red]Dirty[/red]" if st.dirty else "[green]Clean[/green]"
        origin_str = f"+{st.ahead_origin} / -{st.behind_origin}"
        upstream_str = (
            f"+{st.ahead_upstream} / -{st.behind_upstream}"
            if st.upstream_configured
            else "[dim]No Upstream[/dim]"
        )

        state = "In Sync"
        if st.ahead_origin > 0:
            state = "[yellow]Unpushed[/yellow]"
        if st.behind_upstream > 0:
            state = "[cyan]Update Available[/cyan]"
        if st.dirty:
            state = "[red]Uncommitted[/red]"

        table.add_row(st.name, st.branch, dirty_str, origin_str, upstream_str, state)

    console.print(table)


@sync_app.command("dirty")
def cmd_dirty(
    root: Annotated[Path | None, typer.Option("--root", "-r")] = None,
) -> None:
    """Exit with code 1 if any repository has uncommitted or unpushed changes."""
    ws_root = resolve_workspace_root(root)
    dirty_repos: list[str] = []

    for repo_slug in DEFAULT_REPOSITORIES:
        st = inspect_repo(repo_slug, ws_root)
        if st.exists and (st.dirty or st.ahead_origin > 0):
            reason = []
            if st.dirty:
                reason.append("uncommitted changes")
            if st.ahead_origin > 0:
                reason.append(f"{st.ahead_origin} commits unpushed to origin")
            dirty_repos.append(f"{st.name}: {', '.join(reason)}")

    if dirty_repos:
        err_console.print("[bold red]Unsaved work detected:[/bold red]")
        for item in dirty_repos:
            err_console.print(f"  - {item}")
        raise typer.Exit(1)

    console.print("[green]All repositories clean and in sync with origin.[/green]")


@sync_app.command("push")
def cmd_push(
    root: Annotated[Path | None, typer.Option("--root", "-r")] = None,
) -> None:
    """Push committed changes on current branch to origin for all repositories."""
    ws_root = resolve_workspace_root(root)
    for repo_slug in DEFAULT_REPOSITORIES:
        st = inspect_repo(repo_slug, ws_root)
        if not st.exists or st.ahead_origin == 0:
            continue
        console.print(f"[bold cyan]Pushing {st.name} ({st.branch}) to origin...[/bold cyan]")
        res = _git_run(["push", "-u", "origin", st.branch], st.path)
        if res.returncode != 0:
            err_console.print(f"[red]Failed to push {st.name}:[/red] {res.stderr}")
        else:
            console.print(f"[green]Pushed {st.name}.[/green]")


@sync_app.command("bootstrap")
def cmd_bootstrap(
    root: Annotated[Path | None, typer.Option("--root", "-r")] = None,
    user: Annotated[
        str | None, typer.Option("--user", "-u", help="GitHub user for fork remotes.")
    ] = None,
) -> None:
    """Clone missing repositories and configure fork origin and canonical upstream."""
    ws_root = resolve_workspace_root(root)
    gh_user = user or detect_github_user()

    for repo_slug in DEFAULT_REPOSITORIES:
        org, name = repo_slug.split("/", 1)
        dest = ws_root / org / name
        if dest.exists():
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        canon_url = f"git@github.com:{repo_slug}.git"
        origin_url = f"git@github.com:{gh_user}/{name}.git" if gh_user else canon_url

        console.print(f"[cyan]Cloning {repo_slug} -> {dest}...[/cyan]")
        clone_res = subprocess.run(["git", "clone", origin_url, str(dest)], check=False)
        if clone_res.returncode != 0:
            # Fall back to canonical if user fork doesn't exist
            console.print(
                f"[yellow]Fork {origin_url} not found; cloning canonical {canon_url}...[/yellow]"
            )
            subprocess.run(["git", "clone", canon_url, str(dest)], check=True)

        if gh_user and origin_url != canon_url:
            _git_run(["remote", "add", "upstream", canon_url], dest)
            _git_run(["fetch", "upstream"], dest)
            _git_run(["branch", "--set-upstream-to=upstream/main", "main"], dest)

        console.print(f"[green]Configured {repo_slug}[/green]")


@sync_app.callback(invoke_without_command=True)
def default_sync(
    ctx: typer.Context,
    root: Annotated[Path | None, typer.Option("--root", "-r")] = None,
) -> None:
    """Default: Fetch and sync all repositories against upstream and origin."""
    if ctx.invoked_subcommand is not None:
        return

    ws_root = resolve_workspace_root(root)
    console.print(f"[bold]Syncing repositories in:[/bold] {ws_root}")

    seen = 0
    skipped_dirty: list[str] = []
    for repo_slug in DEFAULT_REPOSITORIES:
        st = inspect_repo(repo_slug, ws_root)
        if not st.exists:
            continue
        seen += 1

        console.print(f"… fetching {st.name}")
        _git_run(["fetch", "--all", "--prune"], st.path)

        # Fast-forward main if clean
        if st.branch == "main" and not st.dirty:
            target_remote = "upstream" if st.upstream_configured else "origin"
            ff_res = _git_run(["merge", "--ff-only", f"{target_remote}/main"], st.path)
            if ff_res.returncode == 0:
                console.print(f"  [green]Fast-forwarded {st.name} to {target_remote}/main[/green]")
            else:
                console.print(
                    f"  [yellow]Cannot fast-forward {st.name}; divergence detected[/yellow]"
                )
        elif st.dirty:
            skipped_dirty.append(st.name)
            console.print(f"  [dim]Skipped merge for dirty repo {st.name}[/dim]")
    if seen == 0:
        console.print("[yellow]No clones under this root.[/yellow] Next: canfar lab sync bootstrap")
    elif skipped_dirty:
        console.print(
            "Dirty repos were left as-is. Next on this computer: canfar lab sync push. "
            "Next in the session: canfar lab sync"
        )


# --- Desk ↔ session exchange (docs/desk-sync.md §6) ---------------------------


def _on_session() -> bool:
    names = {key.upper() for key in os.environ}
    return "SKAHA_SESSIONID" in names or "SKAHA_HOSTNAME" in names


def _mailbox_path(settings: Any, override: Path | None) -> Path:
    return bus_mod.mailbox_dir(override=override if override is not None else settings.sync_mailbox)


def _mailbox_shared(settings: Any, override: Path | None) -> bool:
    return bool(
        override
        or settings.sync_mailbox
        or os.environ.get(bus_mod.MAILBOX_ENV, "").strip()
        or _on_session()
    )


@sync_app.command("exchange")
def cmd_exchange(
    ctx: typer.Context,
    to: Annotated[
        str,
        typer.Option(
            "--to",
            help="Direction: `canfar` (desk → session) or `desktop` (session → desk).",
        ),
    ],
    root: Annotated[Path | None, typer.Option("--root", "-r")] = None,
    repos: Annotated[
        list[str] | None,
        typer.Option("--repos", help="Limit to repos (short names ok; repeatable)."),
    ] = None,
    repos_file: Annotated[
        Path | None, typer.Option("--repos-file", help="workspace.toml-style catalog.")
    ] = None,
    mailbox: Annotated[
        Path | None, typer.Option("--mailbox", help="Sync mailbox override.")
    ] = None,
    mode: Annotated[
        str | None, typer.Option("--publish-mode", help="commit|report (default from config).")
    ] = None,
    message: Annotated[str | None, typer.Option("--message", help="Auto-commit message.")] = None,
    bootstrap: Annotated[
        bool,
        typer.Option("--bootstrap/--no-bootstrap", help="Let the session clone missing repos."),
    ] = True,
) -> None:
    """Publish local commits, then ring the other side through the shared mailbox.

    `--to canfar` (desk): commit + push, then drop a request the session
    watcher answers with bootstrap + fast-forward. `--to desktop` (session):
    commit + push, then drop the event the desk absorbs (rebase).
    """
    if to not in ("canfar", "desktop"):
        raise typer.BadParameter("--to must be `canfar` or `desktop`")
    opts = merge_opts(ctx)
    settings = get_settings()
    ws_root = resolve_workspace_root(root)
    catalog = load_catalog(ws_root, repos_file=repos_file)
    side = "desk" if to == "canfar" else "session"
    engine = SyncEngine(ws_root, catalog, side=side, exclude=settings.sync_exclude)
    report = engine.publish(
        repos=repos,
        mode=mode or settings.sync_publish_mode,
        message=message,
        dry_run=opts.dry_run,
    )
    box = _mailbox_path(settings, mailbox)
    result: dict[str, Any] = {
        "to": to,
        "root": str(ws_root),
        "mailbox": str(box),
        "shared_mailbox": _mailbox_shared(settings, mailbox),
        "published": report.to_dict(),
    }
    if not opts.dry_run:
        bus_mod.ensure(box)
        payload_repos = [
            {"slug": rec.slug, "head": rec.head} for rec in report.repos if rec.status != "missing"
        ]
        if to == "canfar":
            ready = bus_mod.read_ready(mailbox=box)
            request = bus_mod.request_payload(
                side=bus_mod.DESK, root=ws_root, repos=payload_repos, bootstrap=bootstrap
            )
            path = bus_mod.enqueue("requests", request, mailbox=box)
            result.update({"request": path.name, "session_ready": bool(ready)})
        else:
            event = bus_mod.event_payload(
                side=bus_mod.SESSION, root=ws_root, report=report.to_dict()
            )
            path = bus_mod.enqueue("events", event, mailbox=box)
            result.update({"event": path.name})

    if opts.json:
        ui.print_json(result)
        return

    for rec in report.repos:
        console.print(f"  {rec.slug:<28} {rec.status:<14} {rec.detail}")
    if opts.dry_run:
        ui.print_hint("dry-run: nothing pushed, no mailbox message sent")
        return
    if to == "canfar":
        if result.get("session_ready"):
            ui.print_ok(f"Request {result['request']} queued; the session watcher will pull.")
        elif result.get("shared_mailbox"):
            ui.print_warn(
                f"No session watcher seen; request {result['request']} keeps 30 min and "
                "is picked up at session boot."
            )
        else:
            ui.print_warn(
                "Mailbox is laptop-local: the session cannot read it. The push is on "
                "GitHub, so `canfar lab sync` inside the session still pulls it; set "
                "sync_mailbox (SSHFS of arc:/home/$USER) for the doorbell."
            )
    else:
        ui.print_ok(f"Event {result['event']} queued; the desk absorbs it on its next pass.")


@sync_app.command("watch")
def cmd_watch(
    ctx: typer.Context,
    session: Annotated[
        bool, typer.Option("--session", help="Session side: requests → pull.")
    ] = False,
    desk: Annotated[bool, typer.Option("--desk", help="Desk side: events → absorb.")] = False,
    root: Annotated[Path | None, typer.Option("--root", "-r")] = None,
    mailbox: Annotated[
        Path | None, typer.Option("--mailbox", help="Sync mailbox override.")
    ] = None,
    repos_file: Annotated[Path | None, typer.Option("--repos-file")] = None,
    interval: Annotated[
        float, typer.Option("--interval", help="Seconds between passes.")
    ] = DEFAULT_INTERVAL_SECONDS,
    once: Annotated[bool, typer.Option("--once", help="Single pass, then exit.")] = False,
) -> None:
    """Run the mailbox watcher loop (no inotify on /arc: polling by design)."""
    if session and desk:
        raise typer.BadParameter("--session and --desk are mutually exclusive")
    opts = merge_opts(ctx)
    settings = get_settings()
    side = "session" if session else "desk" if desk else ("session" if _on_session() else "desk")
    ws_root = resolve_workspace_root(root)
    catalog = load_catalog(ws_root, repos_file=repos_file)
    engine = SyncEngine(ws_root, catalog, side=side, exclude=settings.sync_exclude)
    box = _mailbox_path(settings, mailbox)

    def say(line: str) -> None:
        if not opts.json:
            console.print(f"[dim]{line}[/dim]")

    if not opts.json:
        console.print(f"[bold]sync watch ({side})[/bold]  root={ws_root}  mailbox={box}")
    processed = watch(
        side=side, engine=engine, mailbox=box, root=ws_root, interval=interval, once=once, say=say
    )
    if opts.json:
        ui.print_json({"side": side, "processed": processed, "mailbox": str(box)})
    elif once:
        ui.print_ok(f"{processed} item(s) processed")
