from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from canfar_lab.core.arc_permissions import (
    AclGroupEntry,
    GmsGroups,
    list_gms_groups,
    project_access,
    project_gms_member,
    read_acl_groups,
)
from canfar_lab.core.disk_usage import ceph_dir_rbytes, disk_usage, naturalsize
from canfar_lab.core.session_common import find_arc_project_root
from canfar_lab.errors import LabError
from canfar_lab.utils.timing import PhaseTimer, call_with_timeout

if TYPE_CHECKING:
    from canfar_lab.core.vospace_status import VaultNodeStatus, VaultStatus

# Ceph home trees are huge; never walk them in-process. du still can stall.
HOME_DIR_TIMEOUT_SEC = 2.0
DIR_SIZE_TIMEOUT_SEC = 8.0
VAULT_TIMEOUT_SEC = 8.0
LIST_ARC_TIMEOUT_SEC = 5.0
PROJECT_PROBE_WORKERS = 8


@dataclass
class QuotaLine:
    label: str
    path: str
    used: str
    total: str
    free: str
    pct: int
    current: bool = False
    source: str = ""


def df_line(path: Path, label: str, *, current: bool = False) -> QuotaLine | None:
    if not path.is_dir():
        return None
    info = disk_usage(path)
    if info is None:
        return None
    return QuotaLine(
        label=label,
        path=info.path,
        used=naturalsize(info.used_bytes),
        total=naturalsize(info.total_bytes),
        free=naturalsize(info.free_bytes),
        pct=info.pct,
        current=current,
        source=info.source,
    )


def _du_bytes(path: Path, timeout_sec: float) -> int | None:
    from canfar_lab.utils.subprocess import run_capture

    try:
        out = run_capture(["du", "-sb", str(path)], timeout=timeout_sec)
        return int(out.split()[0])
    except (ValueError, IndexError):
        return None
    except LabError as exc:
        # A timeout must not start a second walk with `du -sk`.
        if "timed out" in str(exc).lower():
            return None
    try:
        out = run_capture(["du", "-sk", str(path)], timeout=timeout_sec)
        return int(out.split()[0]) * 1024
    except (LabError, ValueError, IndexError):
        return None


def dir_bytes(path: Path, *, timeout_sec: float = DIR_SIZE_TIMEOUT_SEC) -> int | None:
    """Bytes under path without an in-process recursive walk.

    Prefers ``ceph.dir.rbytes`` on this directory (O(1) on CephFS). Else ``du``
    with a timeout. Returns None when the size cannot be determined in time.
    Missing paths are 0.
    """
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    used = ceph_dir_rbytes(path)
    if used is not None:
        return max(0, used)
    return _du_bytes(path, timeout_sec)


def dir_size(path: Path, *, timeout_sec: float = DIR_SIZE_TIMEOUT_SEC) -> int:
    n = dir_bytes(path, timeout_sec=timeout_sec)
    return 0 if n is None else n


def home_breakdown(home: Path) -> list[tuple[str, str, str]]:
    entries = [
        (".cache", "ML/tool caches"),
        (".canfar", "CANFAR client config + lab state"),
        (".astroai", "legacy AstroAI lab dir (moved to ~/.canfar/lab)"),
        (".pixi", "pixi global envs"),
        (".local", "user tools and data"),
        (".config", "application config"),
    ]
    present = [
        (dirname, label, home / dirname) for dirname, label in entries if (home / dirname).exists()
    ]
    if not present:
        return []

    def _one(item: tuple[str, str, Path]) -> tuple[str, str, str]:
        dirname, label, path = item
        n = dir_bytes(path, timeout_sec=HOME_DIR_TIMEOUT_SEC)
        if n is None:
            return (dirname, "—", f"{label} (timed out)")
        return (dirname, naturalsize(n), label)

    workers = min(PROJECT_PROBE_WORKERS, len(present))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lab-home") as pool:
        return list(pool.map(_one, present))


def top_cpu_processes(limit: int = 5) -> list[str]:
    try:
        from canfar_lab.utils.subprocess import run_capture

        out = run_capture(["ps", "aux", "--sort=-%cpu"], timeout=2.0)
        lines = out.splitlines()
        return lines[1 : limit + 1] if len(lines) > 1 else []
    except LabError:
        return []


def list_arc_projects() -> list[Path]:
    root = Path("/arc/projects")
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and os.access(p, os.R_OK))


