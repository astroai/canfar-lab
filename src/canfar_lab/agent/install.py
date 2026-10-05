"""Install and remove agent CLIs. Public names are re-exported for callers."""

from __future__ import annotations

import contextlib
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from canfar_lab.agent.install_bins import (
    BINARY_SOURCE_HOME,
    BINARY_SOURCE_LEGACY,
    BINARY_SOURCE_MANAGED,
    BINARY_SOURCE_MISSING,
    BINARY_SOURCE_OTHER,
    TOOL_BINARIES,
    TOOL_UTILITIES,
    TOOLS,
    _bin_dir,
    _is_system_sg_impostor,
    _npm_prefix,
    _path_under,
    classify_binary,
    clear_legacy_scratch_binary,
    home_bin_candidates,
    legacy_scratch_bin_roots,
    list_tools,
    list_tools_status,
    managed_bin_roots,
    refuse_if_home_owned,
    tool_binary,
    tool_on_path,
)
from canfar_lab.agent.install_fetch import (
    _curl_pipe_bash,
    _download_public_gh_release,
    _ensure_bin_dir,
    _gh_auth_ok,
    _gh_release_bin,
    _link_into_local_bin,
    _managed_share_dir,
    _npm_version_tuple,
    _require,
    _session_environ,
    _verify_cmd,
    curl_installer_environ,
    find_curl_binary,
    npm_global_install_cmd,
    npm_install_environ,
)
from canfar_lab.errors import LabError
from canfar_lab.shell.session_env import resolve_session_env
from canfar_lab.utils.subprocess import run


def install_tool(name: str, *, dry_run: bool = False) -> None:
    if name == "hyperfine":
        raise LabError(
            "hyperfine is image-baked on AstroAI base (already on PATH)",
            hint="hyperfine --version",
        )
    if name not in TOOLS:
        raise LabError(f"Unknown tool: {name}", hint="canfar lab agent list")
    refuse_if_home_owned(name)
    if dry_run:
        return
    from canfar_lab.agent.setup_state import INSTALL_TIMEOUT_SEC, agent_setup_lock

    with agent_setup_lock():
        _install_tool_locked(name, INSTALL_TIMEOUT_SEC)


