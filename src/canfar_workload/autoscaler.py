"""Ray-native autoscaler backed by CANFAR headless sessions.

Implements :class:`ray.autoscaler.node_provider.NodeProvider` (public path in
Ray 2.56+; the old ``_private.node_provider`` module no longer exists) so
Ray's own autoscaler (started on the head with ``--autoscaling-config``) can
launch and terminate Skaha ``ray-worker`` sessions on demand. Node IDs are
CANFAR session IDs.

The provider is used from inside the ray-manager head pod, so it requires
the canfar client. It does **not** need Ray's
autoscaler internals beyond the NodeProvider base class; all session work goes
through :class:`canfar_workload.canfar_ops.CanfarOps`.

Provider config (``provider.config`` in the autoscaling YAML)::

    worker_image: images.canfar.net/astroai/ray-worker:<tag>
    cores: 1
    ram_gb: 4
    gpus: 0
    max_workers: 8
    pending_timeout_minutes: 15
    heartbeat_path: /arc/home/<user>/.astroai/ray/clusters/<id>/manager-heartbeat
    cluster_id: default
    node_manager_port, object_manager_port, ...  (optional Ray port pins)

Ray polls :meth:`internal_ip` until the worker is Running and its pod IP is
known (parsed from the session log's ``Worker <ip> joining`` line), so
asynchronous Skaha startup is handled natively.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from canfar_workload.canfar_ops import CanfarOps
from canfar_workload.ray_cluster import parse_worker_ip_from_logs
from canfar_workload.settings import manager_pod_ip

logger = logging.getLogger(__name__)

# Ray 2.56 moved the provider base to the public `ray.autoscaler.node_provider`
# module (the old `_private.node_provider` path no longer exists). Keep the
# module importable without ray (py3.13 images) but never swallow the failure
# silently — record it so the RuntimeError below names the real cause. Catch
# only ImportError (which covers ModuleNotFoundError for a missing ray): a
# genuine bug elsewhere in ray's import chain must fail loudly, not silently
# degrade the provider to an object subclass.
_RAY_NODE_PROVIDER_IMPORT_ERROR: ImportError | None = None
try:  # pragma: no cover - exercised in images where ray is installed
    from ray.autoscaler.node_provider import NodeProvider as _RayNodeProvider
except ImportError as exc:
    _RayNodeProvider = object  # type: ignore[assignment,misc]
    _RAY_NODE_PROVIDER_IMPORT_ERROR = exc
    logger.warning("Ray NodeProvider import failed; autoscaler disabled: %r", exc)

_TERMINAL_SESSION_STATUSES = {"Failed", "Error", "Succeeded", "Completed", "Terminating"}
_RUNNING_SESSION_STATUSES = {"Running"}
_PENDING_SESSION_STATUSES = {"Pending"}
_DEFAULT_PENDING_TIMEOUT_MINUTES = 15
_DESTROY_RETRIES = 3
_DESTROY_RETRY_SLEEP_S = 0.5

# Ray autoscaler tag names (ray.autoscaler.tags). Workers start Ray themselves
# (start-worker.sh); the head is this manager process, not a Skaha session.
_HEAD_NODE_ID = "ray-head"
TAG_RAY_NODE_KIND = "ray-node-type"
TAG_RAY_USER_NODE_TYPE = "ray-user-node-type"
TAG_RAY_NODE_STATUS = "ray-node-status"
TAG_RAY_NODE_NAME = "ray-node-name"
NODE_KIND_HEAD = "head"
NODE_KIND_WORKER = "worker"
STATUS_UP_TO_DATE = "up-to-date"
_HEAD_TAGS = {
    TAG_RAY_NODE_KIND: NODE_KIND_HEAD,
    TAG_RAY_USER_NODE_TYPE: "ray.head.default",
    TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
    TAG_RAY_NODE_NAME: _HEAD_NODE_ID,
}
_WORKER_TAG_DEFAULTS = {
    TAG_RAY_NODE_KIND: NODE_KIND_WORKER,
    TAG_RAY_USER_NODE_TYPE: "ray.worker.default",
    TAG_RAY_NODE_STATUS: STATUS_UP_TO_DATE,
}


def _autoscaler_worker_prefix(cluster_name: str) -> str:
    """Name prefix for autoscaler workers of *cluster_name*.

    Names are ``ray-as-{cluster}-{ms}`` capped at 60 chars. Reserve 13 digits
    for the millisecond token so age parsing and list/destroy prefixes stay
    aligned when the cluster id is long.
    """
    room = 60 - len("ray-as-") - 1 - 13
    return f"ray-as-{cluster_name[: max(1, room)]}"


def _autoscaler_worker_name(cluster_name: str, *, ms: int | None = None) -> str:
    ms_s = str(int(time.time() * 1000) if ms is None else ms)
    room = 60 - len("ray-as-") - 1 - len(ms_s)
    return f"ray-as-{cluster_name[: max(1, room)]}-{ms_s}"


def _default_worker_image() -> str:
    tag = os.environ.get("RAY_IMAGE_TAG", os.environ.get("BUILD_TAG", "latest"))
    registry = os.environ.get("REGISTRY", "images.canfar.net")
    owner = os.environ.get("OWNER", "astroai")
    return f"{registry}/{owner}/ray-worker:{tag}"


class CanfarNodeProvider(_RayNodeProvider):  # type: ignore[misc,valid-type]
    """Ray autoscaler node provider that creates CANFAR headless sessions."""

    def __init__(self, provider_config: dict[str, Any], cluster_name: str) -> None:
        if _RayNodeProvider is object:  # pragma: no cover - guard for py3.13-only envs
            raise RuntimeError(
                "Ray autoscaler support requires Ray installed. "
                f"NodeProvider import failed: {_RAY_NODE_PROVIDER_IMPORT_ERROR!r}"
            )
        super().__init__(provider_config, cluster_name)
        self.provider_config = provider_config or {}
        # Ray constructs external providers with the whole provider dict
        # (type/module/config). Merge the nested ``config`` so the accessors
        # below (which read top-level keys) see the written options either way.
        nested = self.provider_config.get("config")
        if isinstance(nested, dict):
            self.provider_config = {**self.provider_config, **nested}
        self.cluster_name = cluster_name
        self._ops = CanfarOps()
        self._tags: dict[str, dict[str, str]] = {}
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    # -- config helpers -----------------------------------------------------

    def _worker_image(self) -> str:
        return str(self.provider_config.get("worker_image") or _default_worker_image())

    def _max_workers(self) -> int:
        raw = self.provider_config.get("max_workers")
        if raw is None:
            raw = os.environ.get("RAY_AUTOSCALING_MAX_WORKERS", "8")
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 8

    def _pending_timeout_minutes(self) -> int:
        raw = self.provider_config.get("pending_timeout_minutes")
        if raw is None:
            raw = os.environ.get(
                "RAY_AUTOSCALING_PENDING_TIMEOUT_MINUTES",
                str(_DEFAULT_PENDING_TIMEOUT_MINUTES),
            )
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return _DEFAULT_PENDING_TIMEOUT_MINUTES

    def _worker_spec(self) -> dict[str, Any]:
        return {
            "cores": int(self.provider_config.get("cores", 1)),
            "ram_gb": int(self.provider_config.get("ram_gb", 4)),
            "gpus": int(self.provider_config.get("gpus", 0)),
        }

    def _list_autoscaler_sessions(self) -> list[dict[str, Any]]:
        """Non-terminal listing; raises on CANFAR list failure (fail closed)."""
        return list(
            self._ops.list_headless_sessions(
                name_prefix=_autoscaler_worker_prefix(self.cluster_name)
            )
        )

    def _count_live_workers(self, rows: list[dict[str, Any]] | None = None) -> int:
        if rows is None:
            rows = self._list_autoscaler_sessions()
        n = 0
        for row in rows:
            status = str(row.get("status") or "Unknown")
            if status in _TERMINAL_SESSION_STATUSES:
                continue
            if str(row.get("id") or "").strip():
                n += 1
        with self._lock:
            n += len(self._inflight)
        return n

    def _heartbeat_path(self) -> str:
        return str(
            self.provider_config.get("heartbeat_path")
            or os.environ.get("RAY_MANAGER_HEARTBEAT_PATH", "")
        )

    def _worker_env(self) -> dict[str, str]:
        spec = self._worker_spec()
        env: dict[str, str] = {
            "RAY_CLUSTER_ID": self.cluster_name,
            "RAY_HEAD_IP": manager_pod_ip(),
            "RAY_HEAD_PORT": str(
                self.provider_config.get("ray_head_port") or os.environ.get("RAY_HEAD_PORT", "6379")
            ),
            "RAY_VERSION_EXPECTED": str(
                self.provider_config.get("ray_version")
                or os.environ.get("RAY_VERSION_EXPECTED", "")
            ),
            "RAY_WORKER_CPUS": str(spec["cores"]),
            "RAY_WORKER_GPUS": str(spec["gpus"]),
            "RAY_SPILL_DIR": str(
                self.provider_config.get("spill_dir")
                or f"{os.environ.get('SCRATCH', '/scratch')}/ray/{self.cluster_name}"
            ),
        }
        hb = self._heartbeat_path()
        if hb:
            env["RAY_MANAGER_HEARTBEAT_PATH"] = hb
            env["RAY_MANAGER_HEARTBEAT_TIMEOUT_SECONDS"] = str(
                self.provider_config.get("heartbeat_timeout_seconds")
                or os.environ.get("RAY_MANAGER_HEARTBEAT_TIMEOUT_SECONDS", "120")
            )
        for key in (
            "RAY_NODE_MANAGER_PORT",
            "RAY_OBJECT_MANAGER_PORT",
            "RAY_RUNTIME_ENV_AGENT_PORT",
            "RAY_DASHBOARD_AGENT_GRPC_PORT",
            "RAY_MIN_WORKER_PORT",
            "RAY_MAX_WORKER_PORT",
        ):
            val = self.provider_config.get(key) or os.environ.get(key)
            if val:
                env[key] = str(val)
        return env

    # -- NodeProvider contract ---------------------------------------------

    def create_node(
        self, node_config: dict[str, Any], tags: dict[str, str], count: int
    ) -> dict[str, str]:
        """Launch *count* headless ray-worker sessions; return {node_id: ip}.

        Hard-caps against ``max_workers`` using a live Skaha listing (Pending
        counts) plus an in-process inflight set so overlapping creates cannot
        overshoot. List failures fail closed (no create).
        """
        kind = (tags or {}).get(TAG_RAY_NODE_KIND)
        if kind == NODE_KIND_HEAD or count <= 0:
            # Head is this manager. Ray may still ask; do not spawn a worker.
            return {_HEAD_NODE_ID: manager_pod_ip()} if kind == NODE_KIND_HEAD else {}

        self._reap_stale_pending()

        try:
            rows = self._list_autoscaler_sessions()
        except Exception as exc:  # noqa: BLE001 — fail closed on catalog errors
            logger.error("Refusing create_node: list_headless_sessions failed (%s)", exc)
            raise RuntimeError(
                f"Cannot list autoscaler sessions; refusing create (fail closed): {exc}"
            ) from exc

        max_w = self._max_workers()
        current = self._count_live_workers(rows)
        allowed = max(0, max_w - current)
        if allowed <= 0:
            logger.warning(
                "create_node refused: already at max_workers=%s (live+inflight=%s)",
                max_w,
                current,
            )
            return {}
        if count > allowed:
            logger.warning(
                "create_node clamping count %s -> %s (max_workers=%s live+inflight=%s)",
                count,
                allowed,
                max_w,
                current,
            )
            count = allowed

        # Reserve inflight slots before the slow Skaha create so concurrent
        # create_node calls see the reservation in _count_live_workers.
        placeholders = [f"inflight-{time.time_ns()}-{i}" for i in range(count)]
        with self._lock:
            self._inflight.update(placeholders)

        spec = self._worker_spec()
        # Distinct ``ray-as-`` prefix (autoscaler-managed) so the manager's
        # orphan GC (which owns ``ray-w-``/``ray-retry-``/``ray-preflight-``)
        # never destroys autoscaler nodes, and Ray's autoscaler never adopts
        # manager-created workers (non_terminated_nodes matches this prefix).
        name = _autoscaler_worker_name(self.cluster_name)
        try:
            launches = self._ops.create_headless(
                name=name,
                image=self._worker_image(),
                cores=spec["cores"],
                ram=spec["ram_gb"],
                gpu=spec["gpus"] or None,
                env=self._worker_env(),
                replicas=count,
            )
        except Exception:
            with self._lock:
                self._inflight.difference_update(placeholders)
            raise

        result: dict[str, str] = {}
        stored = {**_WORKER_TAG_DEFAULTS, **(tags or {})}
        with self._lock:
            self._inflight.difference_update(placeholders)
            for launch in launches:
                self._tags[launch.session_id] = dict(stored)
                # Ray resolves IPs lazily via internal_ip(); session may still be Pending.
                result[launch.session_id] = ""
        return result

    def terminate_node(self, node_id: str) -> None:
        if node_id == _HEAD_NODE_ID:
            return
        with self._lock:
            self._tags.pop(node_id, None)
        _destroy_session_with_retries(self._ops, node_id)

    def terminate_nodes(self, node_ids: list[str]) -> None:
        for node_id in node_ids:
            self.terminate_node(node_id)

    def non_terminated_nodes(self, tag_filters: dict[str, str]) -> list[str]:
        self._reap_stale_pending()
        ids = [_HEAD_NODE_ID]
        try:
            rows = self._list_autoscaler_sessions()
        except Exception as exc:  # noqa: BLE001 — return head-only on list failure
            logger.error("non_terminated_nodes list failed (%s); returning head only", exc)
            return (
                [_HEAD_NODE_ID]
                if not tag_filters
                else (
                    [_HEAD_NODE_ID]
                    if _tags_match(self.node_tags(_HEAD_NODE_ID), tag_filters)
                    else []
                )
            )
        for row in rows:
            sid = str(row.get("id") or "")
            if not sid:
                continue
            status = str(row.get("status") or "Unknown")
            if status in _TERMINAL_SESSION_STATUSES:
                continue
            ids.append(sid)
        if not tag_filters:
            return ids
        return [nid for nid in ids if _tags_match(self.node_tags(nid), tag_filters)]

    def _reap_stale_pending(self) -> None:
        """Destroy Pending ray-as-* sessions older than the Pending TTL."""
        timeout_s = self._pending_timeout_minutes() * 60
        now = time.time()
        try:
            rows = self._list_autoscaler_sessions()
        except Exception as exc:  # noqa: BLE001 — reaper must not block create
            logger.warning("pending reaper skipped: list failed (%s)", exc)
            return
        for row in rows:
            status = str(row.get("status") or "Unknown")
            if status not in _PENDING_SESSION_STATUSES:
                continue
            sid = str(row.get("id") or "").strip()
            if not sid:
                continue
            age = _session_age_seconds(row, now=now)
            if age is None or age < timeout_s:
                continue
            logger.warning(
                "Reaping stale Pending autoscaler session %s (age=%.0fs > %ss)",
                sid,
                age,
                timeout_s,
            )
            try:
                _destroy_session_with_retries(self._ops, sid)
            except Exception as exc:  # noqa: BLE001 — continue reaping others
                logger.error("Failed to reap Pending session %s: %s", sid, exc)
            with self._lock:
                self._tags.pop(sid, None)

    def is_running(self, node_id: str) -> bool:
        if node_id == _HEAD_NODE_ID:
            return True
        return self._ops.session_status(node_id) in _RUNNING_SESSION_STATUSES

    def is_terminated(self, node_id: str) -> bool:
        if node_id == _HEAD_NODE_ID:
            return False
        return self._ops.session_status(node_id) in _TERMINAL_SESSION_STATUSES

    def node_tags(self, node_id: str) -> dict[str, str]:
        if node_id == _HEAD_NODE_ID:
            return dict(_HEAD_TAGS)
        with self._lock:
            stored = dict(self._tags.get(node_id) or {})
        # Discovered workers (manager restart) have no in-memory tags; Ray
        # indexes node_tags()[ray-node-type] and KeyErrors on {}.
        return {**_WORKER_TAG_DEFAULTS, **stored}

    def set_node_tags(self, node_id: str, tags: dict[str, str]) -> None:
        if node_id == _HEAD_NODE_ID:
            return
        with self._lock:
            cur = self._tags.setdefault(node_id, {})
            cur.update(tags or {})

    def internal_ip(self, node_id: str) -> str:
        if node_id == _HEAD_NODE_ID:
            return manager_pod_ip()
        info = self._ops.session_info(node_id)
        status = str(info.get("status") or "Unknown")
        if status not in _RUNNING_SESSION_STATUSES:
            return ""
        # Prefer an explicit IP field, else parse the worker log's join line.
        ip = str(info.get("podIP") or info.get("nodeIP") or info.get("ip") or "").strip()
        if ip:
            return ip
        logs = self._ops.session_logs(node_id)
        parsed = parse_worker_ip_from_logs(logs)
        return parsed or ""

    def external_ip(self, node_id: str) -> str:
        return self.internal_ip(node_id)


def write_autoscaling_config(
    *,
    path: str | Path,
    cluster_name: str,
    worker_count: int,
    max_workers: int,
    cores: int = 1,
    ram_gb: int = 4,
    gpus: int = 0,
    worker_image: str | None = None,
    ray_version: str | None = None,
    ray_head_port: int = 6379,
    heartbeat_path: str | None = None,
    spill_dir: str | None = None,
    idle_timeout_minutes: int | None = None,
) -> Path:
    """Write a Ray autoscaling YAML using the CANFAR node provider.

    ``worker_count`` is the initial ``min_workers``; the autoscaler grows to
    ``max_workers`` on demand and shrinks back when idle.

    ``idle_timeout_minutes`` overrides the baked 5-minute idle timeout
    (default: env ``RAY_AUTOSCALING_IDLE_TIMEOUT_MINUTES`` or 5) — tests and
    operators can shrink idle workers faster without rebuilding.
    """
    from canfar_workload.settings import _default_ray_version

    version = ray_version or os.environ.get("RAY_VERSION_EXPECTED", "") or _default_ray_version()
    image = worker_image or _default_worker_image()
    hb = heartbeat_path or os.environ.get("RAY_MANAGER_HEARTBEAT_PATH", "")
    spill = spill_dir or f"{os.environ.get('SCRATCH', '/scratch')}/ray/{cluster_name}"
    idle = (
        idle_timeout_minutes
        if idle_timeout_minutes is not None
        else int(os.environ.get("RAY_AUTOSCALING_IDLE_TIMEOUT_MINUTES", "5"))
    )

    provider_config: dict[str, Any] = {
        "worker_image": image,
        "cores": cores,
        "ram_gb": ram_gb,
        "gpus": gpus,
        "max_workers": int(max_workers),
        "pending_timeout_minutes": int(
            os.environ.get(
                "RAY_AUTOSCALING_PENDING_TIMEOUT_MINUTES",
                str(_DEFAULT_PENDING_TIMEOUT_MINUTES),
            )
        ),
        "ray_head_port": ray_head_port,
        "ray_version": version,
        "spill_dir": spill,
    }
    if hb:
        provider_config["heartbeat_path"] = hb
        provider_config["heartbeat_timeout_seconds"] = int(
            os.environ.get("RAY_MANAGER_HEARTBEAT_TIMEOUT_SECONDS", "120")
        )

    # Ray 2.x autoscaler requires these keys; workers join the cluster natively
    # (start-worker.sh in the worker image), so the command lists stay empty.
    # ``head_node_type``/``auth``/``idle_timeout_minutes`` are mandatory reads
    # in StandardAutoscaler.reset() — omitting them crashes the monitor with
    # KeyError the moment `ray start --head --autoscaling-config` boots.
    # disable_node_updaters: workers have no SSH; Ray setup is start-worker.sh.
    # disable_launch_config_check: tags are not persisted across monitor restarts.
    # foreground_node_launch: Skaha create is slow; launch in the autoscaler thread.
    yaml = f"""\
