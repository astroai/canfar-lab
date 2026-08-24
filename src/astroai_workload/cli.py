"""Run programs on a CANFAR Ray cluster, and start or tear down that cluster."""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from .executor import RayExecutor, run_script
from .models import DataProductRef, ResourceRequest, RunSpec, RunStatus

_MAIN_HELP = """
Run a program on an autoscaling Ray cluster on CANFAR.

  astroai cluster start          # autoscaling head; Ray adds workers on demand
  astroai run train.py --cpus 2  # jobs add workers automatically
  astroai jobs submit --cmd 'python -m mosaic.stack' --wait
  astroai cluster status
  astroai cluster stop           # tear down workers + manager
"""

app = typer.Typer(
    name="astroai",
    help=_MAIN_HELP.strip(),
    no_args_is_help=True,
    add_completion=False,
)

cluster_app = typer.Typer(
    name="cluster",
    help=(
        "Start or stop an autoscaling Ray cluster. Ray adds worker sessions "
        "when a job needs CPUs and idles them out later. "
        "`status` shows whether it is up."
    ),
    no_args_is_help=True,
    add_completion=False,
)
dashboard_app = typer.Typer(
    name="dashboard",
    help="Ray Dashboard URL (jobs, nodes, logs). No subcommand prints the URL.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)
autoscaler_app = typer.Typer(
    name="autoscaler",
    help=(
        "Write YAML so the Ray head starts and stops CANFAR workers on demand. "
        "Manager-image internals; most people should `cluster start` instead."
    ),
    no_args_is_help=True,
    add_completion=False,
)
mcp_app = typer.Typer(
    name="mcp",
    help="Stdio tools for agents: cluster start/status plus job run/list/status/logs/cancel.",
    no_args_is_help=True,
    add_completion=False,
)
cluster_app.add_typer(dashboard_app)
app.add_typer(cluster_app)
app.add_typer(autoscaler_app, hidden=True)
app.add_typer(mcp_app, hidden=True)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


_CLUSTER_LOCK_TIMEOUT = int(os.environ.get("ASTROAI_LAB_CLUSTER_LOCK_TIMEOUT", "120"))


@contextmanager
def _cluster_control_lock() -> Iterator[None]:
    """Serialize cluster start/stop across sessions sharing /arc/home.

    Two sessions starting or one starting while the other stops would race on
    ``ray-manager.env`` and the manager session itself. flock is unreliable on
    NFS, so this is an O_EXCL lock file with staleness recovery.
    """
    from astroai_lab.core.pathlock import path_lock

    path = Path.home() / ".astroai" / "ray" / "control.lock"
    with path_lock(
        path,
        timeout=_CLUSTER_LOCK_TIMEOUT,
        busy_hint="Another cluster start/stop is in progress",
    ):
        yield


def _manager_base_url(address: str | None) -> str:
    """Derive the manager base URL (connect URL) from the Jobs address.

    Inside the manager pod the Jobs address is ``http://127.0.0.1:8265`` and the
    manager UI is ``http://127.0.0.1:5000``. From another session it is the
    public connect URL with ``/dashboard`` stripped off (Jobs lives under the
    dashboard proxy).
    """
    resolved = (address or "").strip().rstrip("/")
    if not resolved:
        from .dashboard import resolve_dashboard_url

        resolved = resolve_dashboard_url() or ""
        if not resolved:
            raise typer.BadParameter(
                "No Ray manager address. Run `astroai cluster start` first, "
                "or set ASTROAI_RAY_JOBS_ADDRESS / pass --address."
            )
    if resolved.endswith("/dashboard"):
        return resolved[: -len("/dashboard")]
    if "127.0.0.1" in resolved or "localhost" in resolved:
        port = resolved.rsplit(":", 1)[-1]
        return f"http://127.0.0.1:{5000 if port == '8265' else port}"
    return resolved


def _manager_client(address: str | None) -> Any:
    from .manager_client import ManagerClient

    return ManagerClient(_manager_base_url(address))


def _cluster_payload_from(address: str | None) -> dict[str, Any]:
    client = _manager_client(address)
    return client.status()


def _parse_env(items: list[str] | None) -> dict[str, str]:
    env_map: dict[str, str] = {}
    for item in items or ():
        if "=" not in item:
            raise ValueError(f"expected KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        env_map[key] = value
    return env_map


def _uri_refs(uris: list[str] | None) -> tuple[DataProductRef, ...]:
    return tuple(DataProductRef(uri) for uri in uris or ())


def _resources(cpus: float, gpus: float, memory: str | None) -> ResourceRequest:
    if memory is not None:
        return ResourceRequest(cpus=cpus, gpus=gpus, memory=memory)
    return ResourceRequest(cpus=cpus, gpus=gpus)


def job_run_payload(
    script: str,
    *,
    address: str | None = None,
    cpus: float = 1.0,
    memory: str | None = None,
    gpus: float = 0.0,
    args: list[str] | None = None,
    env: list[str] | None = None,
    timeout: float | None = None,
    working_directory: str | None = None,
    run_id: str | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> dict[str, Any]:
    """Run a Python script on the cluster and wait. Shared by CLI and MCP."""
    rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
    status, logs = run_script(
        script,
        address=address,
        cpus=cpus,
        memory=memory,
        gpus=gpus,
        args=args,
        env=_parse_env(env) or None,
        timeout=timeout,
        working_directory=working_directory,
        run_id=rid,
        inputs=inputs,
        expected_outputs=outputs,
    )
    return {"run_id": rid, "status": status.value, "logs": logs}


def job_submit_payload(
    command: tuple[str, ...],
    *,
    address: str | None = None,
    cpus: float = 1.0,
    memory: str | None = None,
    gpus: float = 0.0,
    env: list[str] | None = None,
    timeout: float | None = None,
    working_directory: str | None = None,
    run_id: str | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    wait: bool = False,
) -> dict[str, Any]:
    """Submit a command to the cluster. Shared by CLI and MCP."""
    rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
    spec = RunSpec(
        run_id=rid,
        command=command,
        resources=_resources(cpus, gpus, memory),
        environment=_parse_env(env),
        working_directory=working_directory,
        inputs=_uri_refs(inputs),
        expected_outputs=_uri_refs(outputs),
    )
    ex = RayExecutor(address=address)
    if wait:
        status, logs = ex.submit_and_wait(spec, timeout=timeout)
        return {"run_id": rid, "status": status.value, "logs": logs}
    return {"run_id": ex.submit(spec)}


def job_status_payload(run_id: str, address: str | None = None) -> dict[str, Any]:
    status = RayExecutor(address=address).status(run_id)
    return {"run_id": run_id, "status": status.value}


def job_logs_payload(run_id: str, address: str | None = None) -> dict[str, Any]:
    return {"run_id": run_id, "logs": RayExecutor(address=address).logs(run_id)}


def job_wait_payload(
    run_id: str,
    *,
    address: str | None = None,
    timeout: float | None = None,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    status, logs = RayExecutor(address=address).wait(
        run_id, poll_interval=poll_interval, timeout=timeout
    )
    return {"run_id": run_id, "status": status.value, "logs": logs}


def job_cancel_payload(run_id: str, address: str | None = None) -> dict[str, Any]:
    RayExecutor(address=address).cancel(run_id)
    return {"run_id": run_id, "cancel": "requested"}


def job_list_payload(address: str | None = None) -> dict[str, Any]:
    return {"jobs": RayExecutor(address=address).list_jobs()}


def _cli_fail(exc: BaseException) -> NoReturn:
    print(str(exc), file=sys.stderr)
    raise typer.Exit(1)


# =====================================================================
# Jobs commands (cluster must already be up)
# =====================================================================


@app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def cmd_run(
    ctx: typer.Context,
    script: Annotated[Path, typer.Argument(help="Python script to run on the cluster.")],
    cpus: Annotated[float, typer.Option("--cpus", help="CPUs for the driver process.")] = 1.0,
    memory: Annotated[
        str | None,
        typer.Option(
            "--memory",
            "-m",
            help="RAM for the driver (for example 8GiB). Omit unless that RAM is free.",
        ),
    ] = None,
    gpus: Annotated[float, typer.Option("--gpus", help="GPUs for the driver process.")] = 0.0,
    address: Annotated[
        str | None,
        typer.Option("--address", help="Cluster URL (default: ASTROAI_RAY_JOBS_ADDRESS)."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", help="Seconds to wait (default: until the job ends)."),
    ] = None,
    working_directory: Annotated[
        Path | None,
        typer.Option("--cwd", help="Job working directory (default: script folder)."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Name for this job (default: random)."),
    ] = None,
    env: Annotated[
        list[str] | None,
        typer.Option("--env", help="Environment KEY=VALUE (repeat)."),
    ] = None,
    inputs: Annotated[
        list[str] | None,
        typer.Option("--input", help="URI this job reads (repeat). Stored on the Ray job."),
    ] = None,
    outputs: Annotated[
        list[str] | None,
        typer.Option("--output", help="URI this job writes (repeat). Stored on the Ray job."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Run a Python script on the Ray cluster and wait until it finishes.

    Does not start workers. Use `cluster start` first.

    Extra arguments after the script go to the script
    (`astroai run train.py --epochs 2`).

    Examples:
      astroai run train.py --cpus 2
      astroai run train.py --gpus 1 --input /arc/projects/g/in --output /arc/projects/g/out
    """
    try:
        result = job_run_payload(
            str(script),
            address=address,
            cpus=cpus,
            memory=memory,
            gpus=gpus,
            args=list(ctx.args),
            env=env,
            timeout=timeout,
            working_directory=str(working_directory) if working_directory else None,
            run_id=run_id,
            inputs=inputs,
            outputs=outputs,
        )
    except (ValueError, FileNotFoundError, RuntimeError, ConnectionError, OSError) as exc:
        _cli_fail(exc)
    if as_json:
        _print_json(result)
    else:
        print(f"run_id: {result['run_id']}", file=sys.stderr)
        print(f"status: {result['status']}", file=sys.stderr)
        logs = result.get("logs") or ""
        if logs:
            print(logs, end="" if str(logs).endswith("\n") else "\n")
    raise typer.Exit(0 if result["status"] == RunStatus.SUCCEEDED.value else 1)


@app.command("submit")
def cmd_submit(
    cmd: Annotated[
        str | None,
        typer.Option("--cmd", help="Command to run on the cluster, as one string."),
    ] = None,
    argv: Annotated[
        list[str] | None,
        typer.Argument(help="Command words when --cmd is omitted."),
    ] = None,
    cpus: Annotated[float, typer.Option("--cpus")] = 1.0,
    memory: Annotated[
        str | None,
        typer.Option(
            "--memory",
            "-m",
            help="RAM for the driver (for example 8GiB). Omit unless that RAM is free.",
        ),
    ] = None,
    gpus: Annotated[float, typer.Option("--gpus")] = 0.0,
    address: Annotated[str | None, typer.Option("--address")] = None,
    working_directory: Annotated[Path | None, typer.Option("--cwd")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    env: Annotated[list[str] | None, typer.Option("--env")] = None,
    inputs: Annotated[
        list[str] | None,
        typer.Option("--input", help="URI this job reads (repeat). Stored on the Ray job."),
    ] = None,
    outputs: Annotated[
        list[str] | None,
        typer.Option("--output", help="URI this job writes (repeat). Stored on the Ray job."),
    ] = None,
    wait: Annotated[bool, typer.Option("--wait", help="Wait until the job finishes.")] = False,
    timeout: Annotated[float | None, typer.Option("--timeout")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Start a command on the Ray cluster without requiring a .py file.

    Same cluster as `run`. Use this for `python -m package` and other commands.
    Does not wait unless you pass --wait.

    Examples:
      astroai jobs submit --cmd 'python -m mosaic.stack --in /arc/projects/g/in'
      astroai jobs submit --cmd 'python train.py' --wait --cpus 2
    """
    if cmd:
        command = tuple(shlex.split(cmd))
    elif argv:
        command = tuple(argv)
    else:
        raise typer.BadParameter("pass --cmd '…' or the command words", param_hint="--cmd")
    try:
        result = job_submit_payload(
            command,
            address=address,
            cpus=cpus,
            memory=memory,
            gpus=gpus,
            env=env,
            timeout=timeout,
            working_directory=str(working_directory) if working_directory else None,
            run_id=run_id,
            inputs=inputs,
            outputs=outputs,
            wait=wait,
        )
    except (ValueError, RuntimeError, ConnectionError, OSError) as exc:
        _cli_fail(exc)
    if as_json:
        _print_json(result)
        if wait:
            raise typer.Exit(0 if result.get("status") == RunStatus.SUCCEEDED.value else 1)
        return
    if wait:
        print(result["run_id"])
        print(f"status: {result['status']}", file=sys.stderr)
        logs = result.get("logs") or ""
        if logs:
            print(logs, end="" if str(logs).endswith("\n") else "\n")
        raise typer.Exit(0 if result["status"] == RunStatus.SUCCEEDED.value else 1)
    print(result["run_id"])


@app.command("status")
def cmd_status(
    run_id: Annotated[str, typer.Argument(help="Job id from run/submit.")],
    address: Annotated[str | None, typer.Option("--address")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show whether one job is still running, succeeded, or failed."""
    try:
        result = job_status_payload(run_id, address)
    except (RuntimeError, ConnectionError, OSError) as exc:
        _cli_fail(exc)
    if as_json:
        _print_json(result)
    else:
        print(result["status"])


@app.command("logs")
def cmd_logs(
    run_id: Annotated[str, typer.Argument(help="Job id from run/submit.")],
    address: Annotated[str | None, typer.Option("--address")] = None,
) -> None:
    """Print the driver log for one job."""
    try:
        result = job_logs_payload(run_id, address)
    except (RuntimeError, ConnectionError, OSError) as exc:
        _cli_fail(exc)
    logs = result["logs"]
    print(logs, end="" if str(logs).endswith("\n") else "\n")


@app.command("wait")
def cmd_wait(
    run_id: Annotated[str, typer.Argument(help="Job id from run/submit.")],
    address: Annotated[str | None, typer.Option("--address")] = None,
    timeout: Annotated[float | None, typer.Option("--timeout")] = None,
    poll_interval: Annotated[float, typer.Option("--poll-interval")] = 2.0,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Wait until a job finishes, then print its log."""
    try:
        result = job_wait_payload(
            run_id, address=address, timeout=timeout, poll_interval=poll_interval
        )
    except (RuntimeError, ConnectionError, OSError) as exc:
        _cli_fail(exc)
    if as_json:
        _print_json(result)
    else:
        print(f"status: {result['status']}", file=sys.stderr)
        logs = result.get("logs") or ""
        if logs:
            print(logs, end="" if str(logs).endswith("\n") else "\n")
    raise typer.Exit(0 if result["status"] == RunStatus.SUCCEEDED.value else 1)


@app.command("cancel")
def cmd_cancel(
    run_id: Annotated[str, typer.Argument(help="Job id from run/submit.")],
    address: Annotated[str | None, typer.Option("--address")] = None,
) -> None:
    """Ask the cluster to stop a job."""
    try:
        result = job_cancel_payload(run_id, address)
    except (RuntimeError, ConnectionError, OSError) as exc:
        _cli_fail(exc)
    print(f"cancel requested: {result['run_id']}")


@app.command("list")
def cmd_list(
    address: Annotated[str | None, typer.Option("--address")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List jobs on the current cluster."""
    try:
        result = job_list_payload(address)
    except (RuntimeError, ConnectionError, OSError) as exc:
        _cli_fail(exc)
    jobs = result["jobs"]
    if as_json:
        _print_json(jobs)
        return
    if not jobs:
        print("No jobs.")
        return
    print(f"{'RUN ID':<24} {'STATUS':<12} COMMAND")
    for job in jobs:
        rid = str(job.get("submission_id") or job.get("job_id") or "-")
        status = str(job.get("status") or "-")
        entry = str(job.get("entrypoint") or "")
        print(f"{rid:<24} {status:<12} {entry}")


# =====================================================================
# Cluster lifecycle commands (manager /api/v1)
# =====================================================================


def _manager_image() -> str:
    explicit = os.environ.get("RAY_MANAGER_IMAGE", "").strip()
    if explicit:
        return explicit
    tag = os.environ.get("RAY_IMAGE_TAG", os.environ.get("BUILD_TAG", "latest"))
    registry = os.environ.get("REGISTRY", "images.canfar.net")
    owner = os.environ.get("OWNER", "astroai")
    return f"{registry}/{owner}/ray-manager:{tag}"


def cluster_start_payload(
    *,
    address: str | None = None,
    min_workers: int = 0,
    max_workers: int = 8,
    cores: int = 1,
    ram: int = 4,
    gpus: int = 0,
    timeout: int = 1800,
) -> dict[str, Any]:
    """Start (or reuse) an autoscaling Ray cluster.

    Single source of truth shared by ``astroai cluster start`` and the
    MCP ``cluster_start`` tool. Writes ``~/.config/canfar/lab/ray-manager.env``
    so the manager head autoscales, creates the ray-manager session when none
    is running, waits for /readyz, and returns the Jobs address + Dashboard
    URL as a JSON-safe dict. Ray starts ``ray-as-*`` workers when a job needs
    CPUs and idles them out later.

    Raises RuntimeError with a human-readable message when no manager can be
    resolved or the manager is not ready.
    """
    with _cluster_control_lock():
        return _cluster_start_locked(
            address=address,
            min_workers=min_workers,
            max_workers=max_workers,
            cores=cores,
            ram=ram,
            gpus=gpus,
            timeout=timeout,
        )


def _cluster_start_locked(
    *,
    address: str | None,
    min_workers: int,
    max_workers: int,
    cores: int,
    ram: int,
    gpus: int,
    timeout: int,
) -> dict[str, Any]:
    """Cluster start body — caller holds the control lock."""
    from .autoscaler import write_manager_autoscaling_env
    from .dashboard import persist_connect_url, resolve_dashboard_url

    write_manager_autoscaling_env(
        min_workers=min_workers,
        max_workers=max_workers,
        cores=cores,
        ram_gb=ram,
        gpus=gpus,
    )

    existing_manager = False
    if not address:
        try:
            from .canfar_ops import CanfarOps
        except ImportError as exc:
            raise RuntimeError("The canfar client is required to create a manager.") from exc

        ops = CanfarOps()
        existing_manager = ops.find_manager() is not None
        if not existing_manager:
            ops.create_contributed(
                name="raymgr",
                image=_manager_image(),
                cores=2,
                ram=8,
            )

    if address:
        base = address.rstrip("/")
    else:
        # Fresh manager is often Pending without a connect URL until the image
        # pull finishes. Poll for the full caller timeout (default 1800s), not
        # a 2-minute cap — Harbor pulls commonly exceed that.
        poll_s = 5
        max_polls = max(1, timeout // poll_s) if timeout else 12
        base = ""
        for _ in range(max_polls):
            jobs = resolve_dashboard_url()
            base = (
                jobs[: -len("/dashboard")] if jobs and jobs.endswith("/dashboard") else jobs or ""
            )
            if base:
                break
            time.sleep(poll_s)
    if not base:
        raise RuntimeError(
            "No ray-manager found. Run `astroai cluster start` "
            "or start one from the AstroAI hub (Start batch compute)."
        )

    client = _manager_client(base)
    if not client.wait_ready(timeout_seconds=min(timeout, 600)):
        raise RuntimeError(f"Manager not ready at {base} (check auth / preflight).")

    manager_name = base.rstrip("/").rsplit("/", 1)[-1]
    persist_connect_url(manager_name or "default", base)

    status = client.status()
    jobs_url = base.rstrip("/") + "/dashboard"
    result: dict[str, Any] = {
        "manager_url": base,
        "jobs_address": jobs_url,
        "dashboard_url": jobs_url,
        "cluster_phase": (status.get("cluster") or {}).get("phase"),
        "joined_workers": status.get("joined_workers", 0),
        "autoscaling": True,
    }
    if existing_manager:
        result["restart_manager"] = True
    return result


@cluster_app.command("start")
def cluster_cmd_start(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Option("--address", help="Manager connect URL or Jobs API URL."),
    ] = None,
    min_workers: Annotated[
        int, typer.Option("--min-workers", help="Workers kept alive even when idle.")
    ] = 0,
    max_workers: Annotated[int, typer.Option("--max-workers", help="Autoscaler ceiling.")] = 8,
    cores: Annotated[int, typer.Option("--cores", help="CPUs per worker.")] = 1,
    ram: Annotated[int, typer.Option("--ram", help="RAM GiB per worker.")] = 4,
    gpus: Annotated[int, typer.Option("--gpus", help="GPUs per worker.")] = 0,
    timeout: Annotated[int, typer.Option("--timeout", help="Wait timeout (seconds).")] = 1800,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Start the autoscaling Ray cluster. Safe to run if one is already up.

    Writes the manager env file, creates the ray-manager session if needed,
    and lets Ray add `ray-as-*` workers on demand.

    Example:
      astroai cluster start --max-workers 8 --cores 2 --ram 8
    """
    del ctx  # accepted for CLI symmetry; no legacy aliases remain
    try:
        result = cluster_start_payload(
            address=address,
            min_workers=min_workers,
            max_workers=max_workers,
            cores=cores,
            ram=ram,
            gpus=gpus,
            timeout=timeout,
        )
    except RuntimeError as exc:
        _cli_fail(exc)
    if as_json:
        _print_json(result)
    else:
        print(f"manager:     {result['manager_url']}")
        print(f"jobs/dash:   {result['jobs_address']}")
        print(f"phase:       {result['cluster_phase']}  joined: {result['joined_workers']}")
        print(f"autoscaling: on ({min_workers}–{max_workers} workers)")
        if result.get("restart_manager"):
            print(
                "this manager was already running — stop it and re-run "
                "`cluster start` if jobs do not scale"
            )
    # Hint for the caller's shell (a CLI cannot export into its parent).
    print(f"export ASTROAI_RAY_JOBS_ADDRESS={result['jobs_address']}")
    raise typer.Exit(0)


def cluster_status_payload(address: str | None = None) -> dict[str, Any]:
    """Cluster status payload (manager /api/v1/status) — CLI + MCP shared."""
    return _cluster_payload_from(address)


@cluster_app.command("status")
def cluster_cmd_status(
    address: Annotated[str | None, typer.Option("--address")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """See if the cluster is up, and get the Ray Dashboard URL."""
    payload = cluster_status_payload(address)
    if as_json:
        _print_json(payload)
        return
    cluster = payload.get("cluster") or {}
    print(f"phase:        {cluster.get('phase', 'Idle')}")
    print(f"ray address:  {payload.get('ray_address')}")
    print(f"ray nodes:    {payload.get('ray_nodes_alive')}")
    print(f"joined:       {payload.get('joined_workers')} / {cluster.get('worker_count')}")
    auth = payload.get("auth") or {}
    print(f"auth:         {'ok' if auth.get('authenticated') else 'missing'}")
    print(f"dashboard:    {payload.get('dashboard_path')}")
    for w in payload.get("workers") or []:
        print(
            f"  worker {w.get('name')}: {w.get('phase')} joined={w.get('ray_joined')} "
            f"ip={w.get('worker_ip') or '-'}"
        )


def cluster_stop_payload(*, address: str | None = None) -> dict[str, Any]:
    """Tear down the whole cluster — workers and manager — CLI + MCP shared.

    Asks the manager to stop the Ray cluster (destroying every worker
    session), then destroys the ray-manager session itself and clears the
    persisted connect URLs so nothing keeps pointing at a dead manager.
    Runs under the control lock so concurrent start/stop from another
    session sharing this home cannot interleave. Returns a JSON-safe dict.
    """
    with _cluster_control_lock():
        return _cluster_stop_locked(address=address)


def _cluster_stop_locked(*, address: str | None) -> dict[str, Any]:
    """Cluster teardown body — caller holds the control lock."""
    import httpx

    from .dashboard import clear_persisted_connect_urls

    stopped_cluster = False
    manager_error: str | None = None
    try:
        _manager_client(address).stop_cluster()
        stopped_cluster = True
    except (RuntimeError, OSError, httpx.HTTPError) as exc:
        # Unreachable/half-up managers must not block tearing down the session.
        manager_error = str(exc)

    destroyed_manager = False
    try:
        from .canfar_ops import CanfarOps
    except ImportError as exc:
        raise RuntimeError("The canfar client is required to tear down the manager.") from exc

    ops = CanfarOps()
    manager = ops.find_manager()
    detail: str | None = None
    if manager and manager.get("id"):
        destroyed_manager = bool(ops.destroy(str(manager["id"])))
        if not destroyed_manager:
            detail = ops.session_failure_detail(str(manager["id"]))
    cleared = clear_persisted_connect_urls()
    return {
        "stopped_cluster": stopped_cluster,
        "manager_found": bool(manager),
        "destroyed_manager": destroyed_manager,
        "cleared_state": cleared,
        "error": manager_error,
        "detail": detail,
    }


@cluster_app.command("stop")
def cluster_cmd_stop(
    address: Annotated[str | None, typer.Option("--address")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Stop the cluster: destroy every worker session and the manager."""
    try:
        payload = cluster_stop_payload(address=address)
    except RuntimeError as exc:
        _cli_fail(exc)
    if as_json:
        _print_json(payload)
        return
    if not payload["manager_found"]:
        print("no ray-manager found — cluster already down")
        raise typer.Exit(0)
    if payload["stopped_cluster"]:
        print("workers stopped")
    elif payload["error"]:
        print(f"worker stop skipped: {payload['error']}", file=sys.stderr)
    if payload["destroyed_manager"]:
        print("manager destroyed")
    else:
        print("could not destroy the manager session", file=sys.stderr)
        if payload.get("detail"):
            print(payload["detail"], file=sys.stderr)
        raise typer.Exit(1)


# =====================================================================
# Autoscaler commands
# =====================================================================


@autoscaler_app.command("write-config")
def autoscaler_cmd_write_config(
    path: Annotated[Path, typer.Option("--path", help="Output YAML path.")],
    cluster_name: Annotated[str, typer.Option("--cluster-name", help="Ray cluster name.")],
    workers: Annotated[int, typer.Option("--workers", help="Initial min_workers.")] = 0,
    max_workers: Annotated[int, typer.Option("--max-workers", help="Autoscaler ceiling.")] = 8,
    cores: Annotated[int, typer.Option("--cores", help="CPUs per worker session.")] = 1,
    ram_gb: Annotated[int, typer.Option("--ram-gb", help="RAM GiB per worker session.")] = 4,
    gpus: Annotated[int, typer.Option("--gpus", help="GPUs per worker session.")] = 0,
    worker_image: Annotated[str | None, typer.Option("--worker-image")] = None,
    ray_version: Annotated[str | None, typer.Option("--ray-version")] = None,
    ray_head_port: Annotated[int, typer.Option("--ray-head-port")] = 6379,
    heartbeat_path: Annotated[str | None, typer.Option("--heartbeat-path")] = None,
    spill_dir: Annotated[str | None, typer.Option("--spill-dir")] = None,
    idle_timeout_minutes: Annotated[
        int | None,
        typer.Option(
            "--idle-timeout-minutes",
            help="Idle workers are terminated after this many minutes (default: env "
            "RAY_AUTOSCALING_IDLE_TIMEOUT_MINUTES or 5).",
        ),
    ] = None,
) -> None:
    """Write YAML so Ray's own autoscaler can start and stop CANFAR workers.

    Feed the file to `ray start --head --autoscaling-config=<path>` on the
    manager head. That is not `cluster start` and not `run`. Most users
    should `cluster start` instead.

    Example:
      astroai autoscaler write-config --path /tmp/autoscaling.yaml \\
          --cluster-name default --max-workers 8 --cores 2 --ram-gb 8
    """
    from .autoscaler import write_autoscaling_config

    out = write_autoscaling_config(
        path=path,
        cluster_name=cluster_name,
        worker_count=workers,
        max_workers=max_workers,
        cores=cores,
        ram_gb=ram_gb,
        gpus=gpus,
        worker_image=worker_image,
        ray_version=ray_version,
        ray_head_port=ray_head_port,
        heartbeat_path=heartbeat_path,
        spill_dir=spill_dir,
        idle_timeout_minutes=idle_timeout_minutes,
    )
    print(out)


# =====================================================================
# Dashboard commands
# =====================================================================


def dashboard_url_payload(address: str | None = None) -> str:
    """Resolve the Ray Dashboard / Jobs URL — CLI + MCP shared.

    Raises RuntimeError when nothing is resolvable.
    """
    from .dashboard import resolve_dashboard_url

    url = resolve_dashboard_url(address)
    if not url:
        raise RuntimeError(
            "No dashboard URL resolvable. Start a ray-manager session and run "
            "`astroai cluster start` first."
        )
    return url


@dashboard_app.command("url")
def dashboard_cmd_url(
    address: Annotated[str | None, typer.Option("--address")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Print the Ray Dashboard URL for the current cluster."""
    try:
        url = dashboard_url_payload(address)
    except RuntimeError as exc:
        _cli_fail(exc)
    if as_json:
        _print_json({"dashboard_url": url})
    else:
        print(url)


@dashboard_app.command("proxy")
def dashboard_cmd_proxy(
    port: Annotated[int, typer.Option("--port", help="Local port to bind.")] = 9000,
    address: Annotated[str | None, typer.Option("--address")] = None,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
) -> None:
    """Local proxy so a notebook or marimo cell can embed the Ray Dashboard.

    Example:
      astroai cluster dashboard proxy --port 9000
      then <iframe src="http://127.0.0.1:9000/">
    """
    from .dashboard import DashboardProxy, resolve_dashboard_url

    url = resolve_dashboard_url(address)
    if not url:
        raise typer.BadParameter("No dashboard URL resolvable (see `astroai cluster dashboard`).")
    if url.endswith("/dashboard"):
        url = url[: -len("/dashboard")] + "/"
    elif not url.endswith("/"):
        url += "/"

    proxy = DashboardProxy(url, host=host, port=port)
    proxy.start()
    print(f"Ray Dashboard proxy: {proxy.url}")
    print(f"upstream:            {url}")
    print("Press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        proxy.stop()


@mcp_app.command("serve")
def mcp_cmd_serve() -> None:
    """Serve cluster and job tools over stdio for agents.

    Tools: cluster start/check/scale, dashboard URL, and job
    run/submit/list/status/logs/cancel. Same functions as the CLI.

        astroai mcp serve
    """
    from .mcp import serve_stdio

    raise typer.Exit(serve_stdio())


@dashboard_app.command("iframe")
def dashboard_cmd_iframe(
    address: Annotated[str | None, typer.Option("--address")] = None,
    height: Annotated[int, typer.Option("--height")] = 900,
) -> None:
    """Print an HTML iframe that embeds the Ray Dashboard (for a notebook cell)."""
    from .dashboard import dashboard_iframe_html, resolve_dashboard_url

    url = resolve_dashboard_url(address)
    if not url:
        raise typer.BadParameter("No dashboard URL resolvable (see `astroai cluster dashboard`).")
    print(dashboard_iframe_html(url, height=height))


@dashboard_app.callback(invoke_without_command=True)
def dashboard_default(ctx: typer.Context) -> None:
    """Print the Ray Dashboard URL when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    dashboard_cmd_url(address=None, as_json=False)


def register(parent: typer.Typer, *, jobs_as: str = "jobs") -> None:
    """Mount cluster/job commands on the ``astroai`` CLI.

    Job verbs that would collide with session ``status`` go under ``jobs_as``.
    ``run`` stays a top-level command.
    """
    parent.add_typer(cluster_app, name="cluster")
    parent.add_typer(autoscaler_app, name="autoscaler", hidden=True)
    parent.add_typer(mcp_app, name="mcp", hidden=True)
    parent.command(
        "run",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )(cmd_run)
    jobs = typer.Typer(
        name=jobs_as,
        help="List, watch, and stop Ray jobs.",
        no_args_is_help=True,
        add_completion=False,
    )
    jobs.command("submit")(cmd_submit)
    jobs.command("status")(cmd_status)
    jobs.command("logs")(cmd_logs)
    jobs.command("wait")(cmd_wait)
    jobs.command("cancel")(cmd_cancel)
    jobs.command("list")(cmd_list)
    parent.add_typer(jobs, name=jobs_as)


if __name__ == "__main__":
    app()