def _install_tool_locked(name: str, npm_timeout: int) -> None:
    # Scratch roots + env redirects before upstream installers write state.
    from canfar_lab.core.home_layout import ensure_agent_runtime_on_scratch

    ensure_agent_runtime_on_scratch(Path.home(), dry_run=False)
    resolve_session_env(ensure=True)
    _ensure_bin_dir()
    arch = platform.machine()

    if name == "node":
        # Node LTS + npm are baked into the base image (canfar-containers), so
        # this is normally a no-op on CANFAR sessions; keep the pixi fallback
        # for bare environments where node is not already on PATH.
        if shutil.which("node") is not None and shutil.which("npm") is not None:
            return
        _require("pixi")
        session = resolve_session_env(ensure=False)
        pixi_bin = session.pixi_home / "bin"
        bin_dir = _bin_dir()
        run(["pixi", "global", "install", "nodejs"], env=_session_environ(), timeout=npm_timeout)
        for cmd in ("node", "npm", "npx"):
            src = pixi_bin / cmd
            if src.is_file():
                (bin_dir / cmd).unlink(missing_ok=True)
                (bin_dir / cmd).symlink_to(src)
        _verify_cmd("node")
        _verify_cmd("npm")
    elif name == "cursor":
        # Upstream binary remains `agent`; registry / TOOLS id is `cursor`.
        _curl_pipe_bash("https://cursor.com/install")
        found = find_curl_binary("agent")
        if found is not None:
            _link_into_local_bin(found, "agent")
        _verify_cmd("agent")
    elif name == "claude":
        _curl_pipe_bash("https://claude.ai/install.sh")
        found = find_curl_binary("claude")
        if found is not None:
            _link_into_local_bin(found, "claude")
        _verify_cmd("claude")
    elif name == "agy":
        _curl_pipe_bash("https://antigravity.google/cli/install.sh")
        found = find_curl_binary("agy")
        if found is not None:
            _link_into_local_bin(found, "agy")
        _verify_cmd("agy")
    elif name == "copilot":
        env = {"PREFIX": str(_npm_prefix()), "CI": "1"}
        with contextlib.suppress(subprocess.CalledProcessError, LabError):
            _curl_pipe_bash("https://gh.io/copilot-install", env=env)
        copilot_bin = _npm_prefix() / "bin" / "copilot"
        if not copilot_bin.is_file() and shutil.which("copilot") is None:
            _require("npm")
            run(
                npm_global_install_cmd(_npm_prefix(), "@github/copilot@latest"),
                env=npm_install_environ(),
                timeout=npm_timeout,
            )
            copilot_bin = _npm_prefix() / "bin" / "copilot"
        _link_into_local_bin(copilot_bin, "copilot")
        _verify_cmd("copilot", extra_paths=[copilot_bin])
    elif name == "hermes":
        # Nous Research Hermes Agent — self-contained installer (bootstraps its
        # own Python/uv/Node), first-class OpenRouter + headless `hermes -z`.
        _curl_pipe_bash("https://hermes-agent.nousresearch.com/install.sh")
        found = find_curl_binary("hermes")
        if found is not None:
            _link_into_local_bin(found, "hermes")
        _verify_cmd("hermes")
    elif name == "openclaw":
        # Requires Node >= 24.15 — Node 24.18.1 LTS is baked into the base image.
        _require("npm")
        run(
            npm_global_install_cmd(_npm_prefix(), "openclaw@latest"),
            env=npm_install_environ(),
            timeout=npm_timeout,
        )
        openclaw_bin = _npm_prefix() / "bin" / "openclaw"
        _link_into_local_bin(openclaw_bin, "openclaw")
        _verify_cmd("openclaw", extra_paths=[openclaw_bin])
    elif name == "qoder":
        env = {"XDG_BIN_DIR": str(_bin_dir())}
        with contextlib.suppress(subprocess.CalledProcessError, LabError):
            _curl_pipe_bash("https://qoder.com/install", env=env)
        found = find_curl_binary("qodercli")
        if found is not None:
            _link_into_local_bin(found, "qodercli")
            _link_into_local_bin(found, "qoder")
        if shutil.which("qodercli") is None and not (_bin_dir() / "qodercli").is_file():
            _require("npm")
            run(
                npm_global_install_cmd(_npm_prefix(), "@qoder-ai/qodercli@latest"),
                env=npm_install_environ(),
                timeout=npm_timeout,
            )
            npm_bin = _npm_prefix() / "bin" / "qodercli"
            _link_into_local_bin(npm_bin, "qodercli")
            _link_into_local_bin(npm_bin, "qoder")
        _verify_cmd("qodercli")
    elif name == "ast-grep":
        if arch not in ("x86_64", "aarch64"):
            raise LabError(f"Unsupported architecture: {arch}")
        asset = f"app-{arch}-unknown-linux-gnu.zip"
        _gh_release_bin("ast-grep/ast-grep", asset, "sg")
        (_bin_dir() / "ast-grep").unlink(missing_ok=True)
        (_bin_dir() / "ast-grep").symlink_to(_bin_dir() / "sg")
        _verify_cmd("sg")
    elif name == "skore-cli":
        _require("uv")
        run(
            ["uv", "tool", "install", "--force", "skore-cli"],
            env=_session_environ(),
            timeout=npm_timeout,
        )
        _verify_cmd("skore")
    else:
        raise LabError(f"Unknown tool: {name}", hint="canfar lab agent install  (or agent list)")


# ---------------------------------------------------------------------------
# Removal (Phase 2: `agent remove`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoveResult:
    target: str
    status: str  # removed | would_remove | error
    detail: str = ""


# npm package name per tool (mirrors the `npm install -g` calls in install_tool).
TOOL_NPM_PACKAGES = {
    "openclaw": "openclaw",
    "copilot": "@github/copilot",
    "qoder": "@qoder-ai/qodercli",
}

