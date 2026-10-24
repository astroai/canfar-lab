"""Desk state: what the dock chips show.

Every probe is best-effort — the dock must render on a laptop without the
CANFAR client, without auth, and without a session.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from canfar_lab.dsh_release import installed_version
from canfar_lab.session_urls import contrib_url, session_base_url
from canfar_lab.sync import bus as bus_mod
from canfar_lab.sync import clock


def canfar_ops():  # noqa: ANN201 — importable seam for tests
    from canfar_workload.canfar_ops import CanfarOps

    return CanfarOps()


def auth_state(ops=None) -> dict[str, Any]:  # noqa: ANN001
    try:
        status = (ops or canfar_ops()).auth_status()
    except Exception as exc:  # noqa: BLE001 — no client / no credentials is a state
        return {"authenticated": None, "message": str(exc)}
    return {
        "authenticated": bool(status.authenticated),
        "idp": status.idp,
        "server": status.server,
        "message": status.message,
    }


def studio_session(ops=None, *, name: str = "astroai-studio") -> dict[str, Any] | None:  # noqa: ANN001
    """Best live Studio session row (Running preferred), with its browser URL."""
    try:
        rows = (ops or canfar_ops()).list_sessions()
    except Exception:  # noqa: BLE001
        return None
    candidates: list[dict[str, Any]] = []
    for row in rows or []:
        row_name = str(row.get("name") or "")
        image = str(row.get("image") or row.get("imageName") or "").lower()
        if row_name == name or "studio" in image:
            candidates.append(row)
    if not candidates:
        return None
    rank = {"Running": 0, "Pending": 1}
    candidates.sort(key=lambda row: rank.get(str(row.get("status") or ""), 2))
    best = candidates[0]
    session_id = str(best.get("id") or "").strip()
    server = None
    try:
        from canfar.models.config import Configuration

        server = Configuration().active.server
    except Exception:  # noqa: BLE001
        server = None
    url = contrib_url(session_id, server=server) if session_id else None
    return {
        "id": session_id or None,
        "name": str(best.get("name") or ""),
        "status": str(best.get("status") or "Unknown"),
        "image": str(best.get("image") or best.get("imageName") or ""),
        "url": url,
        "base_url": session_base_url(server),
    }


def mailbox_state(
    mailbox: Path,
    *,
    configured: bool = False,
    on_session: bool = False,
) -> dict[str, Any]:
    exists = mailbox.is_dir()
    state: dict[str, Any] = {
        "path": str(mailbox),
        "exists": exists,
        "shared": bool(configured or on_session),
        "session_ready": False,
        "counts": {"requests": 0, "processing": 0, "events": 0},
        "last_exchange": {},
    }
    if not exists:
        return state
    state["counts"] = bus_mod.counts(mailbox=mailbox)
    state["session_ready"] = bus_mod.read_ready(mailbox=mailbox) is not None
    side = bus_mod.DESK
    state["last_exchange"] = bus_mod.read_state(mailbox=mailbox, side=side)
    return state


def lab_version() -> str | None:
    """Version of the running canfar-lab install (``None`` when not installed)."""
    try:
        from canfar_lab.version import display_version

        return display_version()
    except Exception:  # noqa: BLE001 — the dock must render without packaging metadata
        return None


def collect(
    *,
    root: Path,
    mailbox: Path,
    settings=None,  # noqa: ANN001 — LabSettings or None
    dsh_bin: str | None = None,
    dsh_spec: str | None = None,
    release=None,  # noqa: ANN001 — dsh_release.Release or None
    probe: bool = True,
) -> dict[str, Any]:
    """Assemble the dock state. Never raises."""
    on_session = bool(
        os.environ.get("SKAHA_SESSIONID") or os.environ.get("skaha_sessionid")  # noqa: SIM112
    )
    configured = bool(getattr(settings, "sync_mailbox", None)) or bool(
        os.environ.get(bus_mod.MAILBOX_ENV, "").strip()
    )
    session_name = str(getattr(settings, "session_name", "astroai-studio"))
    state: dict[str, Any] = {
        "ok": True,
        "ts": clock.utc_iso(),
        "root": str(root),
        "mode": "session" if on_session else "desk",
        "dsh": {
            "spec": dsh_spec or str(getattr(settings, "dsh_version", "pin")),
            "installed": installed_version(dsh_bin),
            "release": (
                {
                    "version": release.version,
                    "source": release.source,
                    "note": release.note,
                }
                if release is not None
                else None
            ),
        },
        "lab": {"version": lab_version()},
        "mailbox": mailbox_state(mailbox, configured=configured, on_session=on_session),
        "auth": {"authenticated": None, "message": "not probed"},
        "session": None,
    }
    if probe:
        state["auth"] = auth_state()
        state["session"] = studio_session(name=session_name)
    return state


__all__ = ["auth_state", "canfar_ops", "collect", "lab_version", "mailbox_state", "studio_session"]
