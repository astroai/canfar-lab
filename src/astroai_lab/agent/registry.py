"""Agent list entries: YAML under ``data/agent/agents/*.yaml``.

One file per installable agent drives ``agent list``, ``agent install``,
``agent remove``, and ``agent verify``. The schema is validated on load so a
bad entry fails loudly instead of silently degrading the CLI.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

import yaml

from astroai_lab.agent.agent_targets import AGENT_SKILL_DIRS, expand_home, mcp_target
from astroai_lab.agent.bundle_path import bundle_root
from astroai_lab.errors import LabError

INSTALL_METHODS = ("npm", "curl", "gh-release", "uv-tool")
REQUIRED_KEYS = ("id", "name", "homepage", "binary", "install")


def _agents_dir(root: Path | None = None) -> Path:
    return (root or bundle_root()) / "agents"


def _validate(data: dict[str, Any], source: Path) -> dict[str, Any]:
    """Validate + normalize a single registry entry; raise LabError on problems."""
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise LabError(
            f"Agent registry entry {source.name} missing required key(s): {', '.join(missing)}"
        )
    install = data.get("install") or {}
    method = install.get("method")
    if method not in INSTALL_METHODS:
        raise LabError(
            f"Agent {data['id']} has invalid install.method={method!r} "
            f"(expected one of {', '.join(INSTALL_METHODS)}) in {source.name}"
        )
    if method in ("npm", "curl", "uv-tool") and not install.get("source"):
        raise LabError(
            f"Agent {data['id']} install.method={method} requires install.source in {source.name}"
        )
    if method == "gh-release" and not (install.get("repo") and install.get("asset")):
        raise LabError(
            f"Agent {data['id']} install.method=gh-release requires "
            f"install.repo and install.asset in {source.name}"
        )
    if data.get("config") and not data["config"].get("path"):
        raise LabError(f"Agent {data['id']} config requires config.path in {source.name}")
    return data


def load_registry(root: Path | None = None) -> list[dict[str, Any]]:
    """Load + validate every ``agents/*.yaml`` entry, sorted by id."""
    d = _agents_dir(root)
    if not d.is_dir():
        return []
    agents: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise LabError(f"Invalid YAML in agent registry {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise LabError(f"Agent registry entry must be a mapping: {path}")
        agents.append(_validate(raw, path))
    return agents


def list_registry_agents(root: Path | None = None) -> list[dict[str, Any]]:
    return load_registry(root)


def get_registry_agent(agent_id: str, root: Path | None = None) -> dict[str, Any] | None:
    for agent in load_registry(root):
        if agent["id"] == agent_id:
            return agent
    return None


def registry_ids(root: Path | None = None) -> set[str]:
    return {a["id"] for a in load_registry(root)}


_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]+)?)")

# Large Go/Node agent CLIs often need >1s just to print --version (kilo ~1.9s,
# cline ~1.4s on a warm box). 0.8s timed out → blank Version column in
# `agent list`. Override with ASTROAI_LAB_PROBE_VERSION_TIMEOUT if needed.
_DEFAULT_PROBE_TIMEOUT_SEC = 3.0


def _probe_timeout_sec(override: float | None = None) -> float:
    import os

    if override is not None:
        return override
    raw = os.environ.get("ASTROAI_LAB_PROBE_VERSION_TIMEOUT", "").strip()
    if raw:
        try:
            return max(0.2, float(raw))
        except ValueError:
            pass
    return _DEFAULT_PROBE_TIMEOUT_SEC


def resolve_agent_binary(binary: str) -> str | None:
    """Absolute path to an agent CLI, matching list/status classification order.

    Prefer a managed (scratch) or home-owned copy from ``classify_binary`` so
    version/launch probes hit the same binary the Binary column marks ✓ for —
    not a different same-name tool earlier on PATH.
    """
    import os
    import shutil

    from astroai_lab.agent.install import classify_binary
    from astroai_lab.shell.session_env import resolve_session_env

    info = classify_binary(binary)
    classified = info.get("path")
    if classified and Path(str(classified)).is_file() and os.access(str(classified), os.X_OK):
        return str(classified)

    resolved = shutil.which(binary)
    if resolved is not None:
        return resolved
    session = resolve_session_env(ensure=False)
    for candidate in (
        session.astroai_lab_bin_dir / binary,
        session.astroai_lab_npm_prefix / "bin" / binary,
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def probe_version(
    binary: str,
    *,
    timeout: float | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """Best-effort installed version from ``binary <args>`` (default ``--version``).

    Returns the first semver-ish token, or None when the binary is missing /
    hangs / prints nothing parseable. Default timeout is 3s (cold Go/Node
    CLIs); set ``ASTROAI_LAB_PROBE_VERSION=0`` to skip, or
    ``ASTROAI_LAB_PROBE_VERSION_TIMEOUT`` to override seconds. Per-agent
    overrides live in registry YAML under ``version:``.
    """
    import os
    import subprocess

    # Skip probes in unit tests unless explicitly enabled (avoids hung CLIs).
    if os.environ.get("ASTROAI_LAB_PROBE_VERSION", "1") in ("0", "false", "no"):
        return None

    cmd = resolve_agent_binary(binary)
    if cmd is None:
        return None
    argv = [cmd, *(args or ["--version"])]
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_probe_timeout_sec(timeout),
            check=False,
            stdin=subprocess.DEVNULL,
            env=run_env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def _version_probe_opts(
    agent: dict[str, Any], *, timeout: float | None = None
) -> tuple[str, list[str] | None, dict[str, str] | None, float | None]:
    """Resolve binary name + argv/env/timeout from registry ``version:``."""
    from astroai_lab.agent.install import TOOLS, tool_binary

    probe_name = tool_binary(agent["id"]) if agent["id"] in TOOLS else str(agent["binary"])
    version_cfg = agent.get("version") or {}
    args = version_cfg.get("args")
    if args is not None and not isinstance(args, list):
        args = None
    env_raw = version_cfg.get("env") or {}
    env = {str(k): str(v) for k, v in env_raw.items()} if isinstance(env_raw, dict) else None
    agent_timeout = version_cfg.get("timeout")
    if agent_timeout is not None:
        with contextlib.suppress(TypeError, ValueError):
            timeout = float(agent_timeout)
    return probe_name, args, env, timeout


def probe_agent_version(agent: dict[str, Any], *, timeout: float | None = None) -> str | None:
    """Version probe honoring registry ``version.args`` / ``version.env``."""
    probe_name, args, env, timeout = _version_probe_opts(agent, timeout=timeout)
    return probe_version(probe_name, timeout=timeout, args=args, env=env)


def probe_launch(
    binary: str,
    *,
    timeout: float | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """Return an error string if ``binary <args>`` cannot run, else None.

    Default args are ``--version``. Used by ``agent verify`` so a present-but-
    broken CLI fails the gate. Honors the same registry ``version.args`` /
    ``version.env`` overrides as ``probe_agent_version`` via
    ``probe_agent_launch``. Skipped when ``ASTROAI_LAB_PROBE_VERSION=0``.
    """
    import os
    import subprocess

    if os.environ.get("ASTROAI_LAB_PROBE_VERSION", "1") in ("0", "false", "no"):
        return None

    cmd = resolve_agent_binary(binary)
    if cmd is None:
        return f"not found ({binary})"
    argv_tail = args or ["--version"]
    label = " ".join(argv_tail)
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    try:
        proc = subprocess.run(
            [cmd, *argv_tail],
            capture_output=True,
            text=True,
            timeout=_probe_timeout_sec(timeout),
            check=False,
            stdin=subprocess.DEVNULL,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        return f"{binary} hung on {label}"
    except OSError as exc:
        return f"{binary} failed to launch: {exc}"
    text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0 and not text:
        return f"{binary} {label} exited {proc.returncode}"
    if proc.returncode != 0 and not _VERSION_RE.search(text):
        # Non-zero with no version token — treat as launch failure (e.g. bad config).
        detail = text.splitlines()[0][:120] if text else f"exit {proc.returncode}"
        return f"{binary} {label} failed ({detail})"
    return None


def probe_agent_launch(agent: dict[str, Any], *, timeout: float | None = None) -> str | None:
    """Launch smoke-test honoring registry ``version.args`` / ``version.env``."""
    probe_name, args, env, timeout = _version_probe_opts(agent, timeout=timeout)
    return probe_launch(probe_name, timeout=timeout, args=args, env=env)


def _path_has_state(path: Path) -> bool:
    """True when path is a file or a non-empty directory."""
    try:
        if path.is_file() or path.is_symlink():
            return path.exists()
        if path.is_dir():
            return any(path.iterdir())
    except OSError:
        return False
    return False


def config_state_paths(agent: dict[str, Any], home: Path) -> list[Path]:
    """On-disk locations that mean this agent has been configured or logged in.

    Declared ``config.path`` is the settings file ``agent config`` edits.
    Login state often lives next to it (parent dir) or under ``~/.<id>``,
    ``~/.config/<id>``, an MCP target, or a skills tree — agy stores settings
    under ``~/.gemini/antigravity-cli`` and auth in the OS keyring.
    """
    home_resolved = home.resolve()
    seen: set[Path] = set()
    out: list[Path] = []

    def add(path: Path) -> None:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen or key == home_resolved:
            return
        seen.add(key)
        out.append(path)

    config = agent.get("config") or {}
    if config.get("path"):
        cfg = expand_home(str(config["path"]), home)
        add(cfg)
        add(cfg.parent)
    for marker in config.get("markers") or []:
        add(expand_home(str(marker), home))
    aid = str(agent["id"])
    target = mcp_target(aid)
    if target is not None:
        add(home / target.relpath)
    skill_rel = AGENT_SKILL_DIRS.get(aid)
    if skill_rel:
        skill = Path(skill_rel)
        add(home / (skill.parent if skill.parts[0] == ".config" else skill.parts[0]))
    add(home / f".{aid}")
    add(home / ".config" / aid)
    return out


def registry_agent_status(
    agent: dict[str, Any],
    home: Path | None = None,
    *,
    probe_ver: bool = False,
) -> dict[str, Any]:
    """Installed status for a registry agent: binary location + config present.

    Binaries under ``ASTROAI_LAB_BIN_DIR`` (``~/.local/bin``) are *managed*.
    Leftover ``$SCRATCH/.local/bin`` copies are *legacy* (not treated as
    installed for update). Config paths stay on home for persistence.
    Version probing is opt-in (``probe_ver=True``).
    """
    home = home or Path.home()
    binary = str(agent["binary"])
    from astroai_lab.agent.install import (
        BINARY_SOURCE_LEGACY,
        BINARY_SOURCE_MISSING,
        TOOLS,
        classify_binary,
        tool_binary,
    )

    # Prefer TOOLS remaps (qoder→qodercli) when the agent id is also a TOOLS entry.
    probe_name = tool_binary(agent["id"]) if agent["id"] in TOOLS else binary
    info = classify_binary(probe_name, home=home)
    # Legacy scratch-only copies do not count as installed — update will reinstall.
    binary_ok = info["source"] not in (BINARY_SOURCE_MISSING, BINARY_SOURCE_LEGACY)
    config = agent.get("config") or {}
    cfg_path: Path | None = None
    config_declared = bool(config.get("path"))
    if config_declared:
        cfg_path = expand_home(str(config["path"]), home)
    config_present = any(_path_has_state(p) for p in config_state_paths(agent, home))
    # Declared settings file missing is still ok when login/state dirs exist
    # (agy writes settings sparsely; auth lives in the keyring).
    config_ok = config_present if config_declared else True
    version = probe_agent_version(agent) if (probe_ver and binary_ok) else None
    return {
        "id": agent["id"],
        "name": agent["name"],
        "binary": binary,
        "binary_ok": binary_ok,
        "binary_path": info.get("path"),
        "binary_source": info["source"],
        "managed": bool(info["managed"]),
        "home_install": bool(info["home_install"]),
        "legacy": bool(info.get("legacy")),
        "config": str(cfg_path) if cfg_path else "",
        "config_ok": config_ok,
        "config_declared": config_declared,
        "config_present": config_present,
        "installed": binary_ok and (config_ok if config_declared else binary_ok),
        "version": version,
        "summary": agent.get("summary", ""),
    }


def tool_on_path(name: str) -> bool:
    """Re-export of install.tool_on_path so registry callers can mock it locally."""
    from astroai_lab.agent.install import tool_on_path as _tool_on_path

    return _tool_on_path(name)


def registry_verify_issues(
    home: Path | None = None,
    *,
    root: Path | None = None,
    installed_only: bool = False,
    probe_binaries: bool = False,
) -> list[str]:
    """Config (+ optional launch) issues for registered agents.

    With ``installed_only=True``, agents whose binary is not on PATH are
    skipped (no issue). `agent verify` uses this so fresh images that don't
    ship hermes/openclaw still pass the container gate; agents that ARE
    installed (managed, home, or other PATH) still get config checked.
    Pass ``probe_binaries=True`` from ``agent verify`` to also smoke-launch.
    """
    home = home or Path.home()
    issues: list[str] = []
    for agent in load_registry(root):
        status = registry_agent_status(agent, home)
        if not status["binary_ok"]:
            if installed_only:
                continue
            issues.append(
                f"{agent['name']} binary not found ({status['binary']}) — run: "
                f"astroai agent install {agent['id']}"
            )
            continue
        if agent.get("config", {}).get("path") and not status["config_ok"]:
            issues.append(f"{agent['name']} config missing ({status['config']})")
        elif status.get("config") and agent.get("config", {}).get("path"):
            fmt = str((agent.get("config") or {}).get("format", "json"))
            cfg = Path(status["config"])
            if fmt != "markdown" and cfg.is_file():
                # Present config: catch broken syntax so repair has something to fix.
                from astroai_lab.agent import agent_config as agent_config_mod
                from astroai_lab.errors import LabError as _LabError

                try:
                    text = cfg.read_text(encoding="utf-8", errors="replace")
                    agent_config_mod.validate_config_text(agent["id"], text, home=home)
                except _LabError as exc:
                    issues.append(f"{agent['name']} config broken ({cfg}): {exc}")
                except OSError as exc:
                    issues.append(f"{agent['name']} config unreadable ({cfg}): {exc}")
        if probe_binaries:
            launch_err = probe_agent_launch(agent)
            if launch_err:
                issues.append(f"{agent['name']} failed to launch: {launch_err}")
    return issues


def _install_npm(agent: dict[str, Any]) -> str:
    from astroai_lab.agent.install import (
        _link_into_local_bin,
        _npm_prefix,
        _require,
        _verify_cmd,
        npm_global_install_cmd,
        npm_install_environ,
        run,
    )
    from astroai_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    binary = agent["binary"]
    _require("npm")
    run(
        npm_global_install_cmd(_npm_prefix(), str(agent["install"]["source"])),
        env=npm_install_environ(),
        timeout=INSTALL_TIMEOUT_SEC,
    )
    bin_path = _npm_prefix() / "bin" / binary
    _link_into_local_bin(bin_path, binary)
    _verify_cmd(binary, extra_paths=[bin_path])
    return binary


def _install_curl(agent: dict[str, Any]) -> str:
    from astroai_lab.agent.install import (
        _bin_dir,
        _curl_pipe_bash,
        _link_into_local_bin,
        _verify_cmd,
        find_curl_binary,
    )

    binary = str(agent["binary"])
    # Registry installs can pass installer-specific env (e.g. XDG_BIN_DIR,
    # GOOSE_BIN_DIR) with a {bin_dir} token expanded to the session bin dir.
    env = {
        k: str(v).replace("{bin_dir}", str(_bin_dir()))
        for k, v in (agent["install"].get("env") or {}).items()
    }
    raw_args = agent["install"].get("args") or []
    script_args = [str(a) for a in raw_args]
    _curl_pipe_bash(str(agent["install"]["source"]), env=env or None, args=script_args or None)
    extra = [Path(p).expanduser() for p in agent["install"].get("post_binary_paths", [])]
    found = find_curl_binary(binary, extra=extra)
    if found is None:
        raise LabError(
            f"{binary} not found after install — open a new shell",
            hint="Check the installer output; binary should land under $HOME "
            "(~/.local/bin or the agent's own bin dir)",
        )
    _link_into_local_bin(found, binary)
    _verify_cmd(binary, extra_paths=extra)
    return binary


def _install_uv_tool(agent: dict[str, Any]) -> str:
    from astroai_lab.agent.install import _require, _session_environ, _verify_cmd, run
    from astroai_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    binary = agent["binary"]
    _require("uv")
    run(
        ["uv", "tool", "install", "--force", str(agent["install"]["source"])],
        env=_session_environ(),
        timeout=INSTALL_TIMEOUT_SEC,
    )
    _verify_cmd(binary)
    from astroai_lab.agent.install import clear_legacy_scratch_binary

    clear_legacy_scratch_binary(str(binary))
    return binary


def _install_gh_release(agent: dict[str, Any]) -> str:
    import platform

    from astroai_lab.agent.install import _gh_release_bin, _verify_cmd, clear_legacy_scratch_binary

    binary = agent["binary"]
    install = agent["install"]
    # {arch} templates to platform.machine() (x86_64/aarch64) for per-arch assets.
    asset = str(install["asset"]).replace("{arch}", platform.machine())
    _gh_release_bin(
        str(install["repo"]),
        asset,
        binary,
        requires_gh_auth=bool(install.get("requires_gh_auth")),
    )
    _verify_cmd(binary)
    clear_legacy_scratch_binary(str(binary))
    return binary


def install_registry_agent(agent_id: str, *, dry_run: bool = False) -> str:
    """Install a registered agent by id, dispatching on install.method.

    Registered agents that already exist in ``install.TOOLS`` (hermes, openclaw,
    cursor) keep their battle-tested installer via ``install_tool``; future
    registry-only agents dispatch by method here.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="astroai agent list")

    from astroai_lab.agent.install import (
        TOOLS,
        install_tool,
        refuse_if_home_owned,
    )

    if agent_id in TOOLS:
        install_tool(agent_id, dry_run=dry_run)
        return agent_id

    if dry_run:
        return agent_id

    from astroai_lab.agent.setup_state import agent_setup_lock

    with agent_setup_lock():
        refuse_if_home_owned(agent_id)
        method = agent["install"]["method"]
        if method == "npm":
            return _install_npm(agent)
        if method == "curl":
            return _install_curl(agent)
        if method == "uv-tool":
            return _install_uv_tool(agent)
        if method == "gh-release":
            return _install_gh_release(agent)
        raise LabError(f"Agent {agent_id} has unsupported install.method={method!r}")


def remove_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    purge: bool = False,
    clean_home: bool = False,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Uninstall a registered agent by id (Phase 2 `agent remove`).

    Agents that exist in ``install.TOOLS`` (hermes, openclaw, cursor) keep their
    battle-tested uninstaller via ``install.uninstall_tool``; registry-only
    agents are removed by install method here. Returns result dicts for JSON.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="astroai agent list")

    from astroai_lab.agent.install import (
        TOOLS,
        clear_legacy_scratch_binary,
        home_bin_candidates,
        uninstall_tool,
    )

    if agent_id in TOOLS:
        results = uninstall_tool(
            agent_id, home=home, purge=purge, clean_home=clean_home, dry_run=dry_run
        )
        return [r.__dict__ for r in results]

    from astroai_lab.agent.setup_state import agent_setup_lock

    with agent_setup_lock(home):
        results = _remove_registry_method(agent, home=home, purge=purge, dry_run=dry_run)
        home = home or Path.home()
        binary = str(agent["binary"])
        from astroai_lab.agent.install import RemoveResult, _remove_file

        for home_bin in home_bin_candidates(binary, home=home):
            result = _remove_file(home_bin, f"home-binary:{binary}", dry_run=dry_run)
            if result:
                results.append(result.__dict__ if isinstance(result, RemoveResult) else result)
        if not dry_run:
            clear_legacy_scratch_binary(binary)
        return results


def _remove_registry_method(
    agent: dict[str, Any],
    *,
    home: Path | None,
    purge: bool,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Method-based removal for registry agents not present in install.TOOLS."""
    import contextlib
    import shutil
    import subprocess

    from astroai_lab.agent.install import (
        RemoveResult,
        _bin_dir,
        _npm_prefix,
        _session_environ,
    )
    from astroai_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    home = home or Path.home()
    agent_id = agent["id"]
    binary = str(agent["binary"])
    method = agent["install"]["method"]
    results: list[dict[str, Any]] = []

    def rm(path: Path, target: str) -> None:
        if not (path.exists() or path.is_symlink()):
            return
        if dry_run:
            results.append(RemoveResult(target, "would_remove", str(path)).__dict__)
        else:
            try:
                path.unlink(missing_ok=True)
                results.append(RemoveResult(target, "removed", str(path)).__dict__)
            except OSError as exc:
                results.append(RemoveResult(target, "error", str(exc)).__dict__)

    def rm_tree(path: Path, target: str) -> None:
        if not path.exists():
            return
        if dry_run:
            results.append(RemoveResult(target, "would_remove", str(path)).__dict__)
        else:
            try:
                shutil.rmtree(path)
                results.append(RemoveResult(target, "removed", str(path)).__dict__)
            except OSError as exc:
                results.append(RemoveResult(target, "error", str(exc)).__dict__)

    # npm-installed: best-effort `npm uninstall -g`, then drop bin links.
    if method == "npm":
        pkg = re.sub(r"@[^@]*$", "", str(agent["install"].get("source", binary)))
        if not dry_run and shutil.which("npm"):
            from astroai_lab.agent.install import run

            with contextlib.suppress(LabError, subprocess.CalledProcessError, OSError):
                run(
                    ["npm", "uninstall", "-g", "--prefix", str(_npm_prefix()), pkg],
                    env=_session_environ(),
                    timeout=INSTALL_TIMEOUT_SEC,
                    quiet=True,  # keep stdout clean for `--json agent remove/wipe`
                )
        rm(_npm_prefix() / "bin" / binary, f"binary:{binary}")

    # curl / gh-release / uv-tool drop a managed binary.
    rm(_bin_dir() / binary, f"binary:{binary}")

    # Config file (registry config.path).
    config = agent.get("config") or {}
    if config.get("path"):
        cfg = expand_home(str(config["path"]), home)
        rm(cfg, f"config:{cfg}")
        lab_dir = home / ".astroai" / "lab"
        if purge and cfg.parent not in {home, lab_dir}:
            rm_tree(cfg.parent, f"purge:{cfg.parent}")

    # Plugin-applied files (Phase 3 recursive removal). Run the precise
    # plugin sweep first so installed plugins report `removed` (not `skipped`),
    # then a broad sweep of ~/.<id>/skills catches any non-plugin skills.
    from astroai_lab.agent import plugins as agent_plugins

    for row in agent_plugins.remove_agent_plugin_files(agent_id, home=home, dry_run=dry_run):
        results.append(row)
    rm_tree(home / f".{agent_id}" / "skills", f"plugins:{agent_id}")

    # Setup state stamps.
    from astroai_lab.agent.setup_state import failed_path, stamp_path

    rm(stamp_path(home), "state:stamp")
    rm(failed_path(home), "state:failed")

    return results


# ---------------------------------------------------------------------------
# Registry-driven setup / update (Phase 2 `agent setup <id>` + `agent update <id>`)
# ---------------------------------------------------------------------------


def list_installed_registry_agents(home: Path | None = None) -> list[dict[str, Any]]:
    """Registry agents with a CLI on PATH (managed, home, or other)."""
    home = home or Path.home()
    return [a for a in load_registry() if registry_agent_status(a, home)["binary_ok"]]


def _config_scaffold(agent: dict[str, Any]) -> str:
    """Minimal scaffold for a missing registry ``config.path``.

    JSON5/JSONC get a ``//`` comment header (JSONC/JSON5 do not support ``#``
    comments — parse_jsonc only strips ``//`` and ``/* */``); strict JSON gets
    a header-free body; YAML/TOML/markdown get ``#`` headers. All bodies parse
    to an empty mapping / empty file respectively.
    """
    fmt = str((agent.get("config") or {}).get("format", "json"))
    name = agent.get("name", agent["id"])
    header = f"# {name} — scaffolded by `astroai agent setup {agent['id']}`\n"
    if fmt == "json":
        return "{}\n"
    if fmt in ("jsonc", "json5"):
        return f"// {name} — scaffolded by `astroai agent setup {agent['id']}`\n{{}}\n"
    if fmt == "yaml":
        return header + "{}\n"
    # toml / markdown / unknown: comment-only body stays valid.
    return header + "\n"


# Cold uvx/npx MCP startup on CANFAR (NFS home + first download) often exceeds
# Codex's 30s default — bump known slow servers without clobbering larger values.
_CODEX_MCP_STARTUP_TIMEOUT_SEC = 120
_CODEX_MCP_TIMEOUT_KEYS = (
    "mcp_servers.fetch.startup_timeout_sec",
    "mcp_servers.memory.startup_timeout_sec",
    "mcp_servers.github.startup_timeout_sec",
)


def _ensure_codex_mcp_timeouts(cfg: Path, *, home: Path, dry_run: bool) -> str | None:
    """Ensure Codex MCP servers have a long enough startup_timeout_sec.

    Returns an action string when something would change / changed, else None.
    """
    from astroai_lab.agent import agent_config as agent_config_mod
    from astroai_lab.utils.toml_compat import tomllib

    text = cfg.read_text(encoding="utf-8", errors="replace")
    try:
        data = tomllib.loads(text)
    except Exception:  # noqa: BLE001 — leave broken configs to the repair path
        return None
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None

    needed: dict[str, int] = {}
    for dotted in _CODEX_MCP_TIMEOUT_KEYS:
        parts = dotted.split(".")
        if len(parts) != 3:
            continue
        server = servers.get(parts[1])
        if not isinstance(server, dict):
            continue
        current = server.get("startup_timeout_sec")
        if isinstance(current, (int, float)) and int(current) >= _CODEX_MCP_STARTUP_TIMEOUT_SEC:
            continue
        needed[dotted] = _CODEX_MCP_STARTUP_TIMEOUT_SEC
    if not needed:
        return None
    labels = ", ".join(sorted(k.split(".")[1] for k in needed))
    if dry_run:
        return f"would set Codex MCP startup_timeout_sec ({labels})"
    agent_config_mod.edit_agent_config("codex", home=home, set_items=needed)
    return f"set Codex MCP startup_timeout_sec ({labels})"


def _run_post_install(command: str) -> None:
    """Run a ``setup.post_install`` shell command (interactive agents only)."""
    import subprocess

    from astroai_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    try:
        proc = subprocess.run(command, shell=True, timeout=INSTALL_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        raise LabError(
            f"post_install timed out after {INSTALL_TIMEOUT_SEC}s",
            hint="Re-run with a higher ASTROAI_LAB_AGENT_INSTALL_TIMEOUT",
        ) from exc
    if proc.returncode != 0:
        raise LabError(f"post_install exited {proc.returncode}: {command}")


def setup_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    post_install: bool = False,
) -> dict[str, Any]:
    """Write configs, skills, and MCP for one registered agent.

    1. Apply the manifest config bundle when one shares this agent id (real
       starter templates — must run *before* any empty scaffold).
    2. Scaffold the declared config file when still missing (never clobber).
    3. Create the agent's skills dir (AGENT_SKILL_DIRS, when declared).
    4. Re-apply every plugin whose support matrix includes this agent.
    5. Optionally run ``setup.post_install`` (interactive, opt-in).
    6. Record the setup stamp (mode=setup:<id>).

    Returns ``{ok, partial, agent, actions, errors}`` (human-readable action
    strings) for JSON output.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="astroai agent list")
    home = home or Path.home()
    actions: list[str] = []
    errors: list[str] = []

    from astroai_lab.agent.agent_targets import AGENT_SKILL_DIRS
    from astroai_lab.agent.bundle_path import bundle_root
    from astroai_lab.agent.inventory import list_bundles
    from astroai_lab.agent.setup import run_bundle

    # Bundle first when one exists — otherwise an empty scaffold would block
    # install_file() from writing the real starter template (new-user footgun).
    if agent_id in list_bundles():
        if dry_run:
            actions.append(f"would apply config bundle ({agent_id})")
        else:
            run_bundle(
                agent_id,
                bundle_root(),
                home,
                None,
                force=force,
                dry_run=False,
            )
            actions.append(f"applied config bundle ({agent_id})")

    config = agent.get("config") or {}
    if config.get("path"):
        cfg = expand_home(str(config["path"]), home)
        if cfg.is_file():
            actions.append(f"config exists ({cfg})")
        elif dry_run:
            actions.append(f"would create config ({cfg})")
        else:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(_config_scaffold(agent), encoding="utf-8")
            actions.append(f"created config ({cfg})")

    rel = AGENT_SKILL_DIRS.get(agent_id)
    if rel:
        skills_dir = home / rel
        if skills_dir.is_dir():
            actions.append(f"skills dir present ({skills_dir})")
        elif dry_run:
            actions.append(f"would create skills dir ({skills_dir})")
        else:
            skills_dir.mkdir(parents=True, exist_ok=True)
            actions.append(f"created skills dir ({skills_dir})")

    if agent_id == "codex":
        cfg = expand_home("~/.codex/config.toml", home)
        if cfg.is_file():
            patched = _ensure_codex_mcp_timeouts(cfg, home=home, dry_run=dry_run)
            if patched:
                actions.append(patched)

    from astroai_lab.agent import plugins as agent_plugins

    for result in agent_plugins.apply_agent_plugins(
        agent_id, home=home, force=force, dry_run=dry_run, defaults_only=True
    ):
        if result.status == "failed":
            errors.append(f"plugin {result.plugin} ({result.agent}): {result.detail}")
        elif result.status in ("installed", "would_install", "updated"):
            actions.append(
                f"plugin {result.status.replace('_', ' ')} {result.plugin} ({result.agent})"
            )

    post = (agent.get("setup") or {}).get("post_install")
    if post and post_install:
        if dry_run:
            actions.append(f"would run post-install ({post})")
        else:
            try:
                _run_post_install(str(post))
                actions.append(f"ran post-install ({post})")
            except LabError as exc:
                errors.append(f"post-install: {exc}")

    ok = not errors
    if not dry_run and ok:
        from astroai_lab.agent.setup_state import record_setup_ok

        record_setup_ok(home, mode=f"setup:{agent_id}")
    return {
        "ok": ok,
        "partial": bool(actions) and bool(errors),
        "agent": agent_id,
        "actions": actions,
        "errors": errors,
    }


