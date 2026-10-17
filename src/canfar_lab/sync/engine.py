"""GitHub-relay sync engine: publish, absorb, bootstrap.

Behavior contract (docs/desk-sync.md §6):

* **publish** — commit dirty trees (opt-in, with a secret-scan gate) and push
  the branch to ``origin``. Dirty trees are never stashed.
* **absorb** — fetch and fast-forward. ``rebase`` mode is desk-only: the desk
  is human-attended, the session is not, so a session divergence halts.
* **bootstrap** — clone missing repos fork-first with a canonical fallback.

Nothing here touches the mailbox; the watchers in :mod:`canfar_lab.sync.watch`
own the desk ↔ session protocol.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canfar_lab.sync import catalog as catalog_mod
from canfar_lab.sync import clock

DEFAULT_EXCLUDE = [
    "*.log",
    "*.zst",
    "*.tar",
    "*.tar.gz",
    "__pycache__/*",
    ".pixi/*",
    ".venv/*",
    "node_modules/*",
]

#: Files that block an auto-commit entirely (never staged, never pushed).
SECRET_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.crt",
    "*.kdbx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "*.token",
    ".credentials*",
    "credentials.*",
]


def _matches(path: str, patterns: list[str]) -> bool:
    name = Path(path).name
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
        if fnmatch.fnmatch(path, f"*/{pattern}"):
            return True
    return False


def _porcelain_path(line: str) -> str:
    path = line[3:]
    if " -> " in path:  # renames and copies
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


@dataclass
class RepoAction:
    """One repo's outcome inside a publish/absorb run."""

    slug: str
    path: str
    action: str = "none"  # none|cloned|committed|pushed|fast-forwarded|rebased
    #: ok|up-to-date|dirty|skipped-dirty|diverged|missing|detached|secret-scan|error
    status: str = "ok"
    detail: str = ""
    head: str = ""
    ahead: int = 0
    behind: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "path": self.path,
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
            "head": self.head,
            "ahead": self.ahead,
            "behind": self.behind,
        }


