"""MCP stdio server tests (no live Ray cluster)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from astroai_workload.mcp import SERVER_INFO, handle_message

ROOT = Path(__file__).resolve().parents[2]


def _rpc(method: str, params: dict | None = None, msg_id: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if msg_id is not None:
        msg["id"] = msg_id
    return msg


def test_initialize_handshake() -> None:
    resp = handle_message(_rpc("initialize", {"protocolVersion": "2024-11-05"}))
    assert resp is not None
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"] == SERVER_INFO
    assert "tools" in resp["result"]["capabilities"]


def test_initialized_notification_no_response() -> None:
    assert handle_message(_rpc("notifications/initialized", msg_id=None)) is None


def test_ping() -> None:
    resp = handle_message(_rpc("ping"))
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_invalid_request() -> None:
    resp = handle_message({"jsonrpc": "1.0", "method": "x"})
    assert resp["error"]["code"] == -32600


def test_method_not_found() -> None:
    resp = handle_message(_rpc("bogus"))
    assert resp["error"]["code"] == -32601


def test_tools_list_exposes_cluster_and_job_tools() -> None:
    resp = handle_message(_rpc("tools/list"))
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names[:4] == ["cluster_start", "cluster_status", "cluster_stop", "dashboard_url"]
    assert names[4:] == [
        "job_run",
        "job_submit",
        "job_status",
        "job_logs",
        "job_cancel",
        "job_list",
    ]
    for tool in resp["result"]["tools"]:
        assert "inputSchema" in tool
        assert "handler" not in tool  # handlers never leak over the wire


def test_tools_call_unknown_tool() -> None:
    resp = handle_message(_rpc("tools/call", {"name": "nope", "arguments": {}}))
    assert resp["error"]["code"] == -32602


def test_tools_call_cluster_status(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"cluster": {"phase": "Running"}, "joined_workers": 2}
    monkeypatch.setattr("astroai_workload.mcp.cluster_status_payload", lambda address=None: payload)
    resp = handle_message(_rpc("tools/call", {"name": "cluster_status", "arguments": {}}))
    assert "error" not in resp
    assert json.loads(resp["result"]["content"][0]["text"]) == payload


def test_unknown_notification_gets_no_response() -> None:
    """JSON-RPC: never respond to notifications (messages without an id)."""
    assert handle_message(_rpc("notifications/cancelled", msg_id=None)) is None


def test_tools_call_cluster_start_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**kwargs):
        raise RuntimeError("No manager found. Set ASTROAI_RAY_JOBS_ADDRESS or pass --address.")

    monkeypatch.setattr("astroai_workload.mcp.cluster_start_payload", _boom)
    resp = handle_message(_rpc("tools/call", {"name": "cluster_start", "arguments": {}}))
    assert resp["result"]["isError"] is True
    assert "No manager found" in resp["result"]["content"][0]["text"]


def test_tools_call_cluster_start_forwards_autoscaling_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP tool forwards worker sizing to the payload with sane defaults."""
    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {
            "manager_url": "https://x/session/contrib/abc",
            "jobs_address": "https://x/session/contrib/abc/dashboard",
            "dashboard_url": "https://x/session/contrib/abc/dashboard",
            "cluster_phase": "Running",
            "joined_workers": 2,
            "autoscaling": True,
        }

    monkeypatch.setattr("astroai_workload.mcp.cluster_start_payload", _fake)

    handle_message(
        _rpc(
            "tools/call",
            {
                "name": "cluster_start",
                "arguments": {"min_workers": 1, "max_workers": 4, "cores": 2},
            },
        )
    )
    assert captured.get("min_workers") == 1
    assert captured.get("max_workers") == 4
    assert captured.get("cores") == 2

    # Defaults keep the cluster fully elastic.
    captured.clear()
    handle_message(_rpc("tools/call", {"name": "cluster_start", "arguments": {}}))
    assert captured.get("min_workers") == 0
    assert captured.get("max_workers") == 8


def test_tools_list_cluster_start_schema_is_autoscaling_only() -> None:
    resp = handle_message(_rpc("tools/list"))
    start = next(t for t in resp["result"]["tools"] if t["name"] == "cluster_start")
    props = start["inputSchema"]["properties"]
    assert props["min_workers"]["default"] == 0
    assert props["max_workers"]["default"] == 8
    assert "require_preflight" not in props
    assert "autoscaling" not in props
    assert "workers" not in props


