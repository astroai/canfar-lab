"""Tests for git helpers used by ``canfar lab clone --update``."""

from __future__ import annotations

from pathlib import Path

import pytest

from canfar_lab.core.git import (
    git_ensure_upstream,
    git_head_sha,
    git_sync_from_origin,
)
from canfar_lab.errors import LabError
from canfar_lab.utils.subprocess import run, run_capture


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    run(["git", "init", "-q"], cwd=path)
    run(["git", "config", "user.email", "test@example.com"], cwd=path)
    run(["git", "config", "user.name", "test"], cwd=path)
    run(["git", "checkout", "-b", "main"], cwd=path)
    (path / "README").write_text("a\n")
    run(["git", "add", "README"], cwd=path)
    run(["git", "commit", "-m", "init", "--quiet"], cwd=path)
    return path


def test_git_sync_from_origin_updates_from_remote(tmp_path: Path) -> None:
    remote = _git_repo(tmp_path / "remote")
    bare = tmp_path / "bare.git"
    run(["git", "clone", "--bare", "-q", str(remote), str(bare)])
    work = tmp_path / "work"
    run(["git", "clone", "-q", str(bare), str(work)])
    (remote / "README").write_text("b\n")
    run(["git", "add", "README"], cwd=remote)
    run(["git", "commit", "-m", "two", "--quiet"], cwd=remote)
    run(["git", "push", "-q", str(bare), "HEAD:main"], cwd=remote)
    old = git_head_sha(work)
    new = git_sync_from_origin(work)
    assert new
    assert new != old
    assert (work / "README").read_text() == "b\n"


def test_git_sync_refuses_dirty(tmp_path: Path) -> None:
    remote = _git_repo(tmp_path / "remote")
    work = tmp_path / "work"
    run(["git", "clone", "-q", str(remote), str(work)])
    (work / "README").write_text("dirty\n")
    with pytest.raises(LabError, match="Dirty"):
        git_sync_from_origin(work)


def test_git_sync_force_resets_dirty(tmp_path: Path) -> None:
    remote = _git_repo(tmp_path / "remote")
    bare = tmp_path / "bare.git"
    run(["git", "clone", "--bare", "-q", str(remote), str(bare)])
    work = tmp_path / "work"
    run(["git", "clone", "-q", str(bare), str(work)])
    (work / "README").write_text("dirty\n")
    (remote / "README").write_text("b\n")
    run(["git", "add", "README"], cwd=remote)
    run(["git", "commit", "-m", "two", "--quiet"], cwd=remote)
    run(["git", "push", "-q", str(bare), "HEAD:main"], cwd=remote)
    sha = git_sync_from_origin(work, force=True)
    assert sha
    assert (work / "README").read_text() == "b\n"


def test_git_sync_ref_branch(tmp_path: Path) -> None:
    remote = _git_repo(tmp_path / "remote")
    run(["git", "checkout", "-b", "wip/topic"], cwd=remote)
    (remote / "README").write_text("topic\n")
    run(["git", "add", "README"], cwd=remote)
    run(["git", "commit", "-m", "topic", "--quiet"], cwd=remote)
    bare = tmp_path / "bare.git"
    run(["git", "clone", "--bare", "-q", str(remote), str(bare)])
    work = tmp_path / "work"
    run(["git", "clone", "-q", str(bare), str(work)])
    sha = git_sync_from_origin(work, ref="wip/topic")
    assert sha
    assert (work / "README").read_text() == "topic\n"
    assert run_capture(["git", "branch", "--show-current"], cwd=work).strip() == "wip/topic"


def test_git_ensure_upstream(tmp_path: Path) -> None:
    work = _git_repo(tmp_path / "work")
    git_ensure_upstream(work, "astroai/torchsky")
    url = run_capture(["git", "remote", "get-url", "upstream"], cwd=work)
    assert "astroai/torchsky" in url
