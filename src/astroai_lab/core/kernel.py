"""Jupyter kernel helpers with scratch-safe cache env."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from astroai_lab.core.project import require_project
from astroai_lab.errors import LabError
from astroai_lab.shell.session_env import resolve_session_env
from astroai_lab.utils.subprocess import run, run_capture

# Cache-related exports injected into kernel.json so notebooks inherit hygiene
# even when Jupyter was started without a login shell / profile.
_KERNEL_ENV_KEYS = (
    "UV_CACHE_DIR",
    "PIXI_CACHE_DIR",
    "RATTLER_CACHE_DIR",
    "PIP_CACHE_DIR",
    "NPM_CONFIG_CACHE",
    "HF_HOME",
    "TORCH_HOME",
    "TRANSFORMERS_CACHE",
    "HF_DATASETS_CACHE",
    "CONDA_PKGS_DIRS",
    "MAMBA_PKGS_DIRS",
    "MAMBA_ROOT_PREFIX",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "MPLCONFIGDIR",
    "WORK",
    "SRCDIR",
    "SCRATCH",
    "PROJECT",
    "ASTROAI_LAB_BIN_DIR",
    "ASTROAI_LAB_RUNTIME_ROOT",
)


def _kernels_dir() -> Path:
    return Path.home() / ".local" / "share" / "jupyter" / "kernels"


def _python_for_project(project: Path) -> Path:
    kind = require_project(project)
    if kind.value == "pixi":
        return project / ".pixi" / "envs" / "default" / "bin" / "python"
    return project / ".venv" / "bin" / "python"


def _scratch_venv_python(name: str = "notebook") -> Path:
    env = resolve_session_env(ensure=True)
    scratch = env.scratch_dir
    if scratch is None:
        raise LabError(
            "No writable /scratch — cannot create notebook venv off $HOME.",
            hint="Launch a session with scratch, or use a project under $WORK.",
        )
    root = scratch / ".astroai-lab" / "venvs" / name
    py = root / "bin" / "python"
    if py.is_file():
        return py
    run([sys.executable, "-m", "venv", str(root)])
    run([str(py), "-m", "pip", "install", "-q", "ipykernel"])
    return py


def _patch_kernel_env(kernel_name: str) -> Path:
    """Write cache redirects into kernel.json env block."""
    kdir = _kernels_dir() / kernel_name
    kjson = kdir / "kernel.json"
    if not kjson.is_file():
        raise LabError(f"Kernel spec missing: {kjson}")
    data = json.loads(kjson.read_text(encoding="utf-8"))
    exports = resolve_session_env(ensure=True).exports()
    env_block = dict(data.get("env") or {})
    for key in _KERNEL_ENV_KEYS:
        if key in exports:
            env_block[key] = exports[key]
    data["env"] = env_block
    kjson.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return kjson


def register_kernel(project: Path, *, name: str | None = None) -> str:
    if shutil.which("jupyter") is None:
        raise LabError("jupyter not found.", hint="Use a notebook session image.")
    require_project(project)
    py = _python_for_project(project)
    if not py.is_file():
        raise LabError("Project environment not installed.", hint="pixi install")

    os.environ.setdefault("JUPYTER_CONFIG_DIR", str(Path.home() / ".jupyter"))
    Path(os.environ["JUPYTER_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)

    kernel_name = name or project.name
    display = f"Python ({kernel_name})"
    run(
        [
            str(py),
            "-m",
            "ipykernel",
            "install",
            "--user",
            f"--name={kernel_name}",
            f"--display-name={display}",
        ]
    )
    kjson = _kernels_dir() / kernel_name / "kernel.json"
    if not kjson.is_file():
        # Tests mock `run`; still write a stub so env patching can proceed.
        kjson.parent.mkdir(parents=True, exist_ok=True)
        kjson.write_text(
            json.dumps(
                {
                    "argv": [str(py), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                    "display_name": display,
                    "language": "python",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    _patch_kernel_env(kernel_name)
    return kernel_name


def ensure_scratch_safe_kernel(*, name: str = "astroai") -> str:
    """Register/refresh a default notebook kernel with scratch-backed caches.

    Notebook-only students: no pixi/uv project required.
    """
    py = _scratch_venv_python(name)
    os.environ.setdefault("JUPYTER_CONFIG_DIR", str(Path.home() / ".jupyter"))
    Path(os.environ["JUPYTER_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
    display = f"AstroAI ({name})"
    run(
        [
            str(py),
            "-m",
            "ipykernel",
            "install",
            "--user",
            f"--name={name}",
            f"--display-name={display}",
        ]
    )
    _patch_kernel_env(name)
    return name


def list_kernels() -> list[dict[str, str]]:
    if shutil.which("jupyter") is None:
        return []
    try:
        out = run_capture(["jupyter", "kernelspec", "list", "--json"])
        data = json.loads(out)
        specs = data.get("kernelspecs", {})
        return [{"name": k, "path": v.get("resource_dir", "")} for k, v in specs.items()]
    except (LabError, json.JSONDecodeError):
        return []


def unregister_kernel(name: str) -> None:
    if shutil.which("jupyter") is None:
        raise LabError("jupyter not found.")
    run(["jupyter", "kernelspec", "remove", "-y", name])
