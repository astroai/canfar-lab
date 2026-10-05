"""Binary classification, landing paths, and legacy scratch cleanup."""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

from canfar_lab.core.paths import npm_prefix_dir, user_bin_dir
from canfar_lab.errors import LabError

TOOLS = {
    "node": "Node.js + npm (baked into base image; pixi fallback)",
    "cursor": "Cursor Agent (binary: agent)",
    "claude": "Claude Code",
    "agy": "Antigravity CLI",
    "copilot": "GitHub Copilot CLI",
    "qoder": "Qoder CLI (qodercli)",
    "hermes": "Hermes Agent (Nous Research)",
    "openclaw": "OpenClaw (openclaw/openclaw)",
    # Backend for `agent plugins install ast-grep-cli` only (not an agent).
    "ast-grep": "ast-grep (sg)",
    # Backend for `agent plugins install skore-cli` (binary: skore).
    "skore-cli": "Skore CLI (skore — skills + Skore Hub)",
}

# TOOLS entries that are not coding agents (no agents/*.yaml registry row).
# hyperfine is image-baked — do not list or reinstall it.
TOOL_UTILITIES = frozenset({"node", "ast-grep", "skore-cli"})

# CLI binary name when it differs from the install tool key.
TOOL_BINARIES = {
    "ast-grep": "sg",
    "skore-cli": "skore",
    "qoder": "qodercli",
    "cursor": "agent",  # upstream Cursor Agent binary is still named `agent`
}

# Where an on-disk CLI came from relative to lab management.
BINARY_SOURCE_MANAGED = "managed"  # under CANFAR_LAB_BIN_DIR / npm prefix (scratch)
BINARY_SOURCE_HOME = "home"  # under $HOME (/arc/home) — user-owned, not managed
BINARY_SOURCE_OTHER = "other"  # elsewhere on PATH
BINARY_SOURCE_MISSING = "missing"
# Kept for call-site compatibility; scratch is managed again, not "legacy".
BINARY_SOURCE_LEGACY = "legacy"


def _bin_dir() -> Path:
    # Tests patch ``canfar_lab.agent.install._bin_dir``. Honor that binding.
    from canfar_lab.agent import install as install_mod

    override = install_mod.__dict__.get("_bin_dir")
    if override is not None and override is not _bin_dir:
        return override()
    return user_bin_dir()


def _npm_prefix() -> Path:
    from canfar_lab.agent import install as install_mod

    override = install_mod.__dict__.get("_npm_prefix")
    if override is not None and override is not _npm_prefix:
        return override()
    return npm_prefix_dir()


def list_tools() -> dict[str, str]:
    return dict(TOOLS)


def tool_binary(name: str) -> str:
    if name in TOOL_BINARIES:
        return TOOL_BINARIES[name]
    # Registry agents may declare a different binary (augment → auggie).
    try:
        from canfar_lab.agent.registry import get_registry_agent

        agent = get_registry_agent(name)
        if agent is not None and agent.get("binary"):
            return str(agent["binary"])
    except (ImportError, KeyError, TypeError, ValueError):
        pass
    return name


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _resolve_session_env(*, ensure: bool = False):
    """Use ``install.resolve_session_env`` so a test patch on that facade applies."""
    from canfar_lab.agent import install as install_mod

    return install_mod.resolve_session_env(ensure=ensure)


def managed_bin_roots() -> list[Path]:
    """Dirs where canfar-lab owns agent CLIs (scratch / session, never $HOME)."""
    session = _resolve_session_env(ensure=False)
    # Include `_bin_dir()` / `_npm_prefix()` so test monkeypatches and the
    # live session resolver always agree on "managed".
    roots = [
        session.canfar_lab_bin_dir,
        session.canfar_lab_npm_prefix / "bin",
        _bin_dir(),
        _npm_prefix() / "bin",
    ]
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        try:
            key = root.resolve()
        except OSError:
            key = root
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def legacy_scratch_bin_roots() -> list[Path]:
    """No-op list: scratch ``.local/bin`` is managed again, not legacy.

    Kept so older call sites / tests that import the name keep working.
    """
    return []