@dataclass
class ExchangeReport:
    side: str
    op: str
    root: str
    dry_run: bool = False
    started: str = ""
    finished: str = ""
    repos: list[RepoAction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.repos:
            counts[rec.status] = counts.get(rec.status, 0) + 1
        return counts

    def touched(self) -> list[str]:
        """Slugs whose local state changed during the run."""
        return [rec.slug for rec in self.repos if rec.action in ("committed", "pushed", "cloned")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "op": self.op,
            "root": self.root,
            "dry_run": self.dry_run,
            "started": self.started,
            "finished": self.finished,
            "summary": self.summary(),
            "repos": [rec.to_dict() for rec in self.repos],
            "notes": self.notes,
        }


class SyncEngine:
    """Publish/absorb/clone operations over one workspace root."""

    def __init__(
        self,
        root: Path,
        catalog: list[str],
        *,
        side: str = "desk",
        exclude: list[str] | None = None,
        github_user: str | None = None,
        say=None,  # noqa: ANN001 — Callable[[str], None]
        timeout: int = 60,
    ) -> None:
        self.root = Path(root)
        self.catalog = list(catalog)
        self.side = side
        self.exclude = list(DEFAULT_EXCLUDE) + list(exclude or [])
        self.github_user = github_user
        self._detected_user = False
        self._say = say or (lambda message: None)
        self.timeout = timeout

    # ---------------------------------------------------------------- helpers

    def say(self, message: str) -> None:
        self._say(f"[sync:{self.side}] {message}")

    def repo_path(self, slug: str) -> Path:
        return catalog_mod.repo_path(self.root, slug)

    def default_message(self) -> str:
        return f"sync({self.side}): {clock.stamp()}"

    def _select(self, repos: list[str] | None) -> list[str]:
        return catalog_mod.normalize_selection(self.catalog, repos)

    def _resolve_user(self) -> str | None:
        if self.github_user is None and not self._detected_user:
            self._detected_user = True
            self.github_user = catalog_mod.detect_github_user()
        return self.github_user

    def _git(
        self, args: list[str], cwd: Path, *, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout or self.timeout,
        )

    @staticmethod
    def _clean(text: str, limit: int = 300) -> str:
        return " ".join((text or "").split())[:limit]

    def _head(self, repo: Path) -> str:
        cp = self._git(["rev-parse", "--short", "HEAD"], repo)
        return cp.stdout.strip() if cp.returncode == 0 else ""

    def _branch(self, repo: Path) -> str:
        cp = self._git(["branch", "--show-current"], repo)
        return cp.stdout.strip() if cp.returncode == 0 else ""

    def _porcelain(self, repo: Path) -> list[str]:
        cp = self._git(["status", "--porcelain"], repo)
        return [line for line in (cp.stdout or "").splitlines() if line.strip()]

    def _counts(self, repo: Path, ref: str) -> tuple[int, int] | None:
        """(behind, ahead) of HEAD vs ``ref``, or None when incomparable."""
        cp = self._git(["rev-list", "--left-right", "--count", f"{ref}...HEAD"], repo)
        if cp.returncode != 0:
            return None
        parts = (cp.stdout or "").split()
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    # ---------------------------------------------------------------- publish

    def publish(
        self,
        *,
        repos: list[str] | None = None,
        mode: str = "commit",
        message: str | None = None,
        dry_run: bool = False,
    ) -> ExchangeReport:
        """Commit (when ``mode=commit``) and push origin for the selected repos."""
        report = ExchangeReport(
            side=self.side,
            op="publish",
            root=str(self.root),
            dry_run=dry_run,
            started=clock.utc_iso(),
        )
        for slug in self._select(repos):
            record = RepoAction(slug=slug, path=str(self.repo_path(slug)))
            report.repos.append(record)
            repo = self.repo_path(slug)
            if not (repo / ".git").exists():
                record.status = "missing"
                record.detail = "not cloned here"
                continue
            branch = self._branch(repo)
            if not branch:
                record.status = "detached"
                record.detail = "HEAD is detached"
                continue

            fetched = self._git(["fetch", "--prune", "origin"], repo, timeout=150).returncode == 0
            if not fetched:
                report.notes.append(f"{slug}: origin fetch failed (offline?)")

            lines = self._porcelain(repo)
            paths = [_porcelain_path(line) for line in lines]
            secrets = sorted({p for p in paths if _matches(p, SECRET_PATTERNS)})

            counts = self._counts(repo, f"origin/{branch}") if fetched else None
            if counts is not None:
                record.behind, record.ahead = counts

            committed = False
            staged: list[str] = []
            if lines and mode == "commit":
                if secrets:
                    record.status = "secret-scan"
                    record.detail = "refused auto-commit; secret-like files: " + ", ".join(
                        secrets[:5]
                    )
                    record.head = self._head(repo)
                    continue
                if dry_run:
                    committed = True
                    record.action = "committed"
                    record.detail = f"would commit {len(lines)} change(s)"
                else:
                    self._git(["add", "-A", "."], repo)
                    excluded = sorted({p for p in paths if _matches(p, self.exclude)})
                    if excluded:
                        self._git(["reset", "--quiet", "--", *excluded], repo)
                    cp = self._git(["diff", "--cached", "--name-only"], repo)
                    staged = [ln for ln in (cp.stdout or "").splitlines() if ln.strip()]
                    if not staged:
                        record.status = "dirty"
                        record.detail = "only excluded files changed"
                        record.head = self._head(repo)
                        continue
                    commit = self._git(
                        [
                            "-c",
                            "commit.gpgsign=false",
                            "commit",
                            "-m",
                            message or self.default_message(),
                            "--quiet",
                        ],
                        repo,
                    )
                    if commit.returncode != 0:
                        record.status = "error"
                        record.detail = (
                            f"commit failed: {self._clean(commit.stderr or commit.stdout)}"
                        )
                        record.head = self._head(repo)
                        continue
                    committed = True
                    record.action = "committed"
            elif lines:
                record.status = "dirty"
                record.detail = f"{len(lines)} changed file(s); publish mode is report"
                record.head = self._head(repo)
                continue

            ahead = record.ahead
            if committed or ahead > 0:
                if dry_run:
                    record.action = "pushed"
                    record.status = "ok"
                    bits = []
                    if committed:
                        bits.append(f"would commit {len(lines)} change(s)")
                    if ahead:
                        bits.append(f"would push {ahead} commit(s)")
                    record.detail = "; ".join(bits) or "would push"
                else:
                    push = self._git(
                        ["push", "-u", "origin", branch], repo, timeout=max(self.timeout, 180)
                    )
                    if push.returncode == 0:
                        record.action = "pushed"
                        record.status = "ok"
                        bits = []
                        if committed:
                            bits.append(f"committed {len(staged)} file(s)")
                        if ahead:
                            bits.append(f"pushed {ahead} commit(s)")
                        record.detail = "; ".join(bits) or "pushed"
                    else:
                        record.status = "error"
                        record.detail = f"push failed: {self._clean(push.stderr)}"
            else:
                record.status = "up-to-date"
                record.detail = "clean and synced with origin"
            record.head = self._head(repo)
        report.finished = clock.utc_iso()
        return report

    # ----------------------------------------------------------------- absorb

    def absorb(
        self,
        *,
        repos: list[str] | None = None,
        mode: str = "ff",
        bootstrap: bool = False,
        dry_run: bool = False,
    ) -> ExchangeReport:
        """Fetch and integrate origin for the selected repos.

        ``mode="ff"`` refuses divergence; ``mode="rebase"`` rebases local
        commits (desk only) and aborts cleanly on conflicts.
        """
        report = ExchangeReport(
            side=self.side,
            op="absorb",
            root=str(self.root),
            dry_run=dry_run,
            started=clock.utc_iso(),
        )
        for slug in self._select(repos):
            record = RepoAction(slug=slug, path=str(self.repo_path(slug)))
            report.repos.append(record)
            repo = self.repo_path(slug)
            if not (repo / ".git").exists():
                if not bootstrap:
                    record.status = "missing"
                    record.detail = "not cloned here"
                    continue
                if dry_run:
                    record.action = "cloned"
                    record.detail = "would clone"
                    continue
                ok, detail = self._clone(slug)
                if ok:
                    record.action = "cloned"
                    record.status = "ok"
                    record.detail = detail
                    record.head = self._head(repo)
                else:
                    record.status = "error"
                    record.detail = detail
                continue

            branch = self._branch(repo)
            if not branch:
                record.status = "detached"
                record.detail = "HEAD is detached"
                continue

            lines = self._porcelain(repo)
            if any(not line.startswith("??") for line in lines):
                record.status = "skipped-dirty"
                record.detail = "uncommitted changes; publish first"
                continue

            fetched = self._git(["fetch", "--prune", "origin"], repo, timeout=150).returncode == 0
            if not fetched:
                record.status = "error"
                record.detail = "origin fetch failed (offline?)"
                continue

            ref = f"origin/{branch}"
            counts = self._counts(repo, ref)
            if counts is None:
                record.status = "no-origin-ref"
                record.detail = f"{ref} not found"
                continue
            record.behind, record.ahead = counts
            behind, ahead = counts

            if behind == 0:
                record.status = "up-to-date"
                record.detail = f"ahead {ahead}" if ahead else "clean and synced"
            elif ahead == 0:
                if dry_run:
                    record.action = "fast-forwarded"
                    record.status = "ok"
                    record.detail = f"would fast-forward {behind} commit(s)"
                else:
                    cp = self._git(["merge", "--ff-only", ref], repo)
                    if cp.returncode == 0:
                        record.action = "fast-forwarded"
                        record.status = "ok"
                        record.detail = f"fast-forwarded {behind} commit(s)"
                    else:
                        record.status = "error"
                        record.detail = (
                            f"fast-forward failed: {self._clean(cp.stderr or cp.stdout)}"
                        )
            elif mode == "rebase":
                if dry_run:
                    record.action = "rebased"
                    record.status = "ok"
                    record.detail = f"would rebase {ahead} local commit(s) on {ref}"
                else:
                    cp = self._git(["rebase", ref], repo, timeout=max(self.timeout, 120))
                    if cp.returncode == 0:
                        record.action = "rebased"
                        record.status = "ok"
                        record.detail = f"rebased {ahead} local commit(s)"
                    else:
                        self._git(["rebase", "--abort"], repo)
                        record.status = "diverged"
                        record.detail = (
                            f"rebase failed and was aborted: {self._clean(cp.stderr or cp.stdout)}"
                        )
            else:
                record.status = "diverged"
                record.detail = f"ahead {ahead}, behind {behind}; ff-only refuses"
            record.head = self._head(repo)
        report.finished = clock.utc_iso()
        return report

    # -------------------------------------------------------------- bootstrap

    def _clone(self, slug: str) -> tuple[bool, str]:
        """Clone ``slug`` fork-first; returns (ok, detail)."""
        org, _, name = slug.partition("/")
        dest = self.repo_path(slug)
        dest.parent.mkdir(parents=True, exist_ok=True)
        clone_timeout = max(self.timeout, 600)
        user = self._resolve_user()
        if user and org == "astroai":
            fork_url = f"git@github.com:{user}/{name}.git"
            cp = self._git(
                ["clone", "--quiet", fork_url, str(dest)], self.root, timeout=clone_timeout
            )
            if cp.returncode == 0:
                self._wire_upstream(dest, slug, user_fork=True)
                return True, f"cloned fork {user}/{name}"
            shutil.rmtree(dest, ignore_errors=True)
        cp = self._git(
            ["clone", "--quiet", f"git@github.com:{slug}.git", str(dest)],
            self.root,
            timeout=clone_timeout,
        )
        if cp.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            return False, f"clone failed: {self._clean(cp.stderr)}"
        if user and org == "astroai":
            self._wire_upstream(dest, slug, user_fork=False)
        return True, f"cloned {slug}"

    def _wire_upstream(self, repo: Path, slug: str, *, user_fork: bool) -> None:
        if user_fork:
            self._git(["remote", "add", "upstream", f"git@github.com:{slug}.git"], repo)
        try:
            self._git(["fetch", "--quiet", "upstream"], repo, timeout=150)
            self._git(["branch", "--set-upstream-to=upstream/main", "main"], repo)
        except subprocess.SubprocessError:
            pass


__all__ = [
    "DEFAULT_EXCLUDE",
    "SECRET_PATTERNS",
    "ExchangeReport",
    "RepoAction",
    "SyncEngine",
]