# Home-relative config files a tool owns (removed on `agent remove`).
# Registry-driven agents carry their own config.path in agents/*.yaml.
TOOL_CONFIG_PATHS = {
    "copilot": [".copilot/mcp-config.json"],
    "qoder": [".qoder/settings.json"],
    "hermes": [".hermes/config.yaml"],
    "openclaw": [".openclaw/openclaw.json"],
}


def _remove_file(path: Path, target: str, *, dry_run: bool) -> RemoveResult | None:
    """Unlink a file/symlink; None when it doesn't exist."""
    if not (path.exists() or path.is_symlink()):
        return None
    if dry_run:
        return RemoveResult(target, "would_remove", str(path))
    try:
        path.unlink(missing_ok=True)
        return RemoveResult(target, "removed", str(path))
    except OSError as exc:
        return RemoveResult(target, "error", str(exc))


def _remove_tree(path: Path, target: str, *, dry_run: bool) -> RemoveResult | None:
    """Remove a directory tree; None when it doesn't exist."""
    if not path.exists():
        return None
    if dry_run:
        return RemoveResult(target, "would_remove", str(path))
    try:
        shutil.rmtree(path)
        return RemoveResult(target, "removed", str(path))
    except OSError as exc:
        return RemoveResult(target, "error", str(exc))


def uninstall_tool(
    name: str,
    *,
    home: Path | None = None,
    purge: bool = False,
    clean_home: bool = False,
    dry_run: bool = False,
) -> list[RemoveResult]:
    """Uninstall a CLI tool: binaries, config files, plugin files, setup stamps.

    Removes managed CLIs under ``CANFAR_LAB_BIN_DIR`` / npm prefix. Home
    (``/arc``) copies under ``~/.local/bin`` and similar paths are only removed
    when ``clean_home=True``. ``purge`` removes the tool's whole home config dir.
    """
    if name not in TOOLS:
        raise LabError(f"Unknown tool: {name}", hint="canfar lab agent list")
    home = home or Path.home()
    from canfar_lab.agent.setup_state import agent_setup_lock

    with agent_setup_lock(home):
        return _uninstall_tool_locked(
            name, home=home, purge=purge, clean_home=clean_home, dry_run=dry_run
        )


