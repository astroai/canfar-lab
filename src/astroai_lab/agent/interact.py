"""Interactive agent & open container diagnostics, access URLs, and port mappings."""

from __future__ import annotations

import os
import shutil
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EndpointInfo:
    name: str
    port: int
    path_prefix: str
    description: str
    active: bool
    url_hint: str


def inspect_interact_endpoints() -> dict[str, Any]:
    """Inspect active container services, open ports, and agent interaction points."""
    session_kind = os.environ.get("ASTROAI_SESSION_KIND", "").strip().lower() or "unknown"
    wizard_port = int(os.environ.get("ASTROAI_AGENT_WIZARD_PORT", "4792"))
    openworker_port = int(os.environ.get("ASTROAI_OPENWORKER_PORT", "5000"))
    openresearch_port = 5000

    endpoints: list[EndpointInfo] = []

    # Agent Hub Wizard
    wizard_active = _check_port("127.0.0.1", wizard_port)
    endpoints.append(
        EndpointInfo(
            name="AstroAI Agent Hub",
            port=wizard_port,
            path_prefix="/astroai-agents/",
            description="Agent status, wizard setup, CANFAR sessions, and Ray status",
            active=wizard_active,
            url_hint=f"http://127.0.0.1:{wizard_port}/ or via proxy at /astroai-agents/",
        )
    )

    # OpenResearch (orx)
    if session_kind == "openresearch":
        orx_active = _check_port("127.0.0.1", openresearch_port)
        endpoints.append(
            EndpointInfo(
                name="OpenResearch Dashboard",
                port=openresearch_port,
                path_prefix="/",
                description="OpenResearch (orx) paper & research agent dashboard",
                active=orx_active,
                url_hint=f"http://127.0.0.1:{openresearch_port}/",
            )
        )

    # OpenWorker
    if session_kind == "openworker":
        ow_active = _check_port("127.0.0.1", openworker_port)
        endpoints.append(
            EndpointInfo(
                name="OpenWorker Browser UI",
                port=openworker_port,
                path_prefix="/",
                description="OpenWorker web surface + Python agent backend",
                active=ow_active,
                url_hint=f"http://127.0.0.1:{openworker_port}/",
            )
        )

    # Terminal / Jupyter / VSCode / Marimo (webterm = legacy session kind)
    if session_kind in ("terminal", "webterm", "unknown"):
        endpoints.append(
            EndpointInfo(
                name="Terminal",
                port=5000,
                path_prefix="/",
                description="browser terminal session (ghostty-web + tmux)",
                active=_check_port("127.0.0.1", 5000),
                url_hint="http://127.0.0.1:5000/",
            )
        )

    return {
        "session_kind": session_kind,
        "hostname": os.environ.get("HOSTNAME", "localhost"),
        "endpoints": [asdict(e) for e in endpoints],
        "installed_agents": _detect_installed_agent_clis(),
    }


def _check_port(host: str, port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://{host}:{port}/healthz", method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except OSError:
        # Try root
        try:
            req = urllib.request.Request(f"http://{host}:{port}/", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status < 500
        except OSError:
            return False


def _detect_installed_agent_clis() -> list[str]:
    clis = [
        "agent",
        "kilo",
        "goose",
        "cline",
        "opencode",
        "codex",
        "qodercli",
        "orx",
        "openworker-server",
    ]
    found = [c for c in clis if shutil.which(c) is not None]
    # Cursor's binary is `agent` on scratch, which is often not on this process PATH.
    if "agent" not in found:
        try:
            from astroai_lab.agent.registry import resolve_agent_binary

            if resolve_agent_binary("agent"):
                found.insert(0, "agent")
        except Exception:  # noqa: BLE001 — listing must not fail inspect
            pass
    return found