@dataclass
class ArcProjectInfo:
    name: str
    path: Path
    quota: QuotaLine | None
    is_cwd: bool
    access: str = "ro"
    acl_groups: list[AclGroupEntry] | None = None
    gms_member: bool | None = None
    vault: VaultNodeStatus | None = None

    def __post_init__(self) -> None:
        if self.acl_groups is None:
            self.acl_groups = []


def arc_project_dict(info: ArcProjectInfo) -> dict:
    vault = info.vault
    vault_payload = None
    if vault is not None:
        from canfar_lab.core.vospace_status import vault_node_dict

        vault_payload = vault_node_dict(vault)
    return {
        "name": info.name,
        "path": str(info.path),
        "is_cwd": info.is_cwd,
        "access": info.access,
        "acl_groups": [{"name": g.name, "perms": g.perms} for g in (info.acl_groups or [])],
        "gms_member": info.gms_member,
        "quota": info.quota.__dict__ if info.quota else None,
        "vault": vault_payload,
    }


def _project_info(proj: Path, cwd_root: Path | None) -> ArcProjectInfo:
    acl_groups = read_acl_groups(proj)
    return ArcProjectInfo(
        name=proj.name,
        path=proj,
        quota=df_line(proj, proj.name, current=(cwd_root == proj)),
        is_cwd=cwd_root == proj,
        access=project_access(proj),
        acl_groups=acl_groups,
    )


def apply_remote_project_info(
    rows: list[ArcProjectInfo],
    gms: GmsGroups | None,
    vault: VaultStatus | None,
) -> None:
    from canfar_lab.core.vospace_status import vault_by_name

    by_name = vault_by_name(vault)
    for row in rows:
        row.gms_member = project_gms_member(row.name, row.acl_groups or [], gms)
        row.vault = by_name.get(row.name.casefold())


def cwd_arc_project(start: Path | None = None) -> ArcProjectInfo | None:
    """The /arc/projects tree that contains cwd, without listing every team dir."""
    cwd_root = find_arc_project_root(start)
    if cwd_root is None:
        return None
    info = _project_info(cwd_root, cwd_root)
    info.gms_member = None
    return info


def arc_project_statuses(
    start: Path | None = None,
    *,
    gms: GmsGroups | None | bool = True,
    vault: bool = True,
    timer: PhaseTimer | None = None,
) -> tuple[
    ArcProjectInfo | None,
    list[ArcProjectInfo],
    GmsGroups | None,
    VaultStatus | None,
]:
    """Team projects under /arc/projects with access, ACL groups, GMS, and vault."""
    clock = timer or PhaseTimer()
    cwd_root = find_arc_project_root(start)
    with clock.phase("team projects"):
        projects = call_with_timeout(list_arc_projects, LIST_ARC_TIMEOUT_SEC, [])
        if projects:
            workers = min(PROJECT_PROBE_WORKERS, len(projects))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lab-arc") as pool:
                rows = list(pool.map(lambda p: _project_info(p, cwd_root), projects))
        else:
            rows = []
        rows.sort(key=lambda row: (not row.is_cwd, row.name.lower()))
        active = next((row for row in rows if row.is_cwd), None)

    gms_info: GmsGroups | None
    if gms is True:
        with clock.phase("gms"):
            gms_info = list_gms_groups()
    elif gms is False:
        gms_info = None
    else:
        gms_info = gms

    vault_info = None
    if vault:
        from canfar_lab.core.vospace_status import vault_statuses

        with clock.phase("vault"):
            vault_info = call_with_timeout(
                lambda: vault_statuses(
                    arc_names=[row.name for row in rows],
                    gms=gms_info,
                ),
                VAULT_TIMEOUT_SEC,
                None,
            )
    apply_remote_project_info(rows, gms_info, vault_info)
    return active, rows, gms_info, vault_info


def collect_status_quotas(
    *,
    home: Path,
    scratch: Path | None,
    projects: list[ArcProjectInfo] | None = None,
) -> list[QuotaLine]:
    quotas: list[QuotaLine] = []
    if q := df_line(home, "home"):
        quotas.append(q)
    if projects is None:
        _, projects, _, _ = arc_project_statuses(gms=False, vault=False)
    for proj in projects:
        if proj.quota is not None:
            quotas.append(proj.quota)
        elif proj.is_cwd:
            quotas.append(
                QuotaLine(
                    label=proj.name,
                    path=str(proj.path),
                    used="?",
                    total="?",
                    free="?",
                    pct=0,
                    current=True,
                    source="unknown",
                )
            )
    if scratch is not None and (q := df_line(scratch, "scratch")):
        quotas.append(q)
    return quotas
