"""Agent setup lock, stamp, failed marker, and machine-readable report."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astroai_lab.agent.bundle_path import bundle_root

GIT_TIMEOUT_SEC = int(os.environ.get("ASTROAI_LAB_AGENT_GIT_TIMEOUT", "120"))
# Self-bootstrapping installers (hermes bootstraps its own uv/python/node and
# clones a repo; goose/kilo/opencode curl-installers are similar) routinely take
# several minutes. 300s was too tight — 1500s is the value verified end-to-end
# in a container E2E (gives `_curl_pipe_bash` a bash budget of ~1000s vs ~197s
# at 300) while still failing fast on a hung network.
INSTALL_TIMEOUT_SEC = int(os.environ.get("ASTROAI_LAB_AGENT_INSTALL_TIMEOUT", "1500"))
LOCK_TIMEOUT_SEC = int(os.environ.get("ASTROAI_LAB_AGENT_LOCK_TIMEOUT", "30"))


def lab_state_dir(home: Path | None = None) -> Path:
    home = home or Path.home()
    return home / ".astroai" / "lab"


def stamp_path(home: Path | None = None) -> Path:
    return lab_state_dir(home) / "agent-setup-stamp"


def failed_path(home: Path | None = None) -> Path:
    return lab_state_dir(home) / "agent-setup-failed"


def log_path(home: Path | None = None) -> Path:
    return lab_state_dir(home) / "agent-setup.log"


def lock_path(home: Path | None = None) -> Path:
    return lab_state_dir(home) / "agent-setup.lock"


@dataclass
class SetupState:
    stamp: str | None
    failed: str | None
    log: str | None
    ok: bool
    needs_retry: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_setup_state(home: Path | None = None) -> SetupState:
    home = home or Path.home()
    stamp = stamp_path(home)
    failed = failed_path(home)
    log = log_path(home)
    stamp_text = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else None
    failed_text = failed.read_text(encoding="utf-8").strip() if failed.is_file() else None
    log_exists = log.is_file()
    ok = stamp_text is not None and failed_text is None
    needs_retry = stamp_text is None or failed_text is not None
    return SetupState(
        stamp=stamp_text,
        failed=failed_text,
        log=str(log) if log_exists else None,
        ok=ok,
        needs_retry=needs_retry,
    )


def record_setup_ok(home: Path | None = None, *, mode: str = "install") -> None:
    home = home or Path.home()
    state = lab_state_dir(home)
    state.mkdir(parents=True, exist_ok=True)
    ver = "unknown"
    version_file = bundle_root() / "VERSION"
    if version_file.is_file():
        ver = version_file.read_text(encoding="utf-8").strip()
    from astroai_lab.utils.json_utils import atomic_write_text

    atomic_write_text(
        stamp_path(home),
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + f" bundle={ver} mode={mode}\n",
    )
    failed_path(home).unlink(missing_ok=True)


def record_setup_failed(
    home: Path | None = None,
    *,
    exit_code: int = 1,
    detail: str = "",
) -> None:
    home = home or Path.home()
    state = lab_state_dir(home)
    state.mkdir(parents=True, exist_ok=True)
    line = (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        + f" exit={exit_code}"
        + (f" {detail}" if detail else "")
        + "\n"
    )
    from astroai_lab.utils.json_utils import atomic_write_text

    atomic_write_text(failed_path(home), line)


def append_setup_log(home: Path | None, text: str) -> None:
    home = home or Path.home()
    path = log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


@contextmanager
def agent_setup_lock(
    home: Path | None = None,
    *,
    timeout: float | None = None,
) -> Iterator[None]:
    """Exclusive lock for agent setup / wizard actions (shared lock family)."""
    from astroai_lab.core.pathlock import path_lock

    home = home or Path.home()
    with path_lock(
        lock_path(home),
        timeout=LOCK_TIMEOUT_SEC if timeout is None else timeout,
        busy_hint="Another agent setup is already running",
    ):
        yield


def _lock_holder_alive(path: Path) -> bool:
    try:
        raw = path.read_text(encoding="utf-8").strip().split()
        if not raw:
            return False
        pid = int(raw[0])
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours
    return True


def _lock_is_stale(path: Path) -> bool:
    """Stale when the recorded holder PID is dead or the lock file is unreadable."""
    if not path.is_file():
        return True
    return not _lock_holder_alive(path)


def build_agent_report(home: Path | None = None, *, probe_ver: bool = False) -> dict[str, Any]:
    """One-shot JSON report for wizard / automation (registry-driven)."""
    from concurrent.futures import ThreadPoolExecutor

    from astroai_lab.agent.inventory import verify_setup
    from astroai_lab.agent.registry import list_registry_agents, registry_agent_status
    from astroai_lab.core.session_resources import collect_resources
    from astroai_lab.version import version_info

    home = home or Path.home()
    state = read_setup_state(home)
    issues = verify_setup(home)
    registry = list(list_registry_agents())

    def _status(agent: dict[str, Any]) -> dict[str, Any]:
        return registry_agent_status(agent, home, probe_ver=probe_ver)

    # Parallelize when probing versions — each CLI can take ~2s; sequential
    # would make `agent list` feel broken. Order preserved via map.
    if probe_ver and len(registry) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(registry))) as pool:
            statuses = list(pool.map(_status, registry))
    else:
        statuses = [_status(a) for a in registry]

    agents = [
        {
            "agent": status["id"],
            "id": status["id"],
            "name": status["name"],
            "binary": status["binary_ok"],
            "binary_name": status["binary"],
            "binary_path": status.get("binary_path"),
            "binary_source": status.get("binary_source", "missing"),
            "managed": status.get("managed", False),
            "home_install": status.get("home_install", False),
            "config": status["config_ok"],
            "config_path": status["config"],
            "binary_ok": status["binary_ok"],
            "config_ok": status["config_ok"],
            "config_declared": status.get("config_declared", True),
            "version": status.get("version"),
            "summary": status.get("summary", ""),
        }
        for status in statuses
    ]
    return {
        "ok": state.ok and not issues,
        "lab": version_info(),
        "setup": state.to_dict(),
        "issues": issues,
        "agents": agents,
        "resources": collect_resources().to_dict(),
        "log_tail": _log_tail(home, n=40),
    }


def _log_tail(home: Path, *, n: int = 40) -> str:
    path = log_path(home)
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def dump_json(data: Any) -> str:
    return json.dumps(data, indent=2) + "\n"
