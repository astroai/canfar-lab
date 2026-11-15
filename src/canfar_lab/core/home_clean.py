"""Free space on home: package caches, optional saved environments and preferences."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from canfar_lab.core.disk_usage import naturalsize
from canfar_lab.core.project import save_rows
from canfar_lab.core.storage import dir_bytes

# Caches that leak onto home outside ~/.cache.
EXTRA_REL = (
    ".local/share/uv",
    ".local/share/pip",
    ".local/share/Trash",
    ".npm/_cacache",
    ".pixi/cache",
)


def _under_home(path: Path, home: Path) -> bool:
    try:
        path.resolve().relative_to(home.resolve())
        return True
    except (OSError, ValueError):
        return False


def cache_targets(home: Path) -> list[Path]:
    """Home-side caches as they exist now. Does not walk into each tree.

    Lists top-level entries of ``~/.cache`` (and ``XDG_CACHE_HOME`` when that
    path is still on home). Scratch-backed XDG caches are skipped.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        if not path.exists():
            return
        if not _under_home(path, home):
            return
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    cache_home = home / ".cache"
    if cache_home.is_dir():
        try:
            children = sorted(cache_home.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            children = []
        for child in children:
            add(child)
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        xdg_path = Path(xdg)
        try:
            same = cache_home.exists() and xdg_path.resolve() == cache_home.resolve()
        except OSError:
            same = False
        if not same:
            add(xdg_path)
    for rel in EXTRA_REL:
        add(home / rel)
    return found


def _row(path: Path, kind: str) -> dict[str, Any]:
    n = dir_bytes(path)
    size = 0 if n is None else n
    return {
        "kind": kind,
        "path": str(path),
        "bytes": size,
        "size": "—" if n is None else naturalsize(size),
    }


def plan_clean(home: Path, save_dir: Path) -> dict[str, Any]:
    """What `canfar lab clean` can remove. Does not delete anything."""
    caches = [_row(path, "cache") for path in cache_targets(home)]
    saves = []
    for row in save_rows(save_dir):
        path = Path(row["path"])
        item = _row(path, "save")
        item["name"] = row["name"]
        item["saved_at"] = row["saved_at"]
        item["env_kind"] = row["kind"]
        saves.append(item)
    cfg = home / ".canfar" / "lab" / "config.yaml"
    config = _row(cfg, "config") if cfg.exists() else None
    return {
        "caches": caches,
        "saves": saves,
        "config": config,
        "cache_bytes": sum(r["bytes"] for r in caches),
        "save_bytes": sum(r["bytes"] for r in saves),
    }


def _rm(path: Path) -> dict[str, str]:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        return {"path": str(path), "status": "removed"}
    except OSError as exc:
        return {"path": str(path), "status": "error", "detail": str(exc)}


def apply_clean(
    plan: dict[str, Any],
    *,
    caches: bool,
    saves: bool,
    config: bool,
    dry_run: bool,
) -> list[dict[str, str]]:
    """Delete selected plan rows. ``dry_run`` reports ``would_remove`` only."""
    actions: list[dict[str, str]] = []
    selected: list[dict[str, Any]] = []
    if caches:
        selected.extend(plan["caches"])
    if saves:
        selected.extend(plan["saves"])
    if config and plan["config"] is not None:
        selected.append(plan["config"])
    for row in selected:
        path = Path(row["path"])
        if dry_run:
            actions.append({"path": str(path), "status": "would_remove"})
            continue
        actions.append(_rm(path))
    return actions
