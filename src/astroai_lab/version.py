"""Package version plus the git commit when installed from a git URL.

Images and in-session ``upgrade-cadc-tools.sh`` install
``astroai-lab @ git+https://github.com/astroai/canfar-lab.git@<sha>``.
PEP 610 ``direct_url.json`` records that commit, so ``astroai --version``
can distinguish two builds that still share the same marketing number.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, distribution
from typing import Any

# Marketing / pyproject version. Bump when the CLI contract changes.
PACKAGE_VERSION = "0.5.0"
__version__ = PACKAGE_VERSION


def _direct_url_commit() -> str | None:
    try:
        dist = distribution("astroai-lab")
    except PackageNotFoundError:
        return None
    text = dist.read_text("direct_url.json")
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    vcs = data.get("vcs_info") if isinstance(data, dict) else None
    if not isinstance(vcs, dict):
        return None
    commit = vcs.get("commit_id")
    if isinstance(commit, str) and len(commit) >= 7:
        return commit
    return None


def display_version() -> str:
    """``0.4.0+g2f7e99de`` when the wheel came from git, else ``0.4.0``."""
    base = PACKAGE_VERSION
    commit = _direct_url_commit()
    if not commit:
        return base
    short = commit[:8]
    return f"{base}+g{short}"


def version_info() -> dict[str, Any]:
    commit = _direct_url_commit()
    return {
        "version": PACKAGE_VERSION,
        "commit": commit,
        "display": display_version(),
    }
