"""Repository catalog resolution for the sync engine.

Precedence (docs/desk-sync.md §5.3): an explicit ``--repos-file`` (or the
workspace ``workspace.toml``) wins; cloned org trees beside it are merged in;
otherwise the built-in ``DEFAULT_REPOSITORIES`` list is used. The engine only
ever touches repos in the resolved catalog.
"""

from __future__ import annotations

import contextlib
import getpass
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from canfar_lab.errors import LabError

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

#: Fast-bootstrap tier for a fresh session scratch (workspace-sync.py keeps the
#: same list for images without this CLI).
CORE_REPOSITORIES: list[str] = [
    "astroai/torchsky",
    "astroai/uspm",
    "astroai/cosmodist",
    "astroai/torchfits",
    "astroai/canfar-lab",
    "astroai/canfar-skills",
]

WORKSPACE_TOML = "workspace.toml"


def detect_github_user() -> str | None:
    """Detect the current GitHub username dynamically without hardcoding."""
    for var in ("GH_USER", "GITHUB_USER"):
        val = os.environ.get(var, "").strip()
        if val:
            return val

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


def find_workspace_toml(root: Path) -> Path | None:
    """First workspace catalog: ``root``, cwd, then ``~/src``."""
    candidates = [
        root / WORKSPACE_TOML,
        Path.cwd() / WORKSPACE_TOML,
        Path.home() / "src" / WORKSPACE_TOML,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_toml(path: Path) -> dict[str, Any]:
    from canfar_lab.utils.toml_compat import tomllib

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return _parse_toml_fallback(path)
    return data if isinstance(data, dict) else {}


def _parse_toml_fallback(path: Path) -> dict[str, Any]:
    """Minimal ``[repositories."org/name"]`` parser when tomllib is absent."""
    repos: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("[repositories.") and line.endswith("]"):
            current = line[len("[repositories.") : -1].strip().strip('"').strip("'")
            repos[current] = {}
        elif current and "=" in line:
            key, _, value = line.partition("=")
            repos[current][key.strip()] = value.strip().strip('"').strip("'")
    return {"repositories": repos}


def _catalog_from_workspace(path: Path) -> list[str]:
    data = _load_toml(path)
    table = data.get("repositories")
    if not isinstance(table, dict):
        return []
    repos: list[str] = []
    for key, info in table.items():
        slug = str(key)
        if "/" not in slug:
            continue
        lifecycle = "active"
        if isinstance(info, dict):
            lifecycle = str(info.get("lifecycle") or "active")
        if lifecycle in ("archived", "quarantine"):
            continue
        if slug not in repos:
            repos.append(slug)
    return repos


def scan_clones(root: Path) -> list[str]:
    """Slugs for existing ``<root>/<org>/<name>`` git checkouts."""
    found: list[str] = []
    if not root.is_dir():
        return found
    for org_dir in sorted(root.iterdir()):
        if not org_dir.is_dir() or org_dir.name.startswith("."):
            continue
        for child in sorted(org_dir.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                found.append(f"{org_dir.name}/{child.name}")
    return found


def load_catalog(root: Path, *, repos_file: Path | None = None) -> list[str]:
    """Resolved repository slugs for a workspace root."""
    if repos_file is not None:
        explicit = Path(repos_file).expanduser()
        if not explicit.is_file():
            raise LabError(f"repos file not found: {explicit}")
        repos = _catalog_from_workspace(explicit)
        if not repos:
            raise LabError(
                f"no repositories found in {explicit}",
                hint='expects [repositories."org/name"] tables',
            )
        return repos

    workspace = find_workspace_toml(root)
    if workspace is not None:
        repos = _catalog_from_workspace(workspace)
        for slug in scan_clones(root):
            if slug not in repos:
                repos.append(slug)
        if repos:
            return repos
    return list(DEFAULT_REPOSITORIES)


def normalize_selection(catalog: list[str], selection: list[str] | None) -> list[str]:
    """Expand ``--repos`` filters (short names allowed) against the catalog."""
    if not selection:
        return list(catalog)
    out: list[str] = []
    for raw in selection:
        want = str(raw).strip().strip("/")
        if not want:
            continue
        matches = (
            [want] if "/" in want else [slug for slug in catalog if slug.split("/")[-1] == want]
        )
        for slug in matches or [want]:
            if slug not in out:
                out.append(slug)
    return out


def repo_path(root: Path, slug: str) -> Path:
    """``<root>/<org>/<name>`` (flat fallback for odd slugs)."""
    org, _, name = slug.partition("/")
    if org and name:
        return root / org / name
    return root / slug


__all__ = [
    "CORE_REPOSITORIES",
    "DEFAULT_REPOSITORIES",
    "DEFAULT_SCIENCE_REPOSITORIES",
    "WORKSPACE_TOML",
    "detect_github_user",
    "find_workspace_toml",
    "load_catalog",
    "normalize_selection",
    "repo_path",
    "scan_clones",
]
