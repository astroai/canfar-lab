"""Shared test helpers for astroai tests.

Add fixtures and utility functions here that need to be reused across
multiple test modules.  Prefer this module over conftest.py for plain
helper functions — conftest.py is for pytest fixture auto-discovery.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from canfar_lab.models.manifest import EnvManifest, ProjectKind
from canfar_lab.sync.catalog import repo_path


def git_run(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git in ``cwd`` capturing output (test-friendly)."""
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def head_sha(repo: Path, ref: str = "HEAD") -> str:
    return git_run(["rev-parse", ref], repo).stdout.strip()


def make_origin(root: Path, slug: str = "demo/one") -> Path:
    """Bare repository standing in for GitHub."""
    org, _, name = slug.partition("/")
    origin = root / "remotes" / org / f"{name}.git"
    origin.parent.mkdir(parents=True, exist_ok=True)
    git_run(["init", "--bare", "--initial-branch=main", str(origin)], cwd=root)
    return origin


def clone_repo(origin: Path, dest: Path) -> Path:
    """Local clone with deterministic committer settings."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    git_run(["clone", "--quiet", str(origin), str(dest)], cwd=dest.parent)
    git_run(["config", "user.email", "test@example.com"], dest)
    git_run(["config", "user.name", "Test"], dest)
    git_run(["config", "commit.gpgsign", "false"], dest)
    return dest


def commit_file(repo: Path, name: str, content: str, message: str = "update") -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git_run(["add", "--", name], repo)
    git_run(["commit", "-m", message, "--quiet"], repo)
    return head_sha(repo)


def make_worktrees(root: Path, slug: str = "demo/one") -> tuple[Path, Path, Path]:
    """(origin bare, desk clone, session clone), both synced at a seed commit."""
    origin = make_origin(root, slug)
    desk = clone_repo(origin, repo_path(root / "desk", slug))
    commit_file(desk, "README.md", "# demo\n", "seed")
    git_run(["push", "-u", "origin", "main"], desk)
    session = clone_repo(origin, repo_path(root / "session", slug))
    return origin, desk, session


def write_manifest(
    save_dir: Path,
    name: str,
    kind: ProjectKind = ProjectKind.PIXI,
    full: bool = False,
) -> None:
    """Create a minimal save fixture — a directory with a manifest.json.

    Used by tests that exercise list_saves, save_rows, restore_env, etc.
    without needing a real pixi/uv project.
    """
    save_dir.mkdir(parents=True, exist_ok=True)
    manifest = EnvManifest(
        name=name,
        kind=kind,
        saved_at="20250101T000000Z",
        saved_from="/scratch/src/" + name,
        user="testuser",
        full=full,
    )
    (save_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")
