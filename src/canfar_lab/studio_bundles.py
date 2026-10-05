"""Studio bundle discovery and install."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from canfar_lab.studio_paths import (
    ASTROAI_EXTRA_BUNDLES,
    BAKED_PLUGINS_DIR,
    BAKED_PLUGINS_ENV,
    BRAND_BUNDLE,
    IN_BOX_BUNDLES,
    OPENCODE_SESSION_BUNDLE,
    PROFILE_MANIFEST_FILENAME,
    RETIRED_BUNDLES,
    TEAM_BUNDLES,
    WEB_TEMPLATE_BUNDLES,
)


def desired_bundles(
    existing: Iterable[str],
    *,
    with_team: bool,
    required: Iterable[str] = WEB_TEMPLATE_BUNDLES,
    baked: Iterable[str] = (),
) -> list[str]:
    """Normalize a profile's bundle list to the required prefix, then extras.

    ``dsh plugin add`` appends each new dependency to the manifest in install
    order, which is not necessarily the layer order the composition needs, so
    every write goes through this function and the doctor checks the result.

    The ``required`` prefix (the shipped web composition the Studio profile is
    built on) and the Team layers are always present and always first; bundles a
    user added themselves keep their relative order after them, and are never
    dropped. Studio is a web-based profile, so a manifest that lost ``web-app``
    is repaired rather than honoured. Image-baked plugins follow the AstroAI
    extras.
    """
    wanted = [
        *required,
        *(TEAM_BUNDLES if with_team else ()),
        *ASTROAI_EXTRA_BUNDLES,
        *(name for name in baked if name not in ASTROAI_EXTRA_BUNDLES),
    ]
    extras = [
        name
        for name in existing
        if name not in wanted
        and (with_team or name not in TEAM_BUNDLES)
        and name not in ASTROAI_EXTRA_BUNDLES
        and name not in RETIRED_BUNDLES
    ]
    return wanted + extras


def team_bundles_missing(existing: list[str], *, with_team: bool) -> list[str]:
    """Team bundles that should be declared in the manifest but are not."""
    if not with_team:
        return []
    return [name for name in TEAM_BUNDLES if name not in existing]


_VENDORED_PURPOSE = {
    OPENCODE_SESSION_BUNDLE: "x-opencode-session header",
    BRAND_BUNDLE: "AstroAI branding",
}


def vendored_plugin(name: str) -> Path:
    """Packaged plugin tree under ``data/studio/plugins`` (offline CANFAR / --no-install)."""
    return Path(__file__).resolve().parent / "data" / "studio" / "plugins" / name


def ensure_vendored_plugin(
    profile_dir: Path,
    name: str,
    *,
    dry_run: bool = False,
) -> str | None:
    """Copy one vendored AstroAI plugin into the Studio profile's ``node_modules``.

    Always runs — even under ``--no-install`` — because it needs no pnpm or
    network.
    """
    import shutil

    src = vendored_plugin(name)
    if not (src / "package.json").is_file() or not (src / "lib" / "index.js").is_file():
        return None
    dest = profile_dir / "node_modules" / name
    if dry_run:
        return f"would install {name} → {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return f"installed {name} ({_VENDORED_PURPOSE.get(name, 'vendored')})"


def vendored_dependencies() -> dict[str, str]:
    """``file:`` pins for the vendored extras.

    pnpm removes whatever in ``node_modules`` the manifest does not declare, so
    without these the next ``dsh plugin add`` (or a market install) deletes the
    copies :func:`ensure_vendored_plugin` made and the profile no longer boots.
    """
    return {
        name: f"file:{vendored_plugin(name)}"
        for name in ASTROAI_EXTRA_BUNDLES
        if (vendored_plugin(name) / "package.json").is_file()
    }


def baked_plugins_root(env: dict[str, str] | None = None) -> Path | None:
    """The image's baked plugin project, when there is one."""
    environ = env if env is not None else os.environ
    raw = environ.get(BAKED_PLUGINS_ENV, "").strip()
    root = Path(raw).expanduser() if raw else BAKED_PLUGINS_DIR
    return root if (root / PROFILE_MANIFEST_FILENAME).is_file() else None


def baked_bundles(root: Path | None) -> dict[str, str]:
    """Bundle name → version spec for each baked dependency that is a dsh bundle."""
    from canfar_lab.studio_layer import read_manifest

    if root is None:
        return {}
    dependencies = read_manifest(root / PROFILE_MANIFEST_FILENAME).get("dependencies")
    if not isinstance(dependencies, dict):
        return {}
    found: dict[str, str] = {}
    for name, spec in dependencies.items():
        package = read_manifest(root / "node_modules" / name / PROFILE_MANIFEST_FILENAME)
        bundle = (package.get("dsh") or {}).get("bundle")
        if isinstance(bundle, dict) and bundle.get("patch"):
            found[str(name)] = str(spec)
    return found


def _package_dirs(node_modules: Path) -> list[Path]:
    """Top-level package directories, with scoped packages one level down."""
    found: list[Path] = []
    if not node_modules.is_dir():
        return found
    for entry in sorted(node_modules.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if entry.name.startswith("@"):
            found.extend(child for child in sorted(entry.iterdir()) if child.is_dir())
        else:
            found.append(entry)
    return found


def ensure_baked_plugins(
    profile_dir: Path,
    root: Path | None,
    bundles: Iterable[str],
    *,
    dry_run: bool = False,
) -> str | None:
    """Copy the baked plugins, and the packages they need, into the profile.

    Only what the profile lacks is copied: a plugin the person updated or a
    dependency pnpm already manages is theirs. Needs no pnpm or network, so it
    runs under ``--no-install`` like :func:`ensure_vendored_plugin`.
    """
    if root is None:
        return None
    source = root / "node_modules"
    dest_root = profile_dir / "node_modules"
    missing = [name for name in bundles if not (dest_root / name).is_dir()]
    if not missing:
        return None
    if dry_run:
        return "would install baked plugins: " + ", ".join(missing)
    for package in _package_dirs(source):
        dest = dest_root / package.relative_to(source)
        if not os.path.lexists(dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(package, dest, symlinks=True)
    return "installed baked plugins: " + ", ".join(missing)


def bundle_installed(directory: Path, name: str) -> bool:
    """Whether a bundle resolves for a profile.

    Either pnpm installed it into the profile's own ``node_modules``, or the
    launcher's module-fallback position (``$DSH_HOME/profiles/node_modules``,
    one level up) carries it because the installation depends on it.
    """
    for base in (directory / "node_modules", directory.parent / "node_modules"):
        if (base / Path(name) / "package.json").is_file():
            return True
    return False


def out_of_tree_bundles(bundles: Iterable[str]) -> list[str]:
    """Bundles the profile has to install itself (everything but the in-box set).

    AstroAI extras are vendored and copied by :func:`ensure_vendored_plugin`;
    pnpm only sees them as ``file:`` dependencies when it installs something else.
    """
    return [
        name for name in bundles if name not in IN_BOX_BUNDLES and name not in ASTROAI_EXTRA_BUNDLES
    ]


def uninstalled_bundles(directory: Path, bundles: Iterable[str]) -> list[str]:
    """Declared out-of-tree packs that still need a ``dsh plugin add`` (pnpm)."""
    return [name for name in out_of_tree_bundles(bundles) if not bundle_installed(directory, name)]
