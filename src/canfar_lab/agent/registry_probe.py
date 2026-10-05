"""Probe agent binaries: version, launch, and status."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from canfar_lab.agent.agent_targets import AGENT_SKILL_DIRS, expand_home, mcp_target
from canfar_lab.agent.registry_store import load_registry

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]+)?)")

# Large Go/Node agent CLIs often need >1s just to print --version (kilo ~1.9s,
# cline ~1.4s on a warm box). 0.8s timed out → blank Version column in
# `agent list`. Override with CANFAR_LAB_PROBE_VERSION_TIMEOUT if needed.
_DEFAULT_PROBE_TIMEOUT_SEC = 3.0


def _probe_timeout_sec(override: float | None = None) -> float:
    import os

    if override is not None:
        return override
    raw = os.environ.get("CANFAR_LAB_PROBE_VERSION_TIMEOUT", "").strip()
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

    from canfar_lab.agent.install import classify_binary
    from canfar_lab.shell.session_env import resolve_session_env

    info = classify_binary(binary)
    classified = info.get("path")
    if classified and Path(str(classified)).is_file() and os.access(str(classified), os.X_OK):
        return str(classified)

    resolved = shutil.which(binary)
    if resolved is not None:
        return resolved
    session = resolve_session_env(ensure=False)
    for candidate in (
        session.canfar_lab_bin_dir / binary,
        session.canfar_lab_npm_prefix / "bin" / binary,
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
    CLIs); set ``CANFAR_LAB_PROBE_VERSION=0`` to skip, or
    ``CANFAR_LAB_PROBE_VERSION_TIMEOUT`` to override seconds. Per-agent
    overrides live in registry YAML under ``version:``.
    """
    import os
    import subprocess

    # Skip probes in unit tests unless explicitly enabled (avoids hung CLIs).
    if os.environ.get("CANFAR_LAB_PROBE_VERSION", "1") in ("0", "false", "no"):
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
    from canfar_lab.agent.install import TOOLS, tool_binary

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
    ``probe_agent_launch``. Skipped when ``CANFAR_LAB_PROBE_VERSION=0``.
    """
    import os
    import subprocess

    if os.environ.get("CANFAR_LAB_PROBE_VERSION", "1") in ("0", "false", "no"):
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


def agent_config_file(agent: dict[str, Any], home: Path) -> Path | None:
    """Resolved settings file for a registry agent (honors HERMES_HOME)."""
    config = agent.get("config") or {}
    if not config.get("path"):
        return None
    if str(agent["id"]) == "hermes":
        from canfar_lab.core.home_layout import hermes_home_dir

        return hermes_home_dir(home=home) / "config.yaml"
    return expand_home(str(config["path"]), home)


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

    cfg = agent_config_file(agent, home)
    if cfg is not None:
        add(cfg)
        add(cfg.parent)
    config = agent.get("config") or {}
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

    Binaries under ``CANFAR_LAB_BIN_DIR`` (``$SCRATCH/.local/bin``) are
    *managed*. Leftover ``~/.local/bin`` copies under ``$HOME`` (/arc) are
    user-owned home installs — install refuses until ``--clean-home``.
    Config paths stay on home for persistence. Version probing is opt-in
    (``probe_ver=True``).
    """
    home = home or Path.home()
    binary = str(agent["binary"])
    from canfar_lab.agent.install import (
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
        cfg_path = agent_config_file(agent, home)
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
    from canfar_lab.agent.install import tool_on_path as _tool_on_path

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
                f"canfar lab agent install {agent['id']}"
            )
            continue
        if agent.get("config", {}).get("path") and not status["config_ok"]:
            issues.append(f"{agent['name']} config missing ({status['config']})")
        elif status.get("config") and agent.get("config", {}).get("path"):
            fmt = str((agent.get("config") or {}).get("format", "json"))
            cfg = Path(status["config"])
            if fmt != "markdown" and cfg.is_file():
                # Present config: catch broken syntax so repair has something to fix.
                from canfar_lab.agent import agent_config as agent_config_mod
                from canfar_lab.errors import LabError as _LabError

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
