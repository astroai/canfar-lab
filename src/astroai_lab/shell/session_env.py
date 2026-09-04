"""Resolve AstroAI lab session environment (paths, caches, runtime dirs)."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from astroai_lab import config_dir
from astroai_lab.config.settings import get_settings
from astroai_lab.core.session_common import find_arc_project_root, scratch_cache_root, user_tag


def _path_under_roots(path: Path, *roots: Path) -> bool:
    for root in roots:
        if not root.is_dir():
            continue
        try:
            if path.is_relative_to(root):
                return True
        except OSError:
            continue
    return False


def _under_home(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
        home = Path.home().resolve()
        return resolved == home or resolved.is_relative_to(home)
    except (OSError, RuntimeError, ValueError):
        return False


def _session_cache_root(work: Path, scratch: Path | None) -> Path:
    """Package caches live on scratch, else work — never under $HOME."""
    root = scratch_cache_root(work, scratch)
    if not _under_home(root):
        return root
    return Path("/tmp") / f".cache-{user_tag()}"


def _session_cache_path(var: str, default: Path, work: Path, scratch: Path | None) -> Path:
    """Scratch-backed caches win over image build-time ENV when scratch is mounted.

    When scratch is absent, a pre-existing env value is kept only if it already
    lives under the work dir; otherwise the build-time system-prefix default
    (e.g. /usr/local/share/pixi/cache) is redirected to the session default.
    Paths under $HOME are never kept (CANFAR home quota).
    """
    raw = os.environ.get(var, "").strip()
    if raw:
        path = Path(raw)
        if not _under_home(path):
            roots: list[Path] = [work]
            if scratch is not None:
                roots.append(scratch)
            if _path_under_roots(path, *roots):
                return path
    return default


def _session_runtime_path(var: str, default: Path, scratch: Path | None) -> Path:
    """Runtime roots (uv/pixi/mamba) stay off /usr/local build-time prefixes.

    A pre-existing env value is kept only if it already lives under the runtime
    root (ASTROAI_LAB_RUNTIME_ROOT) or scratch; otherwise the build-time
    system-prefix default (e.g. /usr/local/share/micromamba) is redirected to
    the session default.
    """
    raw = os.environ.get(var, "").strip()
    if raw:
        path = Path(raw)
        runtime_root = os.environ.get("ASTROAI_LAB_RUNTIME_ROOT", "").strip()
        roots: list[Path] = []
        if scratch is not None:
            roots.append(scratch)
        if runtime_root:
            rp = Path(runtime_root)
            if rp not in roots:
                roots.append(rp)
        if _path_under_roots(path, *roots):
            return path
    return default


def resolve_work_dir() -> Path:
    settings = get_settings()
    return settings.resolve_work_dir()


def resolve_scratch_dir() -> Path | None:
    settings = get_settings()
    return settings.resolve_scratch_dir()


def user_bin_dir(work: Path, scratch: Path | None) -> Path:
    """CLI install dir — upstream-default ``~/.local/bin`` (not scratch).

    Caches and package runtimes still use scratch via :func:`runtime_root`.
    Override with ``ASTROAI_LAB_BIN_DIR``. ``work`` / ``scratch`` kept for
    call-site compatibility.
    """
    del work, scratch
    for key in ("ASTROAI_LAB_BIN_DIR",):
        raw = os.environ.get(key, "").strip()
        if raw:
            path = Path(raw)
            path.mkdir(parents=True, exist_ok=True)
            return path
    path = Path.home() / ".local" / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def team_bin_dir() -> Path | None:
    proj = find_arc_project_root()
    if proj is None:
        return None
    path = proj / ".local" / "bin"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


def npm_prefix_dir(bin_dir: Path) -> Path:
    """npm global prefix — home ``~/.local`` (parent of bin) by default."""
    for key in ("ASTROAI_LAB_NPM_PREFIX", "NPM_CONFIG_PREFIX"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return Path(raw)
    return bin_dir.parent


def runtime_root(work: Path, scratch: Path | None) -> Path:
    custom = os.environ.get("ASTROAI_LAB_RUNTIME_ROOT", "").strip()
    if custom:
        return Path(custom)
    user = user_tag()
    if scratch is not None:
        return scratch / f".runtime-{user}"
    return work / f".runtime-{user}"


@dataclass(frozen=True)
class SessionEnv:
    work_dir: Path
    scratch_dir: Path | None
    project_dir: Path | None
    astroai_lab_bin_dir: Path
    astroai_lab_team_bin: Path | None
    astroai_lab_npm_prefix: Path
    astroai_lab_runtime_root: Path
    astroai_lab_save_dir: Path
    astroai_lab_config_dir: Path
    uv_cache_dir: Path
    pip_cache_dir: Path
    npm_config_cache: Path
    pixi_cache_dir: Path
    rattler_cache_dir: Path
    mamba_pkgs_dirs: Path
    uv_python_install_dir: Path
    uv_tool_dir: Path
    pixi_home: Path
    mamba_root_prefix: Path
    hf_home: Path
    torch_home: Path
    tmpdir: Path
    xdg_cache_home: Path
    xdg_config_home: Path
    xdg_data_home: Path
    pythonpath_extra: str

    def exports(self) -> dict[str, str]:
        out: dict[str, str] = {
            "SRCDIR": str(self.work_dir),
            "WORK": str(self.work_dir),
            "ASTROAI_LAB_BIN_DIR": str(self.astroai_lab_bin_dir),
            "ASTROAI_LAB_NPM_PREFIX": str(self.astroai_lab_npm_prefix),
            "ASTROAI_LAB_RUNTIME_ROOT": str(self.astroai_lab_runtime_root),
            "ASTROAI_LAB_SAVE_DIR": str(self.astroai_lab_save_dir),
            "ASTROAI_LAB_CONFIG_DIR": str(self.astroai_lab_config_dir),
            "NPM_CONFIG_PREFIX": str(self.astroai_lab_npm_prefix),
            "PYTHONUSERBASE": str(self.astroai_lab_npm_prefix),
            "UV_CACHE_DIR": str(self.uv_cache_dir),
            "PIP_CACHE_DIR": str(self.pip_cache_dir),
            "NPM_CONFIG_CACHE": str(self.npm_config_cache),
            "npm_config_cache": str(self.npm_config_cache),
            "PIXI_CACHE_DIR": str(self.pixi_cache_dir),
            "RATTLER_CACHE_DIR": str(self.rattler_cache_dir),
            "MAMBA_PKGS_DIRS": str(self.mamba_pkgs_dirs),
            "CONDA_PKGS_DIRS": str(self.mamba_pkgs_dirs),
            "UV_PYTHON_INSTALL_DIR": str(self.uv_python_install_dir),
            "UV_PYTHON_BIN_DIR": str(self.astroai_lab_bin_dir),
            "UV_TOOL_DIR": str(self.uv_tool_dir),
            "UV_TOOL_BIN_DIR": str(self.astroai_lab_bin_dir),
            "PIXI_HOME": str(self.pixi_home),
            "MAMBA_ROOT_PREFIX": str(self.mamba_root_prefix),
            "HF_HOME": str(self.hf_home),
            "TRANSFORMERS_CACHE": str(self.hf_home),
            "HF_DATASETS_CACHE": str(self.hf_home / "datasets"),
            "TORCH_HOME": str(self.torch_home),
            "MPLCONFIGDIR": str(self.xdg_cache_home / "matplotlib"),
            "TMPDIR": str(self.tmpdir),
            "XDG_CACHE_HOME": str(self.xdg_cache_home),
            "XDG_CONFIG_HOME": str(self.xdg_config_home),
            "XDG_DATA_HOME": str(self.xdg_data_home),
            "UV_LINK_MODE": os.environ.get("UV_LINK_MODE", "").strip() or "copy",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
        if self.scratch_dir is not None:
            out["SCRATCH"] = str(self.scratch_dir)
        if self.project_dir is not None:
            out["PROJECT"] = str(self.project_dir)
        if self.astroai_lab_team_bin is not None:
            out["ASTROAI_LAB_TEAM_BIN"] = str(self.astroai_lab_team_bin)
        path_parts = []
        if self.astroai_lab_team_bin is not None:
            path_parts.append(str(self.astroai_lab_team_bin))
        path_parts.append(str(self.astroai_lab_bin_dir))
        out["ASTROAI_LAB_PATH_PREFIX"] = ":".join(path_parts)
        if self.pythonpath_extra:
            out["PYTHONPATH"] = self.pythonpath_extra
        return out

    def ensure_dirs(self) -> None:
        for path in (
            self.work_dir,
            self.astroai_lab_bin_dir,
            self.uv_cache_dir,
            self.pip_cache_dir,
            self.npm_config_cache,
            self.pixi_cache_dir,
            self.rattler_cache_dir,
            self.mamba_pkgs_dirs,
            self.uv_python_install_dir,
            self.uv_tool_dir,
            self.pixi_home,
            self.mamba_root_prefix,
            self.hf_home,
            self.torch_home,
            self.tmpdir,
            self.xdg_cache_home,
            self.xdg_data_home,
            self.astroai_lab_npm_prefix,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if self.astroai_lab_team_bin is not None:
            self.astroai_lab_team_bin.mkdir(parents=True, exist_ok=True)


def _pythonpath_extra(work: Path) -> str:
    parts: list[str] = []
    extra = os.environ.get("ASTROAI_LAB_PYTHONPATH", "").strip()
    if extra:
        parts.extend(p for p in extra.split(":") if p)
    manifest = work / ".astroai-lab" / "pythonpath"
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                parts.append(line)
    existing = os.environ.get("PYTHONPATH", "").strip()
    if existing:
        parts.append(existing)
    return ":".join(parts)


def resolve_session_env(*, ensure: bool = True) -> SessionEnv:
    work = resolve_work_dir()
    scratch = resolve_scratch_dir()
    cache_root = _session_cache_root(work, scratch)
    bin_dir = user_bin_dir(work, scratch)
    runtime = runtime_root(work, scratch)
    home = Path.home()
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    # Keep XDG_DATA_HOME on $HOME for small durable agent/jupyter specs;
    # package download caches use XDG_CACHE_HOME / explicit *_CACHE_* vars.
    # When a scratch disk exists, bulk data lands there instead so agent
    # runtimes do not eat the home quota; tiny durable specs stay on /arc.
    if scratch is not None:
        xdg_data = _session_cache_path("XDG_DATA_HOME", cache_root / "data", work, scratch)
    else:
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local" / "share")))
    xdg_cache = _session_cache_path("XDG_CACHE_HOME", cache_root, work, scratch)

    if scratch is not None:
        hf_default = cache_root / "huggingface"
        torch_default = cache_root / "torch"
        tmp_default = scratch / f".tmp-{user_tag()}"
        hf = _session_cache_path("HF_HOME", hf_default, work, scratch)
        torch = _session_cache_path("TORCH_HOME", torch_default, work, scratch)
        tmp = _session_cache_path("TMPDIR", tmp_default, work, scratch)
        pixi_home = _session_runtime_path("PIXI_HOME", runtime / "pixi", scratch)
    else:
        hf = Path(os.environ.get("HF_HOME", str(xdg_cache / "huggingface")))
        torch = Path(os.environ.get("TORCH_HOME", str(xdg_cache / "torch")))
        tmp = Path(os.environ.get("TMPDIR", str(work / f".tmp-{user_tag()}")))
        pixi_home = Path(os.environ.get("PIXI_HOME", str(xdg_data / "pixi")))

    env = SessionEnv(
        work_dir=work,
        scratch_dir=scratch,
        project_dir=find_arc_project_root(),
        astroai_lab_bin_dir=bin_dir,
        astroai_lab_team_bin=team_bin_dir(),
        astroai_lab_npm_prefix=npm_prefix_dir(bin_dir),
        astroai_lab_runtime_root=runtime,
        astroai_lab_save_dir=get_settings().resolve_save_dir(),
        astroai_lab_config_dir=config_dir(),
        uv_cache_dir=_session_cache_path("UV_CACHE_DIR", cache_root / "uv", work, scratch),
        pip_cache_dir=_session_cache_path("PIP_CACHE_DIR", cache_root / "pip", work, scratch),
        npm_config_cache=_session_cache_path("NPM_CONFIG_CACHE", cache_root / "npm", work, scratch),
        pixi_cache_dir=_session_cache_path("PIXI_CACHE_DIR", cache_root / "pixi", work, scratch),
        rattler_cache_dir=_session_cache_path(
            "RATTLER_CACHE_DIR", cache_root / "rattler", work, scratch
        ),
        mamba_pkgs_dirs=_session_cache_path(
            "MAMBA_PKGS_DIRS", cache_root / "conda" / "pkgs", work, scratch
        ),
        uv_python_install_dir=_session_runtime_path(
            "UV_PYTHON_INSTALL_DIR", runtime / "uv" / "python", scratch
        ),
        uv_tool_dir=_session_runtime_path("UV_TOOL_DIR", runtime / "uv" / "tools", scratch),
        pixi_home=pixi_home,
        mamba_root_prefix=_session_runtime_path(
            "MAMBA_ROOT_PREFIX", runtime / "micromamba", scratch
        ),
        hf_home=hf,
        torch_home=torch,
        tmpdir=tmp,
        xdg_cache_home=xdg_cache,
        xdg_config_home=xdg_config,
        xdg_data_home=xdg_data,
        pythonpath_extra=_pythonpath_extra(work),
    )
    if ensure:
        env.ensure_dirs()
    return env


def export_shell(*, ensure: bool = True) -> str:
    env = resolve_session_env(ensure=ensure)
    lines = [f"export {key}={shlex.quote(val)}" for key, val in env.exports().items()]
    return "\n".join(lines)


def export_json(*, ensure: bool = True) -> dict[str, str]:
    """Resolved session environment as a plain dict (machine-readable).

    Same values as `export_shell`, without the `export KEY=...` shell syntax.
    """
    return resolve_session_env(ensure=ensure).exports()
