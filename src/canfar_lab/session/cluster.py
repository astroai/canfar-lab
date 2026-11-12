"""Ray cluster start/stop/status, dashboard link and recent jobs.

Thin wrappers over the ``canfar_workload`` payload functions that already back
``canfar lab cluster`` and the MCP tools. :func:`status` never raises for a
cluster that is simply not running; it reports ``running: False`` instead.
Start/stop raise :class:`LabError` with the manager's message on failure.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from canfar_lab.errors import LabError

_RUNTIME_ERRORS: tuple[type[BaseException], ...] = (RuntimeError, ConnectionError, OSError)


def _errors() -> tuple[type[BaseException], ...]:
    """Errors the workload layer raises for an absent or unreachable manager."""
    extra: list[type[BaseException]] = []
    try:
        import typer

        extra.append(typer.BadParameter)  # raised when no manager address resolves
    except ImportError:
        pass
    try:
        import httpx

        extra.append(httpx.HTTPError)
    except ImportError:
        pass
    return _RUNTIME_ERRORS + tuple(extra)


def _message(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


def dashboard_url(address: str | None = None) -> str | None:
    from canfar_workload.cli import dashboard_url_payload

    try:
        return dashboard_url_payload(address)
    except _errors():
        return None


_ADDRESS_ENV = ("CANFAR_RAY_JOBS_ADDRESS", "RAY_DASHBOARD_URL")


def _live_manager() -> tuple[str | None, bool, str | None]:
    """(jobs address, manager session visible, manager session id) from CANFAR."""
    from canfar_workload.dashboard import _live_manager_connect, jobs_url_from_connect

    connect, visible, manager_id = _live_manager_connect()
    return (jobs_url_from_connect(connect) if connect else None), visible, manager_id


def _forget_stale_address() -> None:
    """The boot-time export pointed at a manager that is gone: drop it everywhere."""
    from canfar_workload.dashboard import clear_persisted_connect_urls

    for name in _ADDRESS_ENV:
        os.environ.pop(name, None)
    with contextlib.suppress(Exception):
        clear_persisted_connect_urls()


def _not_running(state: str, message: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "running": False,
        "state": state,
        "message": message,
        "detail": detail,
        "dashboard_url": None,
        **extra,
    }


def status(address: str | None = None) -> dict[str, Any]:
    """Manager phase, workers and dashboard URL; ``running: False`` when none is up.

    ``state`` is ``running``, ``none``, ``starting`` (a manager session exists
    but has no address yet) or ``unreachable`` (it exists but does not answer).
    A Jobs address exported at session start can name a manager that has
    since gone; then the live CANFAR listing decides, and the stale address
    is forgotten.
    """
    from canfar_workload.cli import cluster_status_payload

    try:
        payload = cluster_status_payload(address)
    except _errors() as exc:
        detail = _message(exc)
        exported = address is None and any(os.environ.get(n) for n in _ADDRESS_ENV)
        # An explicit address that fails is just down; with none, ask CANFAR.
        live, visible, manager_id = _live_manager() if address is None else (None, False, None)
        if exported and live:
            try:
                payload = cluster_status_payload(live)
            except _errors() as retry_exc:
                detail = _message(retry_exc)
            else:
                os.environ["CANFAR_RAY_JOBS_ADDRESS"] = live
                return _running(payload, live)
        if exported and not visible:
            _forget_stale_address()
        if visible and not live:
            return _not_running(
                "starting",
                "A cluster is starting. This usually takes a minute or two.",
                detail,
                manager_id=manager_id,
            )
        if visible:
            return _not_running(
                "unreachable",
                "A cluster session exists but is not answering. It may still be starting; "
                "if this lasts, stop it and start a new one.",
                detail,
                manager_id=manager_id,
            )
        return _not_running("none", "No cluster is running.", detail)
    return _running(payload, address)


def _running(payload: dict[str, Any], address: str | None) -> dict[str, Any]:
    cluster = payload.get("cluster") or {}
    return {
        "ok": True,
        "running": True,
        "state": "running",
        "address": address,
        "phase": cluster.get("phase") or "Idle",
        "ray_address": payload.get("ray_address"),
        "nodes_alive": payload.get("ray_nodes_alive"),
        "workers_joined": payload.get("joined_workers"),
        "workers": payload.get("workers") or [],
        "authenticated": bool((payload.get("auth") or {}).get("authenticated")),
        "dashboard_url": dashboard_url(address),
        "max_workers": cluster.get("max_workers"),
        "raw": payload,
    }


def start(
    *,
    max_workers: int = 8,
    cores: int = 1,
    ram: int = 4,
    gpus: int = 0,
    idle_timeout_minutes: int = 5,
    timeout: int = 1800,
) -> dict[str, Any]:
    """Start (or reuse) the autoscaling Ray cluster and wait until it is ready."""
    from canfar_workload.cli import cluster_start_payload

    try:
        payload = cluster_start_payload(
            max_workers=max_workers,
            cores=cores,
            ram=ram,
            gpus=gpus,
            idle_timeout_minutes=idle_timeout_minutes,
            timeout=timeout,
        )
    except _errors() as exc:
        raise LabError(f"Cluster start failed: {_message(exc)}") from exc
    return {"ok": True, **payload}


def stop() -> dict[str, Any]:
    """Tear down workers and the ray-manager session."""
    from canfar_workload.cli import cluster_stop_payload

    try:
        payload = cluster_stop_payload()
    except _errors() as exc:
        raise LabError(f"Cluster stop failed: {_message(exc)}") from exc
    return {"ok": True, **payload}


def jobs(address: str | None = None, *, limit: int = 10) -> dict[str, Any]:
    """Most recent jobs on the running cluster (empty when none is up)."""
    from canfar_workload.cli import job_list_payload

    try:
        rows = list(job_list_payload(address).get("jobs") or [])
    except _errors() as exc:
        return {"ok": True, "jobs": [], "detail": _message(exc)}
    rows.sort(key=lambda j: j.get("start_time") or 0, reverse=True)
    return {
        "ok": True,
        "jobs": [
            {
                "id": str(j.get("submission_id") or j.get("job_id") or ""),
                "status": str(j.get("status") or ""),
                "entrypoint": str(j.get("entrypoint") or ""),
                "start_time": j.get("start_time"),
                "end_time": j.get("end_time"),
                "message": str(j.get("message") or ""),
            }
            for j in rows[: max(limit, 0)]
        ],
    }
