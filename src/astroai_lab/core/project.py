from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from astroai_lab.core.disk_usage import naturalsize
from astroai_lab.errors import LabError
from astroai_lab.models.manifest import EnvManifest, ProjectKind
from astroai_lab.utils.subprocess import run

KIND_FILES: dict[ProjectKind, tuple[str, str]] = {
    ProjectKind.PIXI: ("pixi.toml", "pixi.lock"),
    ProjectKind.UV: ("pyproject.toml", "uv.lock"),
}
EXTRAS = (".python-version", "README.md")
ENV_DIRS: dict[ProjectKind, str] = {
    ProjectKind.PIXI: ".pixi",
    ProjectKind.UV: ".venv",
}

_MIN_FREE_BYTES = 100 * 1024 * 1024
_MAX_QUOTA_PCT = 98


def detect_project(directory: Path) -> ProjectKind | None:
    if (directory / "pixi.toml").is_file():
        return ProjectKind.PIXI
    if (directory / "pyproject.toml").is_file():
        return ProjectKind.UV
    return None


def require_project(directory: Path) -> ProjectKind:
    kind = detect_project(directory)
    if kind is None:
        raise LabError(
            "No pixi or uv project here (need pixi.toml or pyproject.toml).",
            hint="astroai init mylab",
        )
    return kind


def install_project(directory: Path, *, bootstrap_lock: bool = False, quiet: bool = False) -> None:
    kind = require_project(directory)
    if kind == ProjectKind.PIXI:
        if bootstrap_lock and _run_pixi_install(directory, allow_fail=True):
            return
        if bootstrap_lock:
            (directory / "pixi.lock").unlink(missing_ok=True)
            run(["pixi", "lock"], cwd=directory, quiet=quiet)
        run(["pixi", "install"], cwd=directory, quiet=quiet)
        return
    if bootstrap_lock and _run_uv_sync(directory, allow_fail=True):
        return
    if bootstrap_lock:
        (directory / "uv.lock").unlink(missing_ok=True)
        run(["uv", "lock"], cwd=directory, quiet=quiet)
    run(["uv", "sync"], cwd=directory, quiet=quiet)


def _run_pixi_install(directory: Path, *, allow_fail: bool = False) -> bool:
    try:
        run(["pixi", "install"], cwd=directory, quiet=True)
        return True
    except LabError:
        if allow_fail:
            return False
        raise


def _run_uv_sync(directory: Path, *, allow_fail: bool = False) -> bool:
    try:
        run(["uv", "sync"], cwd=directory, quiet=True)
        return True
    except LabError:
        if allow_fail:
            return False
        raise


def write_manifest(path: Path, manifest: EnvManifest) -> None:
    path.write_text(manifest.model_dump_json(indent=2) + "\n")


def read_manifest(path: Path) -> EnvManifest:
    return EnvManifest.model_validate_json(path.read_text())


def _copy_project_files(source: Path, dest: Path, kind: ProjectKind) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    toml, lock = KIND_FILES[kind]
    shutil.copy2(source / toml, dest / toml)
    if (source / lock).is_file():
        shutil.copy2(source / lock, dest / lock)
    for extra in EXTRAS:
        src = source / extra
        if src.is_file():
            shutil.copy2(src, dest / extra)


def _assert_save_space(path: Path) -> None:
    from astroai_lab.core.disk_usage import disk_usage

    info = disk_usage(path)
    if info is None:
        return
    # 98% matches agent setup, but only for Ceph directory quotas (small /arc
    # homes). statvfs 98% on a large disk can still have gigabytes free.
    if info.source == "ceph-xattr" and info.pct >= _MAX_QUOTA_PCT:
        raise LabError(
            f"Quota {info.pct}% — refusing save",
            hint="Free space on /arc or use a different --to path",
        )
    if info.free_bytes < _MIN_FREE_BYTES:
        raise LabError(
            f"Low disk space ({info.free_bytes // 1024 // 1024}MB free) — save may fail",
            hint="Free space on /arc or use a different --to path",
        )


