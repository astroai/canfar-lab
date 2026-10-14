"""Repository and workspace synchronization for CANFAR and local development."""

from __future__ import annotations

import contextlib
import getpass
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

sync_app = typer.Typer(
    name="sync",
    help="Synchronize science repositories between laptop and CANFAR sessions.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

DEFAULT_SCIENCE_REPOSITORIES: list[str] = [
    "astroai/cfhtcast",
    "astroai/cosmodist",
    "astroai/uspm",
    "astroai/torchz",
    "astroai/torchsky",
    "astroai/torchregress",
    "astroai/torchfits",
    "astroai/xmatcher",
    "astroai/zensus",
    "astroai/weightmask",
]

DEFAULT_REPOSITORIES: list[str] = DEFAULT_SCIENCE_REPOSITORIES + ["astroai/canfar-lab"]


def detect_github_user() -> str | None:
    """Detect current GitHub username dynamically without hardcoding."""
    # 1. Environment variable override
    for var in ("GH_USER", "GITHUB_USER"):
        val = os.environ.get(var, "").strip()
        if val:
            return val

    # 2. gh api
    if shutil.which("gh"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            res = subprocess.run(
                ["gh", "api", "user", "-q", ".login"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()

    # 3. git config
    for key in ("github.user", "user.username"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            res = subprocess.run(
                ["git", "config", key],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()

    with contextlib.suppress(Exception):
        return getpass.getuser()
    return None


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

    for repo_slug in DEFAULT_REPOSITORIES:
        st = inspect_repo(repo_slug, ws_root)
        if not st.exists:
            continue

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
            console.print(f"  [dim]Skipped merge for dirty repo {st.name}[/dim]")
