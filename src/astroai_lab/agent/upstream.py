"""GitHub sparse-clone cache used by plugin github-rule installs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _upstream_cache_root(home: Path, repo: str) -> Path:
    return home / ".cache" / "astroai-lab" / "upstream-skills" / repo.replace("/", "_")


def upstream_cache_path(home: Path, repo: str) -> Path:
    return _upstream_cache_root(home, repo)


def _git_run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    from astroai_lab.agent.setup_state import GIT_TIMEOUT_SEC

    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout="",
            stderr=f"git timed out after {GIT_TIMEOUT_SEC}s: {exc}",
        )


def _clone_upstream_repo(cache_root: Path, repo: str, paths: str | list[str]) -> tuple[str, str]:
    path_list = [paths] if isinstance(paths, str) else list(paths)
    if cache_root.exists():
        shutil.rmtree(cache_root)
    cache_root.parent.mkdir(parents=True, exist_ok=True)
    clone = _git_run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            f"https://github.com/{repo}.git",
            str(cache_root),
        ]
    )
    if clone.returncode != 0:
        detail = (clone.stderr or clone.stdout or "clone failed").strip()
        return "failed", detail
    # --no-cone allows file paths (e.g. .cursor/rules/ponytail.mdc), not only dirs.
    sparse = _git_run(
        ["git", "-C", str(cache_root), "sparse-checkout", "set", "--no-cone", *path_list]
    )
    if sparse.returncode != 0:
        detail = (sparse.stderr or sparse.stdout or "sparse-checkout failed").strip()
        return "failed", detail
    return "cloned", repo


def _refresh_upstream_repo(cache_root: Path, repo: str, paths: str | list[str]) -> tuple[str, str]:
    path_list = [paths] if isinstance(paths, str) else list(paths)
    if not (cache_root / ".git").is_dir():
        return _clone_upstream_repo(cache_root, repo, path_list)
    fetch = _git_run(["git", "-C", str(cache_root), "fetch", "--depth", "1", "origin", "HEAD"])
    if fetch.returncode != 0:
        shutil.rmtree(cache_root)
        return _clone_upstream_repo(cache_root, repo, path_list)
    reset = _git_run(["git", "-C", str(cache_root), "reset", "--hard", "FETCH_HEAD"])
    if reset.returncode != 0:
        detail = (reset.stderr or reset.stdout or "reset failed").strip()
        return "failed", detail
    sparse = _git_run(
        ["git", "-C", str(cache_root), "sparse-checkout", "set", "--no-cone", *path_list]
    )
    if sparse.returncode != 0:
        detail = (sparse.stderr or sparse.stdout or "sparse-checkout failed").strip()
        return "failed", detail
    return "updated", repo
