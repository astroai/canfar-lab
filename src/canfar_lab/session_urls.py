"""Studio session URL construction from the *active* CANFAR server.

``cli/open_cmd.py`` used to hardcode ``workloads.canfar.net``; the desk needs
the same mapping for whichever server ``canfar auth`` points at. The mapping
is best-effort by design — a wrong host is visible immediately in the browser,
and ``CANFAR_LAB_SESSION_BASE_URL`` overrides everything.
"""

from __future__ import annotations

import os

BASE_URL_ENV = "CANFAR_LAB_SESSION_BASE_URL"
DEFAULT_HOST = "workloads.canfar.net"


def active_server() -> str | None:
    """The configured canfar server (``canfar.net``, SRCNet, …), if any."""
    override = os.environ.get("CANFAR_LAB_SERVER", "").strip()
    if override:
        return override
    try:
        from canfar.models.config import Configuration

        return Configuration().active.server or None
    except Exception:  # noqa: BLE001 — desk must render without the client
        return None


def workloads_host(server: str | None) -> str:
    """Map a server name to the session/web host."""
    if not server:
        return DEFAULT_HOST
    host = server.split("//")[-1].strip("/")
    if host.startswith("workloads."):
        return host
    if "canfar.net" in host:
        return DEFAULT_HOST
    return host


def session_base_url(server: str | None = None) -> str:
    override = os.environ.get(BASE_URL_ENV, "").strip()
    if override:
        return override.rstrip("/")
    return f"https://{workloads_host(server if server is not None else active_server())}"


def contrib_url(session_id: str, *, server: str | None = None) -> str:
    return f"{session_base_url(server)}/session/contrib/{str(session_id).strip()}"


def local_base_url() -> str:
    """In-session loopback portal (the Studio image proxy on :5000)."""
    return "http://127.0.0.1:5000"


__all__ = [
    "BASE_URL_ENV",
    "DEFAULT_HOST",
    "active_server",
    "contrib_url",
    "local_base_url",
    "session_base_url",
    "workloads_host",
]