def update_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    force_reinstall: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Refresh one registered agent's CLI and configs.

    1. Refresh the CLI binary (install if missing, or always with --reinstall).
    2. Force re-apply every plugin supporting this agent (skills/MCP/config).
    3. Refresh the setup stamp (mode=update:<id>).

    Returns ``{ok, partial, agent, actions, errors}`` for JSON output.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="astroai agent list")
    home = home or Path.home()
    actions: list[str] = []
    errors: list[str] = []

    from astroai_lab.agent.setup_state import agent_setup_lock

    with agent_setup_lock(home):
        status = registry_agent_status(agent, home)
        if status["binary_ok"] and not force_reinstall:
            actions.append(f"binary up-to-date ({agent_id})")
        else:
            verb = "reinstall" if force_reinstall else "install"
            try:
                install_registry_agent(agent_id, dry_run=dry_run)
                actions.append(f"binary {verb} ({agent_id})")
            except LabError as exc:
                errors.append(f"binary {agent_id}: {exc}")

        from astroai_lab.agent import plugins as agent_plugins

        for result in agent_plugins.apply_agent_plugins(
            agent_id, home=home, force=True, dry_run=dry_run, assume_locked=True
        ):
            if result.status == "failed":
                errors.append(f"plugin {result.plugin} ({result.agent}): {result.detail}")
            elif result.status in ("installed", "would_install", "updated", "removed"):
                actions.append(
                    f"plugin {result.status.replace('_', ' ')} {result.plugin} ({result.agent})"
                )

        ok = not errors
        if not dry_run and ok:
            from astroai_lab.agent.setup_state import record_setup_ok

            record_setup_ok(home, mode=f"update:{agent_id}")
        return {
            "ok": ok,
            "partial": bool(actions) and bool(errors),
            "agent": agent_id,
            "actions": actions,
            "errors": errors,
        }


