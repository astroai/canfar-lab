"""canfar_lab.session.cluster: thin wrappers over canfar_workload payloads."""

from __future__ import annotations

from typing import Any

import pytest
import typer

import canfar_workload.cli as wl
from canfar_lab.errors import LabError
from canfar_lab.session import cluster


def test_status_not_running_when_no_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_address(address: str | None = None) -> dict[str, Any]:
        raise typer.BadParameter("No Ray manager address.")

    monkeypatch.setattr(wl, "cluster_status_payload", _no_address)
    monkeypatch.setattr(wl, "dashboard_url_payload", _no_address)
    monkeypatch.setattr(cluster, "_live_manager", lambda: (None, False, None))
    for name in ("CANFAR_RAY_JOBS_ADDRESS", "RAY_DASHBOARD_URL"):
        monkeypatch.delenv(name, raising=False)
    result = cluster.status()
    assert result == {
        "ok": True,
        "running": False,
        "state": "none",
        "message": "No cluster is running.",
        "detail": "No Ray manager address.",
        "dashboard_url": None,
    }


def test_stale_exported_address_is_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session start exported a dead manager's URL: say "none", not a raw 404."""
    dead = "https://workloads.canfar.net/session/contrib/old123/dashboard"
    monkeypatch.setenv("CANFAR_RAY_JOBS_ADDRESS", dead)
    cleared: list[bool] = []

    def _gone(address: str | None = None) -> dict[str, Any]:
        raise RuntimeError("Client error '404 Not Found' for url '.../api/v1/status'")

    monkeypatch.setattr(wl, "cluster_status_payload", _gone)
    monkeypatch.setattr(cluster, "_live_manager", lambda: (None, False, None))
    import canfar_workload.dashboard as dash

    monkeypatch.setattr(dash, "clear_persisted_connect_urls", lambda: cleared.append(True) or 1)
    result = cluster.status()
    assert result["state"] == "none" and result["message"] == "No cluster is running."
    assert "CANFAR_RAY_JOBS_ADDRESS" not in __import__("os").environ
    assert cleared == [True]


def test_stale_exported_address_falls_back_to_live_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CANFAR_RAY_JOBS_ADDRESS", "https://x/session/contrib/old/dashboard")
    live = "https://x/session/contrib/new/dashboard"
    payload = {"cluster": {"phase": "Ready"}, "joined_workers": 1, "auth": {"authenticated": True}}

    def _status(address: str | None = None) -> dict[str, Any]:
        if address != live:
            raise RuntimeError("404")
        return payload

    monkeypatch.setattr(wl, "cluster_status_payload", _status)
    monkeypatch.setattr(wl, "dashboard_url_payload", lambda address=None: address)
    monkeypatch.setattr(cluster, "_live_manager", lambda: (live, True, "new"))
    result = cluster.status()
    assert result["state"] == "running" and result["address"] == live
    assert result["dashboard_url"] == live


def test_manager_without_address_is_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CANFAR_RAY_JOBS_ADDRESS", "RAY_DASHBOARD_URL"):
        monkeypatch.delenv(name, raising=False)

    def _none(address: str | None = None) -> dict[str, Any]:
        raise typer.BadParameter("No Ray manager address.")

    monkeypatch.setattr(wl, "cluster_status_payload", _none)
    monkeypatch.setattr(cluster, "_live_manager", lambda: (None, True, "pend1"))
    result = cluster.status()
    assert result["state"] == "starting" and result["manager_id"] == "pend1"


def test_status_running(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "cluster": {"phase": "Ready", "worker_count": 2},
        "ray_address": "ray://head:10001",
        "ray_nodes_alive": 3,
        "joined_workers": 2,
        "workers": [{"name": "w1"}],
        "auth": {"authenticated": True},
    }
    monkeypatch.setattr(wl, "cluster_status_payload", lambda address=None: payload)
    monkeypatch.setattr(wl, "dashboard_url_payload", lambda address=None: "https://x/dashboard")
    result = cluster.status()
    assert result["running"] and result["phase"] == "Ready"
    assert result["workers_joined"] == 2 and result["authenticated"]
    assert result["dashboard_url"] == "https://x/dashboard"


def test_start_and_stop_wrap_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _start(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"ray_address": "ray://h"}

    monkeypatch.setattr(wl, "cluster_start_payload", _start)
    assert cluster.start(max_workers=3) == {"ok": True, "ray_address": "ray://h"}
    assert seen["max_workers"] == 3

    def _fail(**_: Any) -> dict[str, Any]:
        raise RuntimeError("manager not ready")

    monkeypatch.setattr(wl, "cluster_stop_payload", _fail)
    with pytest.raises(LabError, match="manager not ready"):
        cluster.stop()


def test_jobs_newest_first_and_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "submission_id": f"r{i}",
            "status": "succeeded",
            "entrypoint": "python x.py",
            "start_time": i,
        }
        for i in range(5)
    ]
    monkeypatch.setattr(wl, "job_list_payload", lambda address=None: {"jobs": rows})
    result = cluster.jobs(limit=2)
    assert [j["id"] for j in result["jobs"]] == ["r4", "r3"]

    def _down(address: str | None = None) -> dict[str, Any]:
        raise ConnectionError("refused")

    monkeypatch.setattr(wl, "job_list_payload", _down)
    assert cluster.jobs() == {"ok": True, "jobs": [], "detail": "refused"}
