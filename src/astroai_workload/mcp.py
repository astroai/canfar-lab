"""Minimal MCP (Model Context Protocol) server over stdio for Ray cluster ops.

Exposes cluster lifecycle and jobs so agents can start workers and run
programs with the same functions as the CLI. Cluster tools need the
canfar client. Job tools need Ray (a ray-manager image, or Ray in this
venv).

Transport: MCP stdio — newline-delimited JSON-RPC 2.0 messages on stdin/stdout.
Deliberately zero-dependency (stdlib ``json`` only) to keep lean images lean;
the protocol surface is just ``initialize`` / ``notifications/initialized`` /
``ping`` / ``tools/list`` / ``tools/call``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from astroai_workload import __version__
from astroai_workload.cli import (
    cluster_start_payload,
    cluster_status_payload,
    dashboard_url_payload,
    job_cancel_payload,
    job_list_payload,
    job_logs_payload,
    job_run_payload,
    job_status_payload,
    job_submit_payload,
)

PROTOCOL_VERSION = "2024-11-05"
# Client-negotiated versions with an identical tools surface: echo the client's
# requested version when known so newer MCP clients (2025-03-26/2025-06-18)
# don't reject the server, else fall back to the base version.
_SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
SERVER_INFO = {"name": "astroai", "version": __version__}

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _json_result(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _json_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def _env_items(args: dict[str, Any]) -> list[str] | None:
    env = args.get("env")
    if env is None:
        return None
    if isinstance(env, dict):
        return [f"{key}={value}" for key, value in env.items()]
    if isinstance(env, list):
        return [str(item) for item in env]
    raise ValueError("env must be an object of KEY: VALUE")


def _str_list(value: Any, name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of strings")
    return [str(item) for item in value]


def _tool_cluster_start(args: dict[str, Any]) -> dict[str, Any]:
    return cluster_start_payload(
        address=args.get("address"),
        min_workers=int(args.get("min_workers", 0)),
        max_workers=int(args.get("max_workers", 8)),
        cores=int(args.get("cores", 1)),
        ram=int(args.get("ram", 4)),
        gpus=int(args.get("gpus", 0)),
        timeout=int(args.get("timeout", 1800)),
    )


def _tool_cluster_status(args: dict[str, Any]) -> dict[str, Any]:
    return cluster_status_payload(args.get("address"))


def _tool_cluster_stop(args: dict[str, Any]) -> dict[str, Any]:
    from astroai_workload.cli import cluster_stop_payload

    return cluster_stop_payload(address=args.get("address"))


def _tool_dashboard_url(args: dict[str, Any]) -> dict[str, Any]:
    url = dashboard_url_payload(args.get("address"))
    return {"dashboard_url": url}


def _tool_job_run(args: dict[str, Any]) -> dict[str, Any]:
    script = args.get("script")
    if not script:
        raise ValueError("script is required")
    return job_run_payload(
        str(script),
        address=args.get("address"),
        cpus=float(args.get("cpus", 1.0)),
        memory=args.get("memory"),
        gpus=float(args.get("gpus", 0.0)),
        args=_str_list(args.get("args"), "args"),
        env=_env_items(args),
        timeout=None if args.get("timeout") is None else float(args["timeout"]),
        working_directory=args.get("cwd"),
        run_id=args.get("run_id"),
        inputs=_str_list(args.get("inputs"), "inputs"),
        outputs=_str_list(args.get("outputs"), "outputs"),
    )


def _tool_job_submit(args: dict[str, Any]) -> dict[str, Any]:
    import shlex

    cmd = args.get("cmd")
    if not cmd:
        raise ValueError("cmd is required")
    return job_submit_payload(
        tuple(shlex.split(str(cmd))),
        address=args.get("address"),
        cpus=float(args.get("cpus", 1.0)),
        memory=args.get("memory"),
        gpus=float(args.get("gpus", 0.0)),
        env=_env_items(args),
        timeout=None if args.get("timeout") is None else float(args["timeout"]),
        working_directory=args.get("cwd"),
        run_id=args.get("run_id"),
        inputs=_str_list(args.get("inputs"), "inputs"),
        outputs=_str_list(args.get("outputs"), "outputs"),
        wait=bool(args.get("wait", False)),
    )


def _tool_job_status(args: dict[str, Any]) -> dict[str, Any]:
    run_id = args.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    return job_status_payload(str(run_id), args.get("address"))


def _tool_job_logs(args: dict[str, Any]) -> dict[str, Any]:
    run_id = args.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    return job_logs_payload(str(run_id), args.get("address"))


def _tool_job_cancel(args: dict[str, Any]) -> dict[str, Any]:
    run_id = args.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    return job_cancel_payload(str(run_id), args.get("address"))


def _tool_job_list(args: dict[str, Any]) -> dict[str, Any]:
    return job_list_payload(args.get("address"))


TOOLS: list[dict[str, Any]] = [
    {
        "name": "cluster_start",
        "description": (
            "Start (or reuse) the autoscaling Ray cluster: writes the manager "
            "env file, creates the ray-manager session when none is running, "
            "and Ray adds workers when jobs need CPUs. Safe to call again. "
            "Returns the cluster URL to use as ASTROAI_RAY_JOBS_ADDRESS."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Manager connect URL or Jobs API URL.",
                },
                "min_workers": {
                    "type": "integer",
                    "description": "Workers kept alive even when idle.",
                    "default": 0,
                },
                "max_workers": {
                    "type": "integer",
                    "description": "Autoscaler ceiling.",
                    "default": 8,
                },
                "cores": {"type": "integer", "description": "CPUs per worker.", "default": 1},
                "ram": {"type": "integer", "description": "RAM GiB per worker.", "default": 4},
                "gpus": {"type": "integer", "description": "GPUs per worker.", "default": 0},
                "timeout": {
                    "type": "integer",
                    "description": "Wait timeout (seconds).",
                    "default": 1800,
                },
            },
        },
        "handler": _tool_cluster_start,
    },
    {
        "name": "cluster_status",
        "description": (
            "See if the Ray cluster is up, whether workers have joined, and the Dashboard path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Manager connect URL or Jobs API URL.",
                },
            },
        },
        "handler": _tool_cluster_status,
    },
    {
        "name": "cluster_stop",
        "description": (
            "Tear down the whole cluster: destroy every worker session and "
            "the ray-manager session, and clear persisted state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Manager connect URL or Jobs API URL.",
                },
            },
        },
        "handler": _tool_cluster_stop,
    },
    {
        "name": "dashboard_url",
        "description": "Ray Dashboard URL for the current cluster.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Manager connect URL or Jobs API URL.",
                },
            },
        },
        "handler": _tool_dashboard_url,
    },
    {
        "name": "job_run",
        "description": (
            "Run a Python script on the existing Ray cluster and wait until it "
            "finishes. Does not start workers. Use cluster_start first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Path to the Python script."},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Arguments passed to the script.",
                },
                "cpus": {"type": "number", "default": 1},
                "memory": {"type": "string", "description": "Driver RAM, for example 8GiB."},
                "gpus": {"type": "number", "default": 0},
                "cwd": {"type": "string"},
                "run_id": {"type": "string"},
                "address": {"type": "string"},
                "timeout": {"type": "number"},
                "inputs": {"type": "array", "items": {"type": "string"}},
                "outputs": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "required": ["script"],
        },
        "handler": _tool_job_run,
    },
    {
        "name": "job_submit",
        "description": (
            "Start a command on the existing Ray cluster (python -m …). "
            "Does not wait unless wait=true. Does not start workers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Command string to run."},
                "cpus": {"type": "number", "default": 1},
                "memory": {"type": "string"},
                "gpus": {"type": "number", "default": 0},
                "cwd": {"type": "string"},
                "run_id": {"type": "string"},
                "address": {"type": "string"},
                "timeout": {"type": "number"},
                "wait": {"type": "boolean", "default": False},
                "inputs": {"type": "array", "items": {"type": "string"}},
                "outputs": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "required": ["cmd"],
        },
        "handler": _tool_job_submit,
    },
    {
        "name": "job_status",
        "description": "Show whether one job is still running, succeeded, or failed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["run_id"],
        },
        "handler": _tool_job_status,
    },
    {
        "name": "job_logs",
        "description": "Print the driver log for one job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["run_id"],
        },
        "handler": _tool_job_logs,
    },
    {
        "name": "job_cancel",
        "description": "Ask the cluster to stop a job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["run_id"],
        },
        "handler": _tool_job_cancel,
    },
    {
        "name": "job_list",
        "description": "List jobs on the current cluster.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
            },
        },
        "handler": _tool_job_list,
    },
]

_TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        }
        for t in TOOLS
    ]


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one decoded JSON-RPC message; return the response (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _json_error(None, INVALID_REQUEST, "Invalid Request")
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        requested = (params.get("protocolVersion") or "").strip()
        version = requested if requested in _SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _json_result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _json_result(msg_id, {})
    if method == "tools/list":
        return _json_result(msg_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        return _handle_tools_call(msg_id, params)
    # JSON-RPC forbids responding to notifications (messages without an id),
    # so unknown notification methods (e.g. notifications/cancelled) get no
    # response — only id-carrying requests get METHOD_NOT_FOUND.
    if msg_id is None:
        return None
    return _json_error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def _handle_tools_call(msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        return _json_error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _json_error(msg_id, INVALID_PARAMS, f"Invalid arguments for {name}: expected object")
    try:
        result = tool["handler"](arguments)
    except (TypeError, ValueError) as exc:
        return _json_error(msg_id, INVALID_PARAMS, f"Invalid arguments for {name}: {exc}")
    except RuntimeError as exc:
        # Business failure (no manager, not ready, unreachable) — report as an
        # error result so the agent sees a clean message, not a JSON-RPC error.
        return _json_result(
            msg_id,
            {
                "content": [{"type": "text", "text": f"{exc}"}],
                "isError": True,
            },
        )
    except Exception as exc:  # noqa: BLE001 — keep the server alive; surface to the client
        return _json_result(
            msg_id,
            {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            },
        )
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    return _json_result(
        msg_id,
        {
            "content": [{"type": "text", "text": text}],
        },
    )


# ---------------------------------------------------------------------------
# stdio loop
# ---------------------------------------------------------------------------


def serve_stdio() -> int:
    """Run the MCP server over stdin/stdout until EOF.

    Each line is one JSON-RPC message; notifications get no response. Returns 0
    on clean EOF.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(
                json.dumps(_json_error(None, PARSE_ERROR, f"Parse error: {exc}")) + "\n"
            )
            sys.stdout.flush()
            continue
        response = handle_message(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0