def home_bin_candidates(binary: str, *, home: Path | None = None) -> list[Path]:
    """Typical user-owned CLI locations under $HOME (/arc/home on CANFAR)."""
    home = home or Path.home()
    return [
        home / ".local" / "bin" / binary,
        home / f".{binary}" / "bin" / binary,
        home / ".npm-global" / "bin" / binary,
        home / ".kilo" / "bin" / binary,
        home / ".opencode" / "bin" / binary,
        home / ".hermes" / "bin" / binary,
        home / ".agy" / "bin" / binary,
    ]


def classify_binary(
    binary: str,
    *,
    home: Path | None = None,
) -> dict[str, object]:
    """Locate a CLI and classify ownership for list/install/remove policy.

    Config may live on ``$HOME`` (/arc/home); managed binaries live under
    ``CANFAR_LAB_BIN_DIR`` (scratch). A home-tree CLI is user-owned: lab will
    not install/overwrite it, but ``agent remove --clean-home`` can delete it.

    Special case: Linux ``/usr/bin/sg`` is shadow-utils ``newgrp``, not
    ast-grep — treat it as missing unless a managed/home ``sg`` or ``ast-grep``
    is present.
    """
    home = home or Path.home()
    managed_roots = managed_bin_roots()
    managed_hit: Path | None = None
    for root in managed_roots:
        candidate = root / binary
        if candidate.is_file():
            managed_hit = candidate
            break
        if binary == "sg":
            alt = root / "ast-grep"
            if alt.is_file():
                managed_hit = alt
                break

    home_hit = next(
        (p for p in home_bin_candidates(binary, home=home) if p.is_file()),
        None,
    )
    if home_hit is None and binary == "sg":
        home_hit = next(
            (p for p in home_bin_candidates("ast-grep", home=home) if p.is_file()),
            None,
        )

    which = shutil.which(binary)
    which_path = Path(which) if which else None
    if which_path is not None and binary == "sg" and _is_system_sg_impostor(which_path):
        which_path = None
        alt_which = shutil.which("ast-grep")
        if alt_which is not None:
            which_path = Path(alt_which)

    # Prefer managed (scratch) over home-owned over PATH other.
    if managed_hit is not None:
        path = managed_hit
        source = BINARY_SOURCE_MANAGED
    elif home_hit is not None:
        path = home_hit
        source = BINARY_SOURCE_HOME
    elif which_path is not None and _path_under(which_path, home):
        path = which_path
        source = BINARY_SOURCE_HOME
    elif which_path is not None:
        path = which_path
        source = BINARY_SOURCE_OTHER
    else:
        path = None
        source = BINARY_SOURCE_MISSING

    return {
        "binary": binary,
        "path": str(path) if path else None,
        "source": source,
        "managed": source == BINARY_SOURCE_MANAGED,
        "home_install": home_hit is not None
        or (which_path is not None and _path_under(which_path, home)),
        "home_path": str(home_hit) if home_hit else None,
        "legacy": False,
        "legacy_path": None,
    }


