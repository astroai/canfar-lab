"""canfar-lab — in-session workbench for AstroAI sessions on CANFAR."""

from __future__ import annotations

from pathlib import Path

from canfar_lab.version import __version__, display_version, version_info


def config_dir() -> Path:
    """Workbench config directory (~/.canfar/lab)."""
    return Path.home() / ".canfar" / "lab"


def saves_dir() -> Path:
    """Default directory for saved lockfile environments."""
    return config_dir() / "saves"


__all__ = ["__version__", "config_dir", "display_version", "saves_dir", "version_info"]