# Generated by astroai ({__name__}); do not edit by hand.
cluster_name: {cluster_name}
max_workers: {max_workers}
head_node_type: ray.head.default
idle_timeout_minutes: {idle}

auth: {{}}

provider:
  type: external
  module: canfar_workload.autoscaler.CanfarNodeProvider
  disable_node_updaters: true
  disable_launch_config_check: true
  foreground_node_launch: true
  config: {_yaml_inline(provider_config)}

available_node_types:
  ray.head.default:
    resources:
      CPU: 0
      memory: 0
    node_config: {{}}
  ray.worker.default:
    min_workers: {worker_count}
    max_workers: {max_workers}
    resources:
      CPU: {cores}
      memory: {ram_gb * 1024 * 1024 * 1024}
    node_config: {{}}

file_mounts: {{}}
cluster_synced_files: []
env_vars: {{}}
head_setup_commands: []
head_start_ray_commands: []
worker_setup_commands: []
worker_start_ray_commands: []
"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml, encoding="utf-8")
    return path


def manager_autoscaling_env_path() -> Path:
    return Path.home() / ".config" / "canfar" / "lab" / "ray-manager.env"


def write_manager_autoscaling_env(
    *,
    enabled: bool = True,
    min_workers: int = 0,
    max_workers: int = 8,
    cores: int = 1,
    ram_gb: int = 4,
    gpus: int = 0,
    idle_timeout_minutes: int = 5,
) -> Path:
    """Write ``~/.config/canfar/lab/ray-manager.env`` so the next manager autoscales.

    Skaha will not pass ``-e`` into a contributed session. The manager sources
    this file at start. ``enabled=False`` deletes the file.
    """
    path = manager_autoscaling_env_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return path
    from canfar_lab.utils.json_utils import atomic_write_text

    lines = [
        "RAY_AUTOSCALING_ENABLED=1",
        f"RAY_AUTOSCALING_MIN_WORKERS={int(min_workers)}",
        f"RAY_AUTOSCALING_MAX_WORKERS={int(max_workers)}",
        f"RAY_AUTOSCALING_CORES={int(cores)}",
        f"RAY_AUTOSCALING_RAM_GB={int(ram_gb)}",
        f"RAY_AUTOSCALING_GPUS={int(gpus)}",
        f"RAY_AUTOSCALING_IDLE_TIMEOUT_MINUTES={int(idle_timeout_minutes)}",
    ]
    # Manager image ships stock canfar_workload; point PYTHONPATH at a
    # checked-out tree on /arc so create_node hard-cap / idle fixes load.
    # CANFAR_LAB_PYTHONPATH is merged by `canfar lab env export` (profile boot).
    workload_src = (os.environ.get("ASTROAI_WORKLOAD_SRC") or "").strip()
    if workload_src:
        src_path = f"{workload_src.rstrip('/')}/src"
        lines.append(f"PYTHONPATH={src_path}")
        lines.append(f"CANFAR_LAB_PYTHONPATH={src_path}")
        lines.append(f"ASTROAI_WORKLOAD_SRC={workload_src.rstrip('/')}")
    lines.append("")
    atomic_write_text(path, "\n".join(lines))
    return path


