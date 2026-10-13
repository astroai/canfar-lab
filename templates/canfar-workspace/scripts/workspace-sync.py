#!/usr/bin/env python3
"""Workspace and Git Synchronization Tool for AstroAI & sfabbro repositories.

Symmetrically manages git clones across:
- Mac Laptop: ~/src/{astroai,sfabbro}
- CANFAR Sessions: $WORK/{astroai,sfabbro} (where $WORK is /scratch/src)

Features:
- status: Tabular view of branches, dirty working trees, ahead/behind fork & upstream.
- sync (pull): Safe fetch and fast-forward/rebase across all active repositories.
- bootstrap (clone): Clones missing repositories with canonical fork remotes (origin -> sfabbro, upstream -> astroai).
- dirty: Fast check for uncommitted or unpushed work before session teardown.
- push: Pushes committed feature or main branch work to the personal fork (origin).

Standard library only — runs cleanly on macOS and all CANFAR Linux session images.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Try tomllib (Python 3.11+) or tomli fallback
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


# Core tier repositories for fast bootstrapping on new sessions
CORE_REPOSITORIES = [
    "astroai/torchsky",
    "astroai/uspm",
    "astroai/cosmodist",
    "astroai/torchfits",
    "astroai/canfar-lab",
    "astroai/canfar-skills",
]

# Color codes
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, color: str) -> str:
    if not supports_color():
        return text
    return f"{color}{text}{RESET}"


def resolve_workspace_root() -> Path:
    """Resolve the root source directory for repositories."""
    # 1. If current directory has workspace.toml, it's the workspace root
    if (Path.cwd() / "workspace.toml").is_file():
        return Path.cwd().resolve()

    # 2. Check parent of this script (if in ~/src/scripts/workspace-sync.py)
    script_parent = Path(__file__).resolve().parent.parent
    if (script_parent / "workspace.toml").is_file():
        return script_parent

    # 3. CANFAR environment: /scratch is always the code host
    if Path("/scratch").is_dir() and not sys.platform == "darwin":
        scratch_src = Path("/scratch/src")
        return scratch_src

    # 4. If WORK is set
    if "WORK" in os.environ and os.environ["WORK"].strip():
        w = Path(os.environ["WORK"].strip()).expanduser().resolve()
        if (w / "workspace.toml").is_file() or (w / "astroai").is_dir():
            return w
        if (w.parent / "workspace.toml").is_file() or (w.parent / "astroai").is_dir():
            return w.parent

    # 5. Default Mac / local fallback
    home_src = (Path.home() / "src").resolve()
    if (home_src / "workspace.toml").is_file() or (home_src / "astroai").is_dir():
        return home_src

    return Path.cwd().resolve()


def find_workspace_toml(root: Path) -> Path | None:
    """Search for workspace.toml in current dir, workspace root, or repo parent."""
    candidates = [
        root / "workspace.toml",
        Path.cwd() / "workspace.toml",
        Path(__file__).resolve().parent.parent / "workspace.toml",
        Path.home() / "src" / "workspace.toml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def parse_workspace_toml(path: Path) -> dict[str, Any]:
    if tomllib is not None:
        with path.open("rb") as f:
            return tomllib.load(f)
    # Minimal fallback parser for repositories table if tomllib is missing
    repos: dict[str, Any] = {}
    current_key = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("[repositories.") and line.endswith("]"):
            current_key = line[len("[repositories.") : -1].strip('"').strip("'")
            repos[current_key] = {}
        elif current_key and "=" in line:
            k, _, v = line.partition("=")
            repos[current_key][k.strip()] = v.strip().strip('"').strip("'")
    return {"repositories": repos}


def load_repository_catalog(root: Path) -> list[dict[str, str]]:
    """Return all active repositories belonging to astroai or sfabbro."""
    toml_path = find_workspace_toml(root)
    repos: list[dict[str, str]] = []
    seen = set()

    if toml_path and toml_path.is_file():
        data = parse_workspace_toml(toml_path)
        for key, info in data.get("repositories", {}).items():
            org, _, name = key.partition("/")
            if org not in ("astroai", "sfabbro"):
                continue
            lifecycle = info.get("lifecycle", "active")
            if lifecycle in ("archived", "quarantine"):
                continue
            seen.add(key)
            repos.append({
                "key": key,
                "org": org,
                "name": name,
                "path": str(root / org / name),
                "policy": info.get("remote_policy", "astroai_fork" if org == "astroai" else "personal"),
                "lifecycle": lifecycle,
            })

    # Also scan disk for any existing checkouts under root/astroai and root/sfabbro
    for org in ("astroai", "sfabbro"):
        org_dir = root / org
        if not org_dir.is_dir():
            continue
        for child in org_dir.iterdir():
            if child.is_dir() and (child / ".git").is_dir():
                key = f"{org}/{child.name}"
                if key not in seen:
                    seen.add(key)
                    repos.append({
                        "key": key,
                        "org": org,
                        "name": child.name,
                        "path": str(child),
                        "policy": "astroai_fork" if org == "astroai" else "personal",
                        "lifecycle": "active",
                    })

    repos.sort(key=lambda r: (0 if r["org"] == "astroai" else 1, r["name"]))
    return repos


def run_git(repo_dir: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir)] + args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


@dataclass
class RepoStatus:
    key: str
    path: Path
    exists: bool
    branch: str = "none"
    dirty: bool = False
    untracked_count: int = 0
    stashed_count: int = 0
    origin_ahead: int = 0
    origin_behind: int = 0
    upstream_ahead: int = 0
    upstream_behind: int = 0
    has_upstream: bool = False
    has_origin: bool = False
    notes: str = ""


def get_repo_status(info: dict[str, str]) -> RepoStatus:
    repo_path = Path(info["path"])
    key = info["key"]
    if not (repo_path / ".git").is_dir():
        return RepoStatus(key=key, path=repo_path, exists=False, notes="missing")

    # Current branch
    res = run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = res.stdout.strip() or "HEAD"

    # Dirty status
    res = run_git(repo_path, ["status", "--porcelain"])
    dirty_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    dirty = any(not ln.startswith("??") for ln in dirty_lines)
    untracked = sum(1 for ln in dirty_lines if ln.startswith("??"))

    # Stash count
    res = run_git(repo_path, ["stash", "list"])
    stashes = len([ln for ln in res.stdout.splitlines() if ln.strip()])

    # Check remotes
    res = run_git(repo_path, ["remote"])
    remotes = set(res.stdout.split())
    has_origin = "origin" in remotes
    has_upstream = "upstream" in remotes

    origin_ahead, origin_behind = 0, 0
    upstream_ahead, upstream_behind = 0, 0

    if has_origin:
        # Check against origin/branch
        res = run_git(repo_path, ["rev-list", "--count", "--left-right", f"origin/{branch}...HEAD"])
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split()
            if len(parts) == 2:
                origin_behind, origin_ahead = int(parts[0]), int(parts[1])

    if has_upstream:
        target = "upstream/main" if branch == "main" else f"upstream/{branch}"
        res = run_git(repo_path, ["rev-list", "--count", "--left-right", f"{target}...HEAD"])
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split()
            if len(parts) == 2:
                upstream_behind, upstream_ahead = int(parts[0]), int(parts[1])

    return RepoStatus(
        key=key,
        path=repo_path,
        exists=True,
        branch=branch,
        dirty=dirty,
        untracked_count=untracked,
        stashed_count=stashes,
        origin_ahead=origin_ahead,
        origin_behind=origin_behind,
        upstream_ahead=upstream_ahead,
        upstream_behind=upstream_behind,
        has_upstream=has_upstream,
        has_origin=has_origin,
    )


def cmd_status(catalog: list[dict[str, str]], root: Path) -> int:
    print(f"\n{BOLD}Workspace Status: {root}{RESET}")
    print(f"{'Repository':<32} {'Branch':<18} {'Working Tree':<16} {'vs Origin':<16} {'vs Upstream':<16}")
    print("-" * 100)

    any_dirty = False
    for item in catalog:
        st = get_repo_status(item)
        if not st.exists:
            print(f"{c(st.key, DIM):<32} {c('(not cloned)', DIM):<18} {c('—', DIM):<16} {c('—', DIM):<16} {c('—', DIM):<16}")
            continue

        # Working tree column
        wt_str = "clean"
        wt_color = GREEN
        if st.dirty:
            wt_str = "dirty"
            wt_color = RED
            any_dirty = True
        if st.untracked_count > 0:
            wt_str += f" (+{st.untracked_count}?)"
            if wt_color == GREEN:
                wt_color = YELLOW
        if st.stashed_count > 0:
            wt_str += f" [{st.stashed_count}s]"

        # Origin column
        if not st.has_origin:
            orig_str = "no origin"
            orig_color = RED
        elif st.origin_ahead == 0 and st.origin_behind == 0:
            orig_str = "synced"
            orig_color = GREEN
        else:
            parts = []
            if st.origin_ahead > 0:
                parts.append(f"ahead {st.origin_ahead}")
            if st.origin_behind > 0:
                parts.append(f"behind {st.origin_behind}")
            orig_str = ", ".join(parts)
            orig_color = YELLOW

        # Upstream column
        if not st.has_upstream:
            up_str = "—"
            up_color = DIM
        elif st.upstream_ahead == 0 and st.upstream_behind == 0:
            up_str = "synced"
            up_color = GREEN
        else:
            parts = []
            if st.upstream_ahead > 0:
                parts.append(f"ahead {st.upstream_ahead}")
            if st.upstream_behind > 0:
                parts.append(f"behind {st.upstream_behind}")
            up_str = ", ".join(parts)
            up_color = YELLOW if st.upstream_behind == 0 else RED

        b_color = BOLD if st.branch == "main" else CYAN
        print(f"{st.key:<32} {c(st.branch, b_color):<18} {c(wt_str, wt_color):<16} {c(orig_str, orig_color):<16} {c(up_str, up_color):<16}")

    print("-" * 100)
    return 1 if any_dirty else 0


def cmd_dirty(catalog: list[dict[str, str]], root: Path) -> int:
    """List all repositories with uncommitted or unpushed work."""
    dirty_count = 0
    for item in catalog:
        st = get_repo_status(item)
        if not st.exists:
            continue
        reasons = []
        if st.dirty:
            reasons.append("uncommitted changes")
        if st.untracked_count > 0:
            reasons.append(f"{st.untracked_count} untracked files")
        if st.origin_ahead > 0:
            reasons.append(f"{st.origin_ahead} unpushed commits (vs origin)")
        if st.stashed_count > 0:
            reasons.append(f"{st.stashed_count} stashes")

        if reasons:
            dirty_count += 1
            print(f"{c(st.key, RED)} ({st.branch}): {', '.join(reasons)}")
            res = run_git(st.path, ["status", "--short"])
            for line in res.stdout.splitlines()[:10]:
                print(f"    {line}")
            if len(res.stdout.splitlines()) > 10:
                print(f"    ... (+{len(res.stdout.splitlines()) - 10} more)")

    if dirty_count == 0:
        print(c("All repositories are clean and synced to origin.", GREEN))
        return 0
    else:
        print(f"\n{c(f'Total repositories needing attention: {dirty_count}', YELLOW)}")
        return 1


def sync_single_repo(st: RepoStatus, item: dict[str, str]) -> str:
    """Fetch and sync a single repository."""
    repo = st.path
    branch = st.branch
    if branch == "HEAD":
        return f"{c('DETACHED', RED)}: HEAD is detached, skipping auto-sync"

    stashed = False
    if st.dirty:
        res = run_git(repo, ["stash", "push", "-m", "workspace-sync auto-stash", "--quiet"])
        if res.returncode == 0:
            stashed = True
        else:
            return f"{c('DIRTY', RED)}: uncommitted changes could not be stashed"

    # Fetch remotes
    sync_remote = "upstream" if st.has_upstream else "origin"
    if st.has_origin:
        run_git(repo, ["fetch", "--prune", "origin"])
    if st.has_upstream:
        run_git(repo, ["fetch", "--prune", "upstream"])

    # Determine sync target
    target_ref = None
    if sync_remote == "upstream" and branch == "main":
        target_ref = "upstream/main"
        run_git(repo, ["branch", "--set-upstream-to=upstream/main", "main"])
    elif st.has_origin:
        target_ref = f"origin/{branch}"
        run_git(repo, ["branch", f"--set-upstream-to=origin/{branch}", branch])

    if not target_ref:
        if stashed:
            run_git(repo, ["stash", "pop", "--quiet"])
        return f"{c('SKIP', YELLOW)}: no tracking target"

    # Check ahead / behind
    res = run_git(repo, ["rev-list", "--count", "--left-right", f"{target_ref}...HEAD"])
    if res.returncode != 0:
        if stashed:
            run_git(repo, ["stash", "pop", "--quiet"])
        return f"{c('WARN', YELLOW)}: could not compare with {target_ref}"

    behind, ahead = [int(x) for x in res.stdout.strip().split()]

    result_str = ""
    if behind == 0:
        result_str = f"{c('OK', GREEN)}: up to date with {target_ref} (ahead {ahead})"
    elif ahead == 0:
        res = run_git(repo, ["merge", "--ff-only", target_ref, "--quiet"])
        if res.returncode == 0:
            result_str = f"{c('FF', GREEN)}: fast-forwarded {behind} commit(s) from {target_ref}"
        else:
            result_str = f"{c('FAIL', RED)}: fast-forward merge from {target_ref} failed"
    else:
        # Diverged: attempt rebase
        res = run_git(repo, ["rebase", target_ref, "--quiet"])
        if res.returncode == 0:
            result_str = f"{c('REBASED', CYAN)}: rebased on {target_ref} (ahead {ahead}, behind was {behind})"
        else:
            run_git(repo, ["rebase", "--abort"])
            result_str = f"{c('DIVERGED', RED)}: diverged from {target_ref} (ahead {ahead}, behind {behind}); rebase aborted"

    if stashed:
        res = run_git(repo, ["stash", "pop", "--quiet"])
        if res.returncode != 0:
            result_str += f" | {c('STASH CONFLICT', RED)}: resolve manually (git stash drop when done)"

    return result_str


def cmd_sync(catalog: list[dict[str, str]], root: Path, repo_filters: list[str] | None = None) -> int:
    print(f"\n{BOLD}Syncing repositories under: {root}{RESET}\n")
    attention = []

    for item in catalog:
        if repo_filters and not any(f in item["key"] or f in item["name"] for f in repo_filters):
            continue

        st = get_repo_status(item)
        if not st.exists:
            continue

        label = f"{item['key']} ({st.branch})"
        print(f"… {label:<38} ", end="", flush=True)
        msg = sync_single_repo(st, item)
        print(msg)
        if "FAIL" in msg or "DIVERGED" in msg or "CONFLICT" in msg or "DETACHED" in msg:
            attention.append(item["key"])

    if attention:
        print(f"\n{c('Repositories needing attention:', RED)}")
        for k in attention:
            print(f"  - {k}")
        return 1

    print(f"\n{c('All repositories successfully synchronized.', GREEN)}")
    return 0


def clone_repo(root: Path, org: str, name: str, user: str = "sfabbro") -> bool:
    """Clone a single repository with origin=sfabbro fork and upstream=astroai (if astroai org)."""
    dest = root / org / name
    if (dest / ".git").is_dir():
        print(f"exists: {dest}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    fork_spec = f"{user}/{name}"
    canon_spec = f"{org}/{name}"

    # Prefer gh if available and authenticated
    gh_cmd = shutil.which("gh")
    if gh_cmd:
        cmd = [gh_cmd, "repo", "clone", fork_spec, str(dest)]
    else:
        cmd = ["git", "clone", f"git@github.com:{fork_spec}.git", str(dest)]

    print(f"Cloning {fork_spec} -> {dest} ...")
    res = subprocess.run(cmd, check=False)
    if res.returncode != 0:
        # If fork does not exist, fall back to cloning canonical
        print(c(f"Could not clone fork {fork_spec}; trying canonical {canon_spec}...", YELLOW))
        if gh_cmd:
            cmd = [gh_cmd, "repo", "clone", canon_spec, str(dest)]
        else:
            cmd = ["git", "clone", f"git@github.com:{canon_spec}.git", str(dest)]
        res = subprocess.run(cmd, check=False)
        if res.returncode != 0:
            print(c(f"Failed to clone {canon_spec}", RED))
            return False

    # Wire upstream for astroai repos
    if org == "astroai":
        run_git(dest, ["remote", "get-url", "upstream"])
        upstream_url = f"git@github.com:astroai/{name}.git"
        run_git(dest, ["remote", "add", "upstream", upstream_url])
        # Ensure main tracks upstream/main
        run_git(dest, ["fetch", "upstream"])
        run_git(dest, ["branch", "--set-upstream-to=upstream/main", "main"])

    print(c(f"Successfully configured {org}/{name}", GREEN))
    return True


def cmd_bootstrap(catalog: list[dict[str, str]], root: Path, tier: str = "core", targets: list[str] | None = None) -> int:
    print(f"\n{BOLD}Bootstrapping repositories under: {root}{RESET}")
    print(f"Tier: {tier}\n")

    selected = []
    if targets:
        for t in targets:
            norm = t.strip("/")
            if "/" not in norm:
                matches = [item for item in catalog if item["name"] == norm]
            else:
                matches = [item for item in catalog if item["key"] == norm]
            selected.extend(matches)
    elif tier == "core":
        selected = [item for item in catalog if item["key"] in CORE_REPOSITORIES]
    else:  # all
        selected = catalog

    success_count = 0
    for item in selected:
        ok = clone_repo(root, item["org"], item["name"])
        if ok:
            success_count += 1

    print(f"\n{c(f'Bootstrapped {success_count}/{len(selected)} repositories.', GREEN)}")
    return 0 if success_count == len(selected) else 1


def cmd_push(catalog: list[dict[str, str]], root: Path) -> int:
    """Push unpushed commits on fork branches to origin."""
    print(f"\n{BOLD}Pushing unpushed commits to origin forks...{RESET}\n")
    pushed = 0
    for item in catalog:
        st = get_repo_status(item)
        if not st.exists or not st.has_origin:
            continue
        if st.origin_ahead > 0:
            print(f"Pushing {item['key']} ({st.branch}, ahead {st.origin_ahead}) -> origin/{st.branch} ...")
            res = run_git(st.path, ["push", "-u", "origin", st.branch])
            if res.returncode == 0:
                print(c(f"  ✓ Pushed {item['key']}", GREEN))
                pushed += 1
            else:
                print(c(f"  ✗ Failed to push {item['key']}: {res.stderr.strip()}", RED))
        else:
            print(f"  {item['key']} ({st.branch}): up to date on origin")

    print(f"\n{c(f'Completed. {pushed} repos pushed.', GREEN)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Workspace & Git Synchronization Tool for AstroAI & sfabbro repositories."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root directory (defaults to $WORK, /scratch/src, or ~/src).",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # status
    subparsers.add_parser("status", help="Show tabular status of all repositories.")

    # dirty
    subparsers.add_parser("dirty", help="List repositories with uncommitted or unpushed changes.")

    # sync
    sync_p = subparsers.add_parser("sync", help="Fetch and fast-forward/rebase all repositories.")
    sync_p.add_argument("repos", nargs="*", help="Optional specific repos to sync.")

    # pull (alias of sync)
    pull_p = subparsers.add_parser("pull", help="Alias for sync.")
    pull_p.add_argument("repos", nargs="*", help="Optional specific repos to sync.")

    # push
    subparsers.add_parser("push", help="Push unpushed commits to origin forks.")

    # bootstrap
    boot_p = subparsers.add_parser("bootstrap", help="Clone repositories into canonical org layout.")
    boot_p.add_argument(
        "--tier",
        choices=["core", "all"],
        default="core",
        help="Repository tier to clone (default: core).",
    )
    boot_p.add_argument("repos", nargs="*", help="Optional explicit list of repos to clone.")

    args = parser.parse_args()
    root = args.root or resolve_workspace_root()

    catalog = load_repository_catalog(root)

    cmd = args.command or "status"
    if cmd == "status":
        return cmd_status(catalog, root)
    elif cmd == "dirty":
        return cmd_dirty(catalog, root)
    elif cmd in ("sync", "pull"):
        return cmd_sync(catalog, root, repo_filters=args.repos or None)
    elif cmd == "push":
        return cmd_push(catalog, root)
    elif cmd == "bootstrap":
        return cmd_bootstrap(catalog, root, tier=args.tier, targets=args.repos or None)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
