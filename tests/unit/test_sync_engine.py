from __future__ import annotations

import subprocess
from pathlib import Path

from canfar_lab.sync.engine import SyncEngine
from tests.helpers import commit_file, git_run, head_sha, make_worktrees

SLUG = "demo/one"


def _engine(root: Path, side: str = "desk", **kwargs) -> SyncEngine:
    return SyncEngine(root, [SLUG], side=side, github_user="tester", **kwargs)


def test_publish_commits_and_pushes(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    (desk / "notes.txt").write_text("hello\n", encoding="utf-8")
    engine = _engine(tmp_path / "desk")

    report = engine.publish()
    rec = report.repos[0]
    assert rec.status == "ok"
    assert rec.action == "pushed"
    assert "committed" in rec.detail
    assert head_sha(desk).startswith(rec.head)

    git_run(["fetch", "--quiet", "origin"], session)
    assert head_sha(session, "origin/main") == head_sha(desk)


def test_publish_up_to_date_is_noop(tmp_path):
    _origin, desk, _session = make_worktrees(tmp_path)
    engine = _engine(tmp_path / "desk")
    first = engine.publish()
    assert first.repos[0].status == "up-to-date"

    commit_file(desk, "a.txt", "a\n", "local work")
    second = engine.publish()
    assert second.repos[0].action == "pushed"
    assert second.repos[0].status == "ok"

    third = engine.publish()
    assert third.repos[0].status == "up-to-date"


def test_publish_report_mode_never_commits(tmp_path):
    _origin, desk, _session = make_worktrees(tmp_path)
    commit_file(desk, "a.txt", "a\n", "seed")
    before = head_sha(desk)
    (desk / "wip.txt").write_text("wip\n", encoding="utf-8")

    report = _engine(tmp_path / "desk").publish(mode="report")
    rec = report.repos[0]
    assert rec.status == "dirty"
    assert head_sha(desk) == before


def test_publish_secret_scan_blocks_repo(tmp_path):
    _origin, desk, _session = make_worktrees(tmp_path)
    commit_file(desk, "a.txt", "a\n", "seed")
    before = head_sha(desk)
    (desk / ".env").write_text("SECRET=1\n", encoding="utf-8")

    report = _engine(tmp_path / "desk").publish()
    rec = report.repos[0]
    assert rec.status == "secret-scan"
    assert ".env" in rec.detail
    assert head_sha(desk) == before
    assert (desk / ".env").exists()


def test_publish_excluded_only_changes(tmp_path):
    _origin, desk, _session = make_worktrees(tmp_path)
    commit_file(desk, "a.txt", "a\n", "seed")
    before = head_sha(desk)
    (desk / "noisy.log").write_text("log\n", encoding="utf-8")

    report = _engine(tmp_path / "desk").publish()
    rec = report.repos[0]
    assert rec.status == "dirty"
    assert "excluded" in rec.detail
    assert head_sha(desk) == before


def test_publish_missing_repo_reported(tmp_path):
    (tmp_path / "desk").mkdir()
    report = _engine(tmp_path / "desk").publish()
    assert report.repos[0].status == "missing"


def test_absorb_fast_forwards(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    (desk / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _engine(tmp_path / "desk").publish()

    report = _engine(tmp_path / "session", side="session").absorb(mode="ff")
    rec = report.repos[0]
    assert rec.action == "fast-forwarded"
    assert rec.status == "ok"
    assert (session / "feature.py").is_file()


def test_absorb_skips_dirty_tree(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    desk_engine = _engine(tmp_path / "desk")
    session_engine = _engine(tmp_path / "session", side="session")

    commit_file(desk, "base.txt", "base\n", "add base")
    desk_engine.publish()
    session_engine.absorb(mode="ff")

    (session / "base.txt").write_text("locally modified\n", encoding="utf-8")
    (desk / "new.txt").write_text("new\n", encoding="utf-8")
    desk_engine.publish()

    rec = session_engine.absorb(mode="ff").repos[0]
    assert rec.status == "skipped-dirty"
    assert not (session / "new.txt").exists()


def test_absorb_divergence_halts_in_ff_mode(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    commit_file(session, "local.txt", "local\n", "session work")
    before = head_sha(session)
    (desk / "remote.txt").write_text("remote\n", encoding="utf-8")
    _engine(tmp_path / "desk").publish()

    rec = _engine(tmp_path / "session", side="session").absorb(mode="ff").repos[0]
    assert rec.status == "diverged"
    assert "ff-only" in rec.detail
    assert head_sha(session) == before
    assert not (session / "remote.txt").exists()


def test_absorb_rebase_mode_replays_local_commits(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    commit_file(session, "local.txt", "local\n", "session work")
    (desk / "remote.txt").write_text("remote\n", encoding="utf-8")
    _engine(tmp_path / "desk").publish()

    rec = _engine(tmp_path / "session", side="session").absorb(mode="rebase").repos[0]
    assert rec.action == "rebased"
    assert rec.status == "ok"
    assert (session / "remote.txt").is_file()
    assert (session / "local.txt").is_file()
    log = git_run(["log", "--oneline"], session).stdout
    assert log.index("session work") < log.index("sync(")  # rebased on top


def test_absorb_rebase_conflict_aborts_cleanly(tmp_path):
    _origin, desk, session = make_worktrees(tmp_path)
    commit_file(session, "README.md", "session edit\n", "session edit")
    commit_file(desk, "README.md", "desk edit\n", "desk edit")
    git_run(["push", "origin", "main"], desk)

    rec = _engine(tmp_path / "session", side="session").absorb(mode="rebase").repos[0]
    assert rec.status == "diverged"
    assert "aborted" in rec.detail
    assert not (session / ".git" / "rebase-merge").exists()
    status = git_run(["status", "--porcelain"], session).stdout.strip()
    assert status == ""


def test_absorb_bootstrap_clones_missing(tmp_path, monkeypatch):
    origin, desk, _session = make_worktrees(tmp_path)
    (desk / "seed.txt").write_text("seed\n", encoding="utf-8")
    _engine(tmp_path / "desk").publish()

    import shutil

    from tests.helpers import clone_repo

    shutil.rmtree(tmp_path / "session")
    (tmp_path / "session").mkdir()

    def fake_clone(self, slug):
        dest = self.repo_path(slug)
        clone_repo(origin, dest)
        return True, f"cloned {slug}"

    monkeypatch.setattr(SyncEngine, "_clone", fake_clone)
    engine = _engine(tmp_path / "session", side="session")
    rec = engine.absorb(bootstrap=True).repos[0]
    assert rec.action == "cloned"
    assert (tmp_path / "session" / "demo" / "one" / "seed.txt").is_file()


def test_clone_without_github_user_uses_canonical(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_git(self, args, cwd, *, timeout=None):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 1, "", "unreachable")

    monkeypatch.setattr("canfar_lab.sync.catalog.detect_github_user", lambda: None)
    monkeypatch.setattr(SyncEngine, "_git", fake_git)
    engine = SyncEngine(tmp_path / "root", [SLUG], side="session", github_user=None)
    ok, detail = engine._clone(SLUG)
    assert ok is False
    assert "clone failed" in detail
    assert calls[0][-2:] == [f"git@github.com:{SLUG}.git", str(tmp_path / "root" / "demo" / "one")]


def test_clone_falls_back_fork_then_canonical(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    outcomes = iter([1, 0])

    def fake_git(self, args, cwd, *, timeout=None):
        calls.append(list(args))
        rc = next(outcomes, 1)
        return subprocess.CompletedProcess(args, rc, "", "boom")

    monkeypatch.setattr(SyncEngine, "_git", fake_git)
    monkeypatch.setattr(SyncEngine, "_wire_upstream", lambda self, repo, slug, user_fork: None)
    engine = SyncEngine(tmp_path / "root", ["astroai/one"], side="session", github_user="tester")
    ok, detail = engine._clone("astroai/one")
    assert ok is True
    assert "cloned astroai/one" in detail
    assert "tester/one.git" in calls[0][2]
    assert "git@github.com:astroai/one.git" in calls[1]