def read_manager_autoscaling_env(path: Path | None = None) -> dict[str, int] | None:
    """Parse the manager autoscaling env file into comparable ints.

    Returns ``None`` when the file is missing or unreadable.
    """
    path = path or manager_autoscaling_env_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    raw: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        raw[key.strip()] = val.strip()
    keys = {
        "min_workers": "RAY_AUTOSCALING_MIN_WORKERS",
        "max_workers": "RAY_AUTOSCALING_MAX_WORKERS",
        "cores": "RAY_AUTOSCALING_CORES",
        "ram_gb": "RAY_AUTOSCALING_RAM_GB",
        "gpus": "RAY_AUTOSCALING_GPUS",
        "idle_timeout_minutes": "RAY_AUTOSCALING_IDLE_TIMEOUT_MINUTES",
    }
    out: dict[str, int] = {}
    for dest, env_key in keys.items():
        if env_key not in raw:
            return None
        try:
            out[dest] = int(raw[env_key])
        except ValueError:
            return None
    return out


def destroy_autoscaler_workers(
    ops: CanfarOps | None = None,
    *,
    cluster_name: str | None = None,
) -> list[str]:
    """Destroy ``ray-as-*`` sessions (optionally limited to one cluster name).

    Used by ``cluster stop`` so teardown is not limited to manager-owned
    ``ray-w-*`` workers tracked in the state store. Returns destroyed session IDs.
    """
    ops = ops or CanfarOps()
    prefix = _autoscaler_worker_prefix(cluster_name) if cluster_name else "ray-as-"
    destroyed: list[str] = []
    try:
        rows = ops.list_headless_sessions(name_prefix=prefix)
    except Exception as exc:  # noqa: BLE001
        logger.error("destroy_autoscaler_workers: list failed (%s)", exc)
        return destroyed
    for row in rows:
        status = str(row.get("status") or "Unknown")
        if status in _TERMINAL_SESSION_STATUSES:
            continue
        sid = str(row.get("id") or "").strip()
        if not sid:
            continue
        try:
            _destroy_session_with_retries(ops, sid)
            destroyed.append(sid)
        except Exception as exc:  # noqa: BLE001
            logger.error("destroy_autoscaler_workers: failed for %s (%s)", sid, exc)
    return destroyed