def fix_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Regenerate or sanitize one registered agent's config.

    Regenerate/sanitize ONE registered agent's config from the registry,
    reusing ``fix.py``'s repair pattern (syntax check → reset to a minimal
    valid body) with the format-aware parse from ``agent_config``:

    1. Missing config → scaffold it (format-aware; JSONC/JSON5 keep a ``//``
       header, strict JSON gets ``{}\n``, YAML/TOML/markdown comment bodies).
    2. Present but unparseable → reset to the scaffold (markdown is read-only:
       no repair).
    3. Present + parseable → nothing to fix.
    4. Ensure the agent's skills dir exists (like `agent setup <id>`).
    5. Refresh the setup stamp / clear the failed marker when healthy.

    A repaired (reset) config loses plugin-written entries — run
    `agent update <id>` afterwards to force re-apply the agent's plugins.

    Returns ``{ok, partial, agent, actions, errors}`` for JSON output.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="astroai agent list")
    home = home or Path.home()
    actions: list[str] = []
    errors: list[str] = []

    config = agent.get("config") or {}
    if config.get("path"):
        cfg = expand_home(str(config["path"]), home)
        fmt = str(config.get("format", "json"))
        if not cfg.is_file():
            if dry_run:
                actions.append(f"would create config ({cfg})")
            else:
                cfg.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_text(_config_scaffold(agent), encoding="utf-8")
                actions.append(f"created config ({cfg})")
        elif fmt == "markdown":
            actions.append(f"config healthy (markdown read-only) ({cfg})")
        else:
            from astroai_lab.agent import agent_config as agent_config_mod

            text = cfg.read_text(encoding="utf-8", errors="replace")
            try:
                agent_config_mod.validate_config_text(agent_id, text, home=home)
            except LabError:
                if dry_run:
                    actions.append(f"would repair broken {fmt} config ({cfg})")
                else:
                    cfg.parent.mkdir(parents=True, exist_ok=True)
                    cfg.write_text(_config_scaffold(agent), encoding="utf-8")
                    actions.append(f"repaired broken {fmt} config ({cfg})")
            else:
                # Parseable — still may need semantic sanitize (OpenCode lsp maps).
                if agent_id == "opencode":
                    from astroai_lab.agent.opencode_config import sanitize_opencode_config
                    from astroai_lab.utils.json_utils import parse_jsonc, write_json

                    parsed = parse_jsonc(text)
                    if isinstance(parsed, dict):
                        cleaned, changes = sanitize_opencode_config(parsed)
                        if changes:
                            if dry_run:
                                actions.append(
                                    "would sanitize OpenCode config: " + "; ".join(changes[:4])
                                )
                            else:
                                write_json(cfg, cleaned)
                                actions.append(
                                    "sanitized OpenCode config: " + "; ".join(changes[:4])
                                )
                        else:
                            actions.append(f"config healthy ({cfg})")
                    else:
                        actions.append(f"config healthy ({cfg})")
                elif agent_id == "codex":
                    patched = _ensure_codex_mcp_timeouts(cfg, home=home, dry_run=dry_run)
                    if patched:
                        actions.append(patched)
                    else:
                        actions.append(f"config healthy ({cfg})")
                else:
                    actions.append(f"config healthy ({cfg})")
    else:
        actions.append("no config declared")

    from astroai_lab.agent.agent_targets import AGENT_SKILL_DIRS

    rel = AGENT_SKILL_DIRS.get(agent_id)
    if rel:
        skills_dir = home / rel
        if skills_dir.is_dir():
            actions.append(f"skills dir present ({skills_dir})")
        elif dry_run:
            actions.append(f"would create skills dir ({skills_dir})")
        else:
            skills_dir.mkdir(parents=True, exist_ok=True)
            actions.append(f"created skills dir ({skills_dir})")

    ok = not errors
    if not dry_run and ok:
        from astroai_lab.agent.setup_state import record_setup_ok

        record_setup_ok(home, mode=f"repair:{agent_id}")
    return {
        "ok": ok,
        "partial": bool(actions) and bool(errors),
        "agent": agent_id,
        "actions": actions,
        "errors": errors,
    }