def _uninstall_tool_locked(
    name: str,
    *,
    home: Path,
    purge: bool,
    clean_home: bool,
    dry_run: bool,
) -> list[RemoveResult]:
    results: list[RemoveResult] = []
    binary = tool_binary(name)
    info = classify_binary(binary, home=home)

    # 1. Managed binaries from CANFAR_LAB_BIN_DIR + npm prefix bin.
    share_root = _managed_share_dir()
    for bin_path in (_bin_dir() / binary, _npm_prefix() / "bin" / binary):
        payload = None
        if bin_path.is_symlink():
            with contextlib.suppress(OSError):
                landed = bin_path.resolve().parent
                if _path_under(landed, share_root):
                    payload = landed
        result = _remove_file(bin_path, f"binary:{binary}", dry_run=dry_run)
        if result:
            results.append(result)
        if payload is not None:
            result = _remove_tree(payload, f"payload:{payload}", dry_run=dry_run)
            if result:
                results.append(result)

    # Convenience aliases created at install time (id name ≠ binary name).
    if name == "qoder":
        result = _remove_file(_bin_dir() / "qoder", "binary:qoder", dry_run=dry_run)
        if result:
            results.append(result)
    if name == "ast-grep":
        result = _remove_file(_bin_dir() / "ast-grep", "binary:ast-grep", dry_run=dry_run)
        if result:
            results.append(result)

    # 1b. Home drop paths — only with --clean-home (scratch is canonical).
    if clean_home:
        managed_landings = {_bin_dir() / binary, _npm_prefix() / "bin" / binary}
        for home_bin in home_bin_candidates(binary, home=home):
            if home_bin in managed_landings:
                continue
            skip = False
            for m in managed_landings:
                with contextlib.suppress(OSError):
                    if (m.exists() or m.is_symlink()) and home_bin.resolve() == m.resolve():
                        skip = True
                        break
            if skip:
                continue
            result = _remove_file(home_bin, f"home-binary:{binary}", dry_run=dry_run)
            if result:
                results.append(result)
    if not dry_run:
        # Install path clears home shadows; remove only touches home with --clean-home.
        if clean_home:
            clear_legacy_scratch_binary(binary)
    else:
        for root in legacy_scratch_bin_roots():
            leg = root / binary
            if leg.is_file() or leg.is_symlink():
                results.append(RemoveResult(f"legacy-binary:{binary}", "would_remove", str(leg)))

    # 2. Best-effort npm uninstall for npm-installed tools (binary removal
    #    above is authoritative; this just cleans the node_modules tree).
    pkg = TOOL_NPM_PACKAGES.get(name)
    if pkg and not dry_run and shutil.which("npm"):
        from canfar_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

        with contextlib.suppress(LabError, subprocess.CalledProcessError, OSError):
            run(
                ["npm", "uninstall", "-g", "--prefix", str(_npm_prefix()), pkg],
                env=_session_environ(),
                timeout=INSTALL_TIMEOUT_SEC,
                quiet=True,  # keep stdout clean for `--json agent remove/wipe`
            )

    # 3. Config files owned by the tool (persistent under $HOME).
    if info["source"] != BINARY_SOURCE_MISSING or purge:
        for rel in TOOL_CONFIG_PATHS.get(name, []):
            result = _remove_file(home / rel, f"config:{rel}", dry_run=dry_run)
            if result:
                results.append(result)

        # 4. Plugin-created files for this agent (precise sweep), then any
        #    leftover ~/.<id>/skills tree.
        from canfar_lab.agent import plugins as agent_plugins

        for row in agent_plugins.remove_agent_plugin_files(name, home=home, dry_run=dry_run):
            results.append(
                RemoveResult(
                    row.get("target", f"plugins:{name}"),
                    row.get("status", "removed"),
                    row.get("detail", ""),
                )
            )
        plugin_dir = home / f".{name}" / "skills"
        result = _remove_tree(plugin_dir, f"plugins:{plugin_dir}", dry_run=dry_run)
        if result:
            results.append(result)

    # 5. Setup state stamps when tearing down an installed tool.
    if info["source"] != BINARY_SOURCE_MISSING:
        from canfar_lab.agent.setup_state import failed_path, stamp_path

        for spath, target in (
            (stamp_path(home), "state:stamp"),
            (failed_path(home), "state:failed"),
        ):
            result = _remove_file(spath, target, dry_run=dry_run)
            if result:
                results.append(result)

    # 6. --purge: remove the tool's whole home config dir (parent of config).
    if purge:
        for rel in TOOL_CONFIG_PATHS.get(name, []):
            d = (home / rel).parent
            lab_dir = home / ".astroai" / "lab"
            if d not in {home, lab_dir}:
                result = _remove_tree(d, f"purge:{d}", dry_run=dry_run)
                if result:
                    results.append(result)

    return results


__all__ = [
    "BINARY_SOURCE_HOME",
    "BINARY_SOURCE_LEGACY",
    "BINARY_SOURCE_MANAGED",
    "BINARY_SOURCE_MISSING",
    "BINARY_SOURCE_OTHER",
    "RemoveResult",
    "TOOL_BINARIES",
    "TOOL_CONFIG_PATHS",
    "TOOL_NPM_PACKAGES",
    "TOOL_UTILITIES",
    "TOOLS",
    "_bin_dir",
    "_curl_pipe_bash",
    "_download_public_gh_release",
    "_gh_auth_ok",
    "_gh_release_bin",
    "_is_system_sg_impostor",
    "_link_into_local_bin",
    "_npm_prefix",
    "_npm_version_tuple",
    "classify_binary",
    "clear_legacy_scratch_binary",
    "curl_installer_environ",
    "find_curl_binary",
    "home_bin_candidates",
    "install_tool",
    "legacy_scratch_bin_roots",
    "list_tools",
    "list_tools_status",
    "managed_bin_roots",
    "npm_global_install_cmd",
    "npm_install_environ",
    "refuse_if_home_owned",
    "tool_binary",
    "tool_on_path",
    "uninstall_tool",
]