def _destroy_session_with_retries(ops: CanfarOps, session_id: str) -> None:
    """Retry Skaha destroy; raise if the session still looks non-terminal."""
    last_ok = False
    for attempt in range(1, _DESTROY_RETRIES + 1):
        last_ok = bool(ops.destroy(session_id))
        if last_ok:
            return
        if attempt < _DESTROY_RETRIES:
            time.sleep(_DESTROY_RETRY_SLEEP_S)
    status = "Unknown"
    with contextlib.suppress(Exception):
        status = ops.session_status(session_id)
    if status in _TERMINAL_SESSION_STATUSES:
        return
    raise RuntimeError(
        f"Failed to destroy session {session_id} after {_DESTROY_RETRIES} attempts "
        f"(status={status}, destroy_ok={last_ok})"
    )


def _session_age_seconds(row: dict[str, Any], *, now: float | None = None) -> float | None:
    """Best-effort age for a Skaha session row.

    Prefers explicit start/creation fields; falls back to a millisecond
    epoch embedded in ``ray-as-<cluster>-<ms>`` names (including replica
    suffixes like ``…-<ms>-1``).
    """
    import re

    now = time.time() if now is None else now
    for key in (
        "startTime",
        "starttime",
        "creationTime",
        "created",
        "createdAt",
        "startDate",
    ):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        parsed = _parse_time_to_epoch(raw)
        if parsed is not None:
            return max(0.0, now - parsed)
    name = str(row.get("name") or "")
    # Prefer a 12+ digit token (ms since epoch); ignore short replica indices.
    matches = re.findall(r"(?<!\d)(\d{12,})(?!\d)", name)
    if matches:
        ms = int(matches[-1])
        epoch = ms / 1000.0 if ms > 10_000_000_000 else float(ms)
        return max(0.0, now - epoch)
    return None


def _parse_time_to_epoch(raw: Any) -> float | None:
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val / 1000.0 if val > 10_000_000_000 else val
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_time_to_epoch(int(text))
    # ISO-8601 (optional trailing Z).
    try:
        from datetime import datetime

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _yaml_inline(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, sort_keys=True)


def _tags_match(tags: dict[str, str], filters: dict[str, str]) -> bool:
    return all(tags.get(key) == value for key, value in filters.items())