def _replace_dir(staging: Path, dest: Path) -> None:
    """Atomically replace dest with staging. Restores dest if the swap fails."""
    parent = dest.parent
    backup = parent / f".{dest.name}.bak-{os.getpid()}"
    if backup.exists():
        shutil.rmtree(backup)
    try:
        if dest.exists():
            dest.rename(backup)
        staging.rename(dest)
    except OSError:
        if not dest.exists() and backup.exists():
            backup.rename(dest)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def save_env(name: str, save_dir: Path, source: Path, *, full: bool = False) -> Path:
    kind = require_project(source)
    parent = save_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    probe = save_dir if save_dir.is_dir() else parent
    _assert_save_space(probe)

    staging = parent / f".{save_dir.name}.staging-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        _copy_project_files(source, staging, kind)
        if full:
            env_dir = ENV_DIRS[kind]
            src_env = source / env_dir
            if not src_env.is_dir():
                raise LabError(
                    f"No {env_dir} directory to pack.",
                    hint="Run pixi install or uv sync first.",
                )
            try:
                tar_zst(src_env, staging / "env.tar.zst", arcname=env_dir)
            except LabError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise LabError("Failed to compress environment pack") from exc
        write_manifest(
            staging / "manifest.json",
            EnvManifest(
                name=name,
                kind=kind,
                saved_at=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                saved_from=str(source.resolve()),
                user=os.environ.get("USER", "").strip() or "unknown",
                full=full,
            ),
        )
        _replace_dir(staging, save_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return save_dir


def tar_zst(source: Path, dest: Path, *, arcname: str) -> None:
    """Stream tar | zstd into dest. Raises LabError if either process fails."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            tar = subprocess.Popen(
                ["tar", "-C", str(source.parent), "-cf", "-", arcname],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                zstd = subprocess.Popen(
                    ["zstd", "-T0", "-"],
                    stdin=tar.stdout,
                    stdout=out,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                tar.kill()
                tar.wait()
                raise LabError(
                    "zstd not found — cannot pack environment",
                    hint="Install zstd or save without --full",
                ) from exc
            if tar.stdout is not None:
                tar.stdout.close()
            zstd.communicate()
            tar.communicate()
    except FileNotFoundError as exc:
        raise LabError(
            "tar not found — cannot pack environment",
            hint="Install tar or save without --full",
        ) from exc
    if tar.returncode not in (0, None):
        raise LabError("Failed to compress environment pack")
    if zstd.returncode not in (0, None):
        raise LabError("Failed to compress environment pack")


def resolve_save_dir(name: str, save_root: Path, from_path: Path | None) -> Path:
    candidates: list[Path] = []
    if from_path is not None:
        candidates.append(from_path)
        candidates.append(from_path / name)
    else:
        candidates.append(save_root / name)
    for save_dir in candidates:
        if (save_dir / "manifest.json").is_file():
            return save_dir
    shown = candidates[0]
    raise LabError(
        f"Save not found: {shown}",
        hint=f"astroai save --list\n  astroai save {name}",
    )


def list_saves(save_root: Path) -> list[tuple[Path, EnvManifest]]:
    if not save_root.is_dir():
        return []
    results: list[tuple[Path, EnvManifest]] = []
    for entry in sorted(save_root.iterdir()):
        manifest_path = entry / "manifest.json"
        if entry.is_dir() and manifest_path.is_file():
            results.append((entry, read_manifest(manifest_path)))
    return results


def save_rows(save_root: Path) -> list[dict[str, str]]:
    return [
        {
            "name": m.name,
            "kind": m.kind.value,
            "saved_at": m.saved_at,
            "path": str(entry),
            "full": str(m.full).lower(),
        }
        for entry, m in list_saves(save_root)
    ]


def warm_cache(save_dir: Path) -> None:
    manifest = read_manifest(save_dir / "manifest.json")
    toml, lock = KIND_FILES[manifest.kind]
    with tempfile.TemporaryDirectory(prefix="astroai-lab-cache-") as tmp:
        tmp_path = Path(tmp)
        src_toml = save_dir / toml
        if not src_toml.is_file():
            return
        shutil.copy2(src_toml, tmp_path / toml)
        src_lock = save_dir / lock
        if src_lock.is_file():
            shutil.copy2(src_lock, tmp_path / lock)
        if manifest.kind == ProjectKind.PIXI:
            run(["pixi", "install", "--quiet"], cwd=tmp_path, quiet=True)
        else:
            run(["uv", "sync", "--quiet"], cwd=tmp_path, quiet=True)


def bootstrap_lock(save_dir: Path, project_dir: Path) -> bool:
    manifest = read_manifest(save_dir / "manifest.json")
    kind = detect_project(project_dir)
    if kind is None or kind != manifest.kind:
        return False
    _toml, lock = KIND_FILES[kind]
    if (project_dir / lock).is_file():
        return False
    src = save_dir / lock
    if not src.is_file():
        return False
    shutil.copy2(src, project_dir / lock)
    return True


def restore_env(save_dir: Path, target: Path) -> None:
    manifest = read_manifest(save_dir / "manifest.json")
    target.mkdir(parents=True, exist_ok=True)
    _copy_project_files(save_dir, target, manifest.kind)

    packed = save_dir / "env.tar.zst"
    if manifest.full and packed.is_file():
        _unpack_env(packed, target)
        return
    install_project(target)


def _unpack_env(packed: Path, target: Path) -> None:
    try:
        zstd = subprocess.Popen(
            ["zstd", "-d", "-c", str(packed)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise LabError(
            "zstd not found — cannot unpack full environment pack",
            hint="Install zstd or resume a lockfile save (without --full)",
        ) from exc
    tar_failed = False
    try:
        subprocess.run(["tar", "-xf", "-"], cwd=target, stdin=zstd.stdout, check=True)
    except FileNotFoundError as exc:
        tar_failed = True
        raise LabError(
            "tar not found — cannot unpack full environment pack",
            hint="Install tar or resume a lockfile save (without --full)",
        ) from exc
    except subprocess.CalledProcessError as exc:
        tar_failed = True
        raise LabError("Failed to unpack full environment pack") from exc
    finally:
        if zstd.stdout is not None:
            zstd.stdout.close()
        zstd.wait()
        if not tar_failed and zstd.returncode not in (0, None):
            raise LabError("Failed to decompress full environment pack")


def format_dir_size(path: Path) -> str:
    from astroai_lab.core.storage import dir_size

    size = dir_size(path)
    if size == 0:
        return "0 B"
    return naturalsize(size)


def init_project(target: Path, *, use_uv: bool = False) -> ProjectKind:
    target.mkdir(parents=True, exist_ok=True)
    if use_uv:
        run(["uv", "init", "--no-readme"], cwd=target)
        return ProjectKind.UV
    run(["pixi", "init", "--no-progress"], cwd=target)
    return ProjectKind.PIXI