def _is_system_sg_impostor(path: Path) -> bool:
    """True when ``path`` is the Linux shadow-utils ``sg`` (newgrp), not ast-grep."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved.name == "newgrp":
        return True
    text = str(resolved)
    return resolved.name == "sg" and (
        text in ("/usr/bin/sg", "/bin/sg", "/usr/sbin/sg")
        or text.endswith(("/usr/bin/sg", "/bin/sg"))
    )


def refuse_if_home_owned(name: str, *, home: Path | None = None) -> None:
    """Block install when the CLI already lives under ``$HOME`` (/arc).

    Also clears unsafe symlink landings at expected paths so curl|bash cannot
    write through them into arbitrary home files.
    """
    for path in _install_landing_paths(name, home=home):
        _clear_unsafe_landing(path)
    binary = tool_binary(name)
    info = classify_binary(binary, home=home)
    if info["managed"]:
        return
    if not info["home_install"]:
        return
    where = info.get("home_path") or info.get("path") or f"~/.local/bin/{binary}"
    raise LabError(
        f"{name} is already installed under your home ({where}). "
        "astroai manages CLIs on $SCRATCH ($CANFAR_LAB_BIN_DIR), not /arc/home.",
        hint=f"canfar lab agent remove {name} --clean-home   # then: agent install {name}",
    )


def _install_landing_paths(name: str, *, home: Path | None = None) -> list[Path]:
    """Paths an upstream installer or shim step may overwrite for *name*."""
    home = home or Path.home()
    binary = tool_binary(name)
    seen: set[Path] = set()
    out: list[Path] = []
    candidates = [
        user_bin_dir() / binary,
        npm_prefix_dir() / "bin" / binary,
        *home_bin_candidates(binary, home=home),
    ]
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    if binary == "sg":
        for path in [
            user_bin_dir() / "ast-grep",
            npm_prefix_dir() / "bin" / "ast-grep",
            *home_bin_candidates("ast-grep", home=home),
        ]:
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def _clear_unsafe_landing(path: Path) -> None:
    """Unlink symlink landings; refuse directories / special files at the path."""
    if path.is_symlink():
        path.unlink()
        return
    if not path.exists():
        return
    if path.is_dir():
        raise LabError(
            f"Install target is a directory: {path}",
            hint="Move or remove it, then retry the install",
        )
    if not path.is_file():
        raise LabError(
            f"Install target is not a regular file: {path}",
            hint="Remove it, then retry the install",
        )


def _unlink_landing(path: Path) -> None:
    """Remove a bin landing without following symlinks (safe before copy/shim)."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        raise LabError(
            f"Cannot replace install target: {path}",
            hint="Remove it, then retry",
        )


def clear_legacy_scratch_binary(binary: str) -> None:
    """Clear leftover ``~/.local/bin/<binary>`` from the brief home-canonical policy.

    Scratch ``$SCRATCH/.local/bin`` is managed again; home copies compete on PATH
    over NFS and must not shadow the scratch install.
    """
    home = Path.home()
    managed = {_bin_dir(), _npm_prefix() / "bin"}
    for root in (home / ".local" / "bin",):
        try:
            if any(root.resolve() == m.resolve() for m in managed if m.exists()):
                continue
        except OSError:
            pass
        for name in (binary, "ast-grep" if binary == "sg" else None):
            if not name:
                continue
            path = root / name
            if not (path.is_file() or path.is_symlink()):
                continue
            with contextlib.suppress(OSError):
                path.unlink()


def tool_on_path(name: str) -> bool:
    """True when any copy of the binary is available (managed, home, or PATH)."""
    binary = tool_binary(name)
    info = classify_binary(binary)
    if info["source"] != BINARY_SOURCE_MISSING:
        return True
    session = _resolve_session_env(ensure=False)
    candidates = [
        session.canfar_lab_bin_dir / binary,
        session.canfar_lab_npm_prefix / "bin" / binary,
    ]
    return any(path.is_file() and os.access(path, os.X_OK) for path in candidates)


def list_tools_status() -> list[dict[str, object]]:
    """Installable tools with whether their binary is currently available."""
    rows: list[dict[str, object]] = []
    for name, desc in TOOLS.items():
        binary = tool_binary(name)
        info = classify_binary(binary)
        rows.append(
            {
                "name": name,
                "binary": binary,
                "description": desc,
                "installed": info["source"] != BINARY_SOURCE_MISSING,
                "source": info["source"],
                "managed": info["managed"],
                "home_install": info["home_install"],
                "path": info["path"],
            }
        )
    return rows