def test_tools_call_cluster_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    import astroai_workload.cli as cli_mod

    calls: dict = {}
    monkeypatch.setattr(
        cli_mod,
        "cluster_stop_payload",
        lambda **kwargs: calls.update(kwargs) or {"destroyed_manager": True},
    )
    resp = handle_message(
        _rpc("tools/call", {"name": "cluster_stop", "arguments": {"address": "https://m"}})
    )
    assert json.loads(resp["result"]["content"][0]["text"]) == {"destroyed_manager": True}
    assert calls.get("address") == "https://m"


def test_initialize_echoes_known_protocol_version() -> None:
    resp = handle_message(_rpc("initialize", {"protocolVersion": "2025-03-26"}))
    assert resp["result"]["protocolVersion"] == "2025-03-26"


def test_initialize_falls_back_for_unknown_protocol_version() -> None:
    resp = handle_message(_rpc("initialize", {"protocolVersion": "2099-01-01"}))
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_tools_call_dashboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "astroai_workload.mcp.dashboard_url_payload", lambda address=None: "http://127.0.0.1:8265"
    )
    resp = handle_message(_rpc("tools/call", {"name": "dashboard_url", "arguments": {}}))
    assert json.loads(resp["result"]["content"][0]["text"]) == {
        "dashboard_url": "http://127.0.0.1:8265"
    }


def test_tools_call_job_run_requires_script() -> None:
    resp = handle_message(_rpc("tools/call", {"name": "job_run", "arguments": {}}))
    assert resp["error"]["code"] == -32602
    assert "script" in resp["error"]["message"]


def test_tools_call_job_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "astroai_workload.mcp.job_list_payload",
        lambda address=None: {"jobs": [{"submission_id": "a", "status": "succeeded"}]},
    )
    resp = handle_message(_rpc("tools/call", {"name": "job_list", "arguments": {}}))
    assert json.loads(resp["result"]["content"][0]["text"])["jobs"][0]["submission_id"] == "a"


def test_tools_call_job_submit_requires_cmd() -> None:
    resp = handle_message(_rpc("tools/call", {"name": "job_submit", "arguments": {}}))
    assert resp["error"]["code"] == -32602
    assert "cmd" in resp["error"]["message"]


def test_tools_call_job_run_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def _fake(script, **kwargs):
        captured["script"] = script
        captured.update(kwargs)
        return {"run_id": "r1", "status": "succeeded", "logs": "ok\n"}

    monkeypatch.setattr("astroai_workload.mcp.job_run_payload", _fake)
    resp = handle_message(
        _rpc(
            "tools/call",
            {
                "name": "job_run",
                "arguments": {
                    "script": "train.py",
                    "cpus": 2,
                    "timeout": "30",
                    "inputs": ["vos://in.fits"],
                    "outputs": ["/arc/out"],
                    "run_id": "r1",
                },
            },
        )
    )
    assert json.loads(resp["result"]["content"][0]["text"])["run_id"] == "r1"
    assert captured["script"] == "train.py"
    assert captured["timeout"] == 30.0
    assert captured["inputs"] == ["vos://in.fits"]
    assert captured["outputs"] == ["/arc/out"]
    assert captured["cpus"] == 2.0
    assert captured["run_id"] == "r1"


def test_tools_call_job_status_requires_run_id() -> None:
    resp = handle_message(_rpc("tools/call", {"name": "job_status", "arguments": {}}))
    assert resp["error"]["code"] == -32602
    assert "run_id" in resp["error"]["message"]


def test_serve_stdio_subprocess_e2e() -> None:
    """Feed a real client conversation through `python -m ... mcp serve`."""
    lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        ),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}),
        "not json",
    ]
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "astroai_workload.cli", "mcp", "serve"],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    out = [json.loads(line) for line in proc.stdout.strip().splitlines()]
    by_id = {m["id"]: m for m in out}
    assert by_id[1]["result"]["serverInfo"]["name"] == "astroai"
    names = [t["name"] for t in by_id[2]["result"]["tools"]]
    assert "cluster_start" in names and "dashboard_url" in names
    assert "cluster_stop" in names
    assert "job_run" in names
    assert by_id[3]["result"] == {}
    # The malformed line produced a parse-error response with no id.
    assert any(m.get("error", {}).get("code") == -32700 for m in out)
