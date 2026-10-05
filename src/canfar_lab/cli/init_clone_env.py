from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from canfar_lab import ui
from canfar_lab.cli.context import get_opts, merge_opts
from canfar_lab.config.settings import get_settings
from canfar_lab.core.git import git_init_and_commit
from canfar_lab.core.paths import resolve_paths
from canfar_lab.core.project import format_dir_size, require_project, save_env, save_rows
from canfar_lab.core.session_common import ensure_writable_dir
from canfar_lab.errors import LabError
from canfar_lab.utils.subprocess import run_capture

_DEFAULT_CLONE_ORG = "astroai"


def _gh_login() -> str | None:
    try:
        login = run_capture(["gh", "api", "user", "-q", ".login"])
    except LabError:
        return None
    return login or None


def _gh_repo_exists(spec: str) -> bool:
    result = subprocess.run(
        ["gh", "repo", "view", spec, "--json", "name"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def resolve_clone_spec(spec: str) -> str:
    """Pass through owner/name. A bare name tries $gh-user then astroai."""
    if "/" in spec:
        return spec
    owners: list[str] = []
    login = _gh_login()
    if login:
        owners.append(login)
    if _DEFAULT_CLONE_ORG not in owners:
        owners.append(_DEFAULT_CLONE_ORG)
    tried = [f"{owner}/{spec}" for owner in owners]
    for candidate in tried:
        if _gh_repo_exists(candidate):
            return candidate
    raise LabError(
        f"No GitHub repo {spec!r} under {' or '.join(tried)}",
        hint="Pass owner/name, or `gh auth login` so your user is tried first.",
    )


def _print_save_list(*, json_output: bool, root: Path) -> None:
    rows = save_rows(root)
    if json_output:
        ui.print_json(rows)
    else:
        ui.env_list_table(rows)


def _src_dir(path: Path) -> Path:
    """Expand ``~``, mkdir, and refuse a non-writable source directory."""
    path = path.expanduser()
    if not ensure_writable_dir(path):
        raise LabError(
            f"Source directory is not writable: {path}",
            hint="On CANFAR use $SCRATCH/src ($WORK). Else $HOME/src or /arc/projects/<group>.",
        )
    return path.resolve()


def _init_impl(
    ctx: typer.Context,
    name: str,
    uv_project: bool,
    no_git: bool,
    no_gh: bool,
    src_dir: Path | None,
    with_dsh: bool = False,
) -> None:
    from canfar_lab.core.project import init_project

    opts = get_opts(ctx)
    settings = get_settings()
    if not uv_project and settings.default_pm == "uv":
        uv_project = True
    paths = resolve_paths()
    try:
        parent = _src_dir(src_dir) if src_dir is not None else paths.work_dir
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    target = parent / name
    if target.exists() and any(target.iterdir()):
        ui.print_error(f"Directory exists and is not empty: {target}")
        raise typer.Exit(1)
    try:
        with ui.progress_task("Initializing project...", quiet=opts.quiet):
            kind = init_project(target, use_uv=uv_project)
            if not no_git:
                git_init_and_commit(target)
    except LabError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1) from exc
    ui.print_ok(f"Project ready: {target}")
    if with_dsh:
        from canfar_lab.panel import scaffold_repo_dsh

        for path in scaffold_repo_dsh(target):
            ui.print_ok(f"dsh scaffold: {path}")
        ui.print_hint('  `canfar lab panel run . "C1: ...; C2: ..." my-slug`')
    ui.print_hint(f"  `cd {target}`")
    ui.print_hint("  `pixi add python numpy`" if kind.value == "pixi" else "  `uv add numpy`")
    if not no_gh and shutil.which("gh") and not no_git:
        ui.print_hint(f"  `gh repo create {name} --private --source=. --push`")


def _gh_fork_parent(spec: str) -> str | None:
    """Return parent owner/name when ``spec`` is a GitHub fork, else None."""
    try:
        parent = run_capture(
            [
                "gh",
                "repo",
                "view",
                spec,
                "--json",
                "isFork,parent",
                "-q",
                "if .isFork then .parent.nameWithOwner else empty end",
            ]
        ).strip()
    except LabError:
        return None
    return parent or None


def _finalize_clone(
    dest: Path,
    repo: str,
    *,
    ref: str | None,
) -> str:
    """Checkout ``ref`` if given, wire upstream for forks, return short HEAD SHA."""
    from canfar_lab.core.git import git_ensure_upstream, git_head_sha, git_sync_from_origin

    if ref:
        git_sync_from_origin(dest, ref=ref, force=True)
    parent = _gh_fork_parent(repo)
    if parent:
        git_ensure_upstream(dest, parent)
    return git_head_sha(dest)


def register(app: typer.Typer) -> None:
    @app.command("init")
    def init_cmd(
        ctx: typer.Context,
        name: Annotated[str, typer.Argument(help="Project directory name.")],
        uv_project: Annotated[bool, typer.Option("--uv")] = False,
        no_git: Annotated[bool, typer.Option("--no-git")] = False,
        no_gh: Annotated[bool, typer.Option("--no-gh")] = False,
        src_dir: Annotated[
            Path | None,
            typer.Option(
                "--dir",
                help="Source directory (default: $SRCDIR).",
            ),
        ] = None,
        with_dsh: Annotated[
            bool,
            typer.Option("--with-dsh", help="Scaffold .dsh/ review-panel patch + README."),
        ] = False,
    ) -> None:
        """Create a new pixi or uv project under the work directory.

        Examples:
            canfar lab init mylab
            canfar lab init mylab --uv
            canfar lab init mylab --dir ~/src
            canfar lab init mylab --with-dsh
        """
        _init_impl(ctx, name, uv_project, no_git, no_gh, src_dir, with_dsh=with_dsh)

    @app.command()
    def clone(
        ctx: typer.Context,
        repos: Annotated[
            list[str] | None,
            typer.Argument(help="GitHub repo(s): owner/name, or a name (your user, then astroai)."),
        ] = None,
        to: Annotated[
            Path | None,
            typer.Option("--to", help="Destination directory (one repo only)."),
        ] = None,
        src_dir: Annotated[
            Path | None,
            typer.Option(
                "--dir",
                help="Source directory (parent for clones; default: $SRCDIR / $WORK).",
            ),
        ] = None,
        from_env: Annotated[str | None, typer.Option("--from-env")] = None,
        from_path: Annotated[Path | None, typer.Option("--from")] = None,
        update: Annotated[
            bool,
            typer.Option(
                "--update",
                help="If the dest already exists, fetch+ff from origin (latest fork tip).",
            ),
        ] = False,
        ref: Annotated[
            str | None,
            typer.Option(
                "--ref",
                help="Branch, tag, or commit SHA to check out after clone/update.",
            ),
        ] = None,
        force: Annotated[
            bool,
            typer.Option(
                "--force",
                help="With --update, hard-reset a dirty tree to the target tip.",
            ),
        ] = False,
    ) -> None:
        """Clone GitHub repo(s) into $WORK ($SCRATCH/src on CANFAR) and install deps.

        Prefers your GitHub user fork when given a bare name. Use ``--update`` so
        jobs/sessions refresh to the latest pushed tip on origin. ``--ref`` pins a
        branch/SHA. Prints the short HEAD SHA so job scripts can record what ran.

        Examples:
            canfar lab clone myproject
            canfar lab clone sfabbro/torchsky --update
            canfar lab clone sfabbro/torchsky --ref topic
            canfar lab clone --from-env ml-base myorg/myproject
            canfar lab clone owner/repo --to $WORK/custom
        """
        from canfar_lab.core.git import git_ensure_upstream, git_head_sha, git_sync_from_origin
        from canfar_lab.core.project import (
            bootstrap_lock,
            detect_project,
            install_project,
            resolve_save_dir,
            warm_cache,
        )
        from canfar_lab.utils.subprocess import run

        opts = get_opts(ctx)
        settings = get_settings()
        from_env = from_env or settings.clone_from_env
        names = list(repos or [])
        if not names:
            ui.print_error("clone needs a repo name")
            ui.print_hint("  canfar lab clone myproject")
            ui.print_hint("  canfar lab clone owner/repo")
            ui.print_hint("  canfar lab clone myproject --update")
            raise typer.Exit(1)
        if to is not None and src_dir is not None:
            ui.print_error("--dir and --to cannot be combined")
            ui.print_hint("  --dir is the parent; --to is the exact destination for one repo")
            raise typer.Exit(1)
        if to is not None and len(names) != 1:
            ui.print_error("--to only works with one repo")
            raise typer.Exit(1)
        if from_path and not from_env:
            ui.print_error("--from requires --from-env <name>")
            raise typer.Exit(1)
        if force and not update:
            ui.print_error("--force requires --update")
            raise typer.Exit(1)
        if shutil.which("gh") is None:
            ui.print_error("gh required.\n  `gh auth login`")
            raise typer.Exit(1)
        paths = resolve_paths()
        try:
            parent = _src_dir(src_dir) if src_dir is not None else paths.work_dir
        except LabError as exc:
            ui.print_error(str(exc))
            raise typer.Exit(1) from exc
        ui.print_hint(f"  work dir: {parent}")
        jobs: list[tuple[str, Path]] = []
        for raw in names:
            try:
                repo = resolve_clone_spec(raw)
            except LabError as exc:
                ui.print_error(str(exc))
                raise typer.Exit(1) from exc
            dest = to.expanduser() if to is not None else parent / repo.rsplit("/", 1)[-1]
            jobs.append((repo, dest))

        failed = 0
        save_dir: Path | None = None
        if opts.dry_run and from_env:
            ui.print_hint(f"  would warm caches from '{from_env}'")
        if from_env and not opts.dry_run:
            try:
                save_dir = resolve_save_dir(from_env, paths.save_dir, from_path)
                with ui.progress_task(f"Warming caches from '{from_env}'...", quiet=opts.quiet):
                    warm_cache(save_dir)
            except LabError as exc:
                ui.print_error(str(exc))
                raise typer.Exit(1) from exc

        for repo, dest in jobs:
            if dest.exists():
                if not update:
                    ui.print_error(f"Target already exists: {dest}")
                    ui.print_hint("  canfar lab clone … --update   # fetch+ff latest from origin")
                    failed += 1
                    continue
                if opts.dry_run:
                    extra = f" ref={ref}" if ref else ""
                    ui.print_ok(f"dry-run: would update {repo} @ {dest}{extra}")
                    continue
                try:
                    with ui.progress_task(f"Updating {repo}...", quiet=opts.quiet):
                        sha = git_sync_from_origin(dest, ref=ref, force=force)
                        parent_repo = _gh_fork_parent(repo)
                        if parent_repo:
                            git_ensure_upstream(dest, parent_repo)
                    ui.print_ok(f"Updated: `cd {dest}` @ {sha} ({repo})")
                except LabError as exc:
                    ui.print_error(str(exc))
                    failed += 1
                continue
            if opts.dry_run:
                extra = f" ref={ref}" if ref else ""
                ui.print_ok(f"dry-run: would clone {repo} -> {dest}{extra}")
                continue
            try:
                with ui.progress_task(f"Cloning {repo}...", quiet=opts.quiet):
                    run(["gh", "repo", "clone", repo, str(dest)])
                    sha = _finalize_clone(dest, repo, ref=ref)
                kind = detect_project(dest)
                bootstrap = bool(save_dir and kind and bootstrap_lock(save_dir, dest))
                if kind:
                    with ui.progress_task(f"Installing {kind.value}...", quiet=opts.quiet):
                        install_project(dest, bootstrap_lock=bootstrap, quiet=opts.quiet)
            except LabError as exc:
                ui.print_error(str(exc))
                failed += 1
                continue
            sha = sha or git_head_sha(dest)
            ui.print_ok(f"Ready: `cd {dest}` @ {sha} ({repo})")
        if failed:
            raise typer.Exit(1)

    @app.command()
    def save(
        ctx: typer.Context,
        name: Annotated[str | None, typer.Argument()] = None,
        to: Annotated[
            Path | None,
            typer.Option("--to", help="Directory to write this snapshot."),
        ] = None,
        from_path: Annotated[
            Path | None,
            typer.Option("--from", help="With --list, directory to scan for snapshots."),
        ] = None,
        full: Annotated[
            bool, typer.Option("--full", help="Pack .pixi / .venv into the snapshot.")
        ] = False,
        list_flag: Annotated[
            bool,
            typer.Option("--list", "-l", help="List saved environments."),
        ] = False,
        json_output: Annotated[
            bool, typer.Option("--json", help="Machine-readable output.")
        ] = False,
    ) -> None:
        """Save the current project, or list snapshots.

        Examples:
            canfar lab save
            canfar lab save mylab --full
            canfar lab save mylab --to /arc/projects/group/env-saves/mylab
            canfar lab save --list
            canfar lab save --list --json
            canfar lab save --list --from /arc/projects/group/env-saves
        """
        opts = merge_opts(ctx, json_output=json_output)
        paths = resolve_paths()
        if list_flag:
            if name or to or full:
                ui.print_error("--list cannot be combined with NAME, --to, or --full")
                raise typer.Exit(1)
            _print_save_list(json_output=opts.json, root=from_path or paths.save_dir)
            return
        if from_path is not None:
            ui.print_error("--from is only valid with --list (use --to to choose where to write)")
            raise typer.Exit(1)
        cwd = Path.cwd()
        save_name = name or cwd.name
        save_dir = to or paths.save_dir / save_name
        try:
            kind = require_project(cwd)
            if opts.dry_run:
                if opts.json:
                    ui.print_json(
                        {
                            "dry_run": True,
                            "name": save_name,
                            "path": str(save_dir),
                            "kind": kind.value,
                            "full": full,
                        }
                    )
                else:
                    ui.print_ok(f"dry-run: would save '{save_name}' -> {save_dir} ({kind.value})")
                return
            save_env(save_name, save_dir, cwd, full=full)
            payload = {
                "name": save_name,
                "path": str(save_dir),
                "kind": kind.value,
                "full": full,
            }
        except (LabError, OSError) as exc:
            ui.print_error(str(exc))
            raise typer.Exit(1) from exc
        if opts.json:
            ui.print_json(payload)
        else:
            ui.print_ok(f"Saved '{save_name}' -> {save_dir} ({format_dir_size(save_dir)})")

    @app.command()
    def resume(
        ctx: typer.Context,
        name: Annotated[str, typer.Argument(help="Snapshot name.")],
        to: Annotated[
            Path | None,
            typer.Option("--to", help="Directory to restore into (default: $SRCDIR/NAME)."),
        ] = None,
        from_path: Annotated[
            Path | None,
            typer.Option("--from", help="Snapshot directory, or a parent of named snapshots."),
        ] = None,
        yes: Annotated[
            bool, typer.Option("--yes", "-y", help="Replace a non-empty target.")
        ] = False,
        json_output: Annotated[
            bool, typer.Option("--json", help="Machine-readable output.")
        ] = False,
    ) -> None:
        """Restore a saved environment.

        Examples:
            canfar lab resume mylab
            canfar lab resume mylab --yes
            canfar lab resume mylab --from /arc/projects/group/env-saves
            canfar lab resume mylab --to $SRCDIR/mylab --from /arc/projects/group/env-saves/mylab
        """
        from canfar_lab.core.project import resolve_save_dir, restore_env

        opts = merge_opts(ctx, yes=yes, json_output=json_output)
        paths = resolve_paths()
        dest = to or paths.work_dir / name
        nonempty = dest.is_dir() and any(dest.iterdir())
        if dest.exists() and dest.is_file():
            ui.print_error(f"Target exists and is a file: {dest}")
            raise typer.Exit(1)
        if nonempty:
            ui.print_warn(f"Target exists and is not empty: {dest}")
            if not opts.yes and not opts.dry_run:
                ui.print_hint("  Use --yes to overwrite, or pass a different --to.")
                raise typer.Exit(1)
        try:
            save_dir = resolve_save_dir(name, paths.save_dir, from_path)
            if opts.dry_run:
                if opts.json:
                    ui.print_json(
                        {
                            "dry_run": True,
                            "name": name,
                            "from": str(save_dir),
                            "path": str(dest),
                            "replace": nonempty,
                        }
                    )
                else:
                    ui.print_ok(f"dry-run: would restore '{name}' -> {dest}")
                    if nonempty:
                        ui.print_hint(f"  would replace {dest}")
                return
            if nonempty:
                shutil.rmtree(dest)
            with ui.progress_task("Restoring environment...", quiet=opts.quiet):
                restore_env(save_dir, dest)
        except (LabError, OSError) as exc:
            ui.print_error(str(exc))
            raise typer.Exit(1) from exc
        if opts.json:
            ui.print_json({"name": name, "from": str(save_dir), "path": str(dest)})
        else:
            ui.print_ok(f"Restored in {dest}")
            ui.print_hint(f"  `cd {dest}`")
