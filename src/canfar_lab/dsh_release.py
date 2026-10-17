"""dsh release resolution: pin, npm dist-tags, newest-across-tags, install.

``canfar lab studio`` and ``canfar lab desk`` both need to know which harness
release to use without giving up reproducibility:

* ``pin`` — the version this repo ships and tests (``studio.DSH_VERSION``).
* ``alpha`` / ``next`` / ``latest`` — the npm dist-tag by that name.
* ``newest`` — highest semver across every dist-tag, prereleases included.
  The npm ``latest`` tag can lag the newer channels (as of 2026-10 the Studio
  image installs ``@alpha``); ``newest`` is the honest "latest".
* any exact version string — used as-is.

Resolution needs the network; failures fall back to the pin with
``source="offline"`` so an offline laptop still boots.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

NPM_PACKAGE = "@deepseek-ai/dsh"
REGISTRY_TAGS_URL = f"https://registry.npmjs.org/-/package/{NPM_PACKAGE}/dist-tags"
TAG_SPECS = ("latest", "next", "alpha")

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


@dataclass(frozen=True)
class Release:
    version: str
    source: str  # pin | alpha | next | latest | newest | exact | offline
    note: str = ""


def semver_key(version: str) -> tuple:
    """Sort key for semver, prereleases included (a release outranks them)."""
    match = _SEMVER_RE.match(version.strip())
    if not match:
        return (-1, -1, -1, (0,))
    major, minor, patch, pre = match.groups()
    if pre is None:
        pre_key: tuple = (1,)  # a final release outranks any prerelease
    else:
        tokens: list[tuple] = []
        for part in pre.split("."):
            if part.isdigit():
                tokens.append((0, int(part), ""))
            else:
                tokens.append((1, 0, part))
        pre_key = (0, *tokens)
    return (int(major), int(minor), int(patch), pre_key)


def _pinned() -> str:
    from canfar_lab.studio import DSH_VERSION

    return DSH_VERSION


def fetch_dist_tags(timeout: float = 5.0) -> dict[str, str]:
    """npm dist-tags for the harness. Raises on network/parse failure."""
    import httpx

    resp = httpx.get(REGISTRY_TAGS_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("unexpected dist-tags payload")
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}


def _try_tags(fetcher) -> dict[str, str] | None:  # noqa: ANN001
    try:
        return (fetcher or fetch_dist_tags)()
    except Exception:  # noqa: BLE001 — offline is a supported state
        return None


def resolve(spec: str | None = None, *, fetcher=fetch_dist_tags) -> Release:  # noqa: ANN001
    """Resolve a user-facing spec: ``pin|alpha|next|latest|newest|<version>``."""
    pinned = _pinned()
    wanted = (spec or "pin").strip()
    if wanted in ("", "pin", "pinned"):
        return Release(pinned, "pin")
    if wanted == "newest":
        tags = _try_tags(fetcher)
        if tags is None:
            return Release(pinned, "offline", note="registry unreachable; using pin")
        best = max(tags.values(), key=semver_key)
        if semver_key(best) <= semver_key(pinned):
            return Release(pinned, "pin", note=f"registry newest is {best}")
        return Release(best, "newest")
    if wanted in TAG_SPECS:
        tags = _try_tags(fetcher)
        if tags is None:
            return Release(pinned, "offline", note=f"registry unreachable; {wanted} unresolved")
        version = tags.get(wanted)
        if not version:
            return Release(pinned, "offline", note=f"no `{wanted}` tag on the registry")
        return Release(version, wanted)
    return Release(wanted, "exact")


def installed_version(binary: str | None) -> str | None:
    """Version of the dsh npm package behind ``binary``, when detectable."""
    if not binary:
        return None
    real = Path(os.path.realpath(binary))
    for parent in list(real.parents)[:8]:
        manifest = parent / "package.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and data.get("name") == NPM_PACKAGE:
            version = data.get("version")
            return str(version) if version else None
        return None
    return None


def npm_binary() -> str | None:
    return shutil.which("npm")


def install_command(version: str) -> list[str]:
    return ["npm", "install", "-g", f"{NPM_PACKAGE}@{version}"]


def has_update(installed: str | None, release: Release) -> bool:
    """True when the installed build should be replaced by ``release``."""
    if not installed:
        return True
    if release.source == "pin":
        return installed != release.version
    return semver_key(installed) < semver_key(release.version)


__all__ = [
    "NPM_PACKAGE",
    "REGISTRY_TAGS_URL",
    "TAG_SPECS",
    "Release",
    "fetch_dist_tags",
    "has_update",
    "install_command",
    "installed_version",
    "npm_binary",
    "resolve",
    "semver_key",
]
