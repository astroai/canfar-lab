from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from canfar_lab import config_dir, saves_dir
from canfar_lab.core.session_common import PLATFORM_SRC, ensure_writable_dir, overlay_work_dir


class LabSettings(BaseSettings):
    """Session workbench settings (Slurm-style env vars + optional config.yaml)."""

    model_config = SettingsConfigDict(
        env_prefix="CANFAR_LAB_",
        env_nested_delimiter="__",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SRCDIR is the source directory (clones, init). WORK is the same path,
    # kept as a synonym. Skaha TMP_SRC_DIR is mapped onto SRCDIR in the image
    # profile. Overlay /srcdir relocates to $SCRATCH/src unless the user set
    # something else.
    work_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("SRCDIR", "srcdir", "WORK", "work_dir"),
    )
    scratch_dir: Path | None = Field(
        default=None, validation_alias=AliasChoices("SCRATCH", "scratch_dir")
    )
    save_dir: Path | None = Field(default=None)
    default_pm: Literal["pixi", "uv"] = "pixi"
    clone_from_env: str | None = None

    # --- sync + desk (docs/desk-sync.md §5.3) — flat keys in config.yaml ------
    # Shared home mailbox the desk/session watchers exchange requests through.
    # On a laptop set this to a shared path (SSHFS of arc:/home/$USER/…) or the
    # session side cannot see requests; see docs/desk-sync.md §6.4.
    sync_mailbox: Path | None = None
    sync_publish_mode: Literal["commit", "report"] = "commit"
    sync_absorb_mode: Literal["ff", "rebase"] | None = None
    sync_exclude: list[str] = Field(default_factory=list)
    # dsh spec for `canfar lab desk`: pin|alpha|next|latest|newest|<version>.
    dsh_version: str = "newest"
    desk_port: int = 5080
    apps_editor: str = "vscode"
    apps_terminal: str = "auto"
    session_name: str = "astroai-studio"
    session_image: str = "images.canfar.net/astroai/studio:latest"
    # Floating AstroAI desk. The bar is always injectable; this flag only
    # opens it at boot. ``CANFAR_LAB_DESK=1`` (also ``true`` / ``yes``).
    desk: bool = False

    def resolve_work_dir(self) -> Path:
        scratch = self.resolve_scratch_dir()
        # Explicit $SRCDIR or $WORK wins (a session or job started with it), then
        # the code dir chosen in Settings (persisted on /arc). The base image's
        # ENV WORK=/srcdir is a default, not a choice, so it comes after.
        work = _env_path("WORK")
        explicit_work = work if work is not None and work != PLATFORM_SRC else None
        candidates = [_env_path("SRCDIR"), explicit_work, _saved_code_dir(scratch), work]
        for raw in (*candidates, self.work_dir):
            if raw is None:
                continue
            raw = raw.expanduser()
            relocated = overlay_work_dir(raw, scratch)
            if relocated is not None:
                return relocated
            if ensure_writable_dir(raw):
                return raw
        relocated = overlay_work_dir(Path("/srcdir"), scratch)
        if relocated is not None:
            return relocated
        for candidate in (Path("/srcdir"), Path.home() / "work"):
            if candidate.is_dir() and os.access(candidate, os.W_OK):
                return candidate
        return Path.cwd()

    def resolve_scratch_dir(self) -> Path | None:
        for raw in (self.scratch_dir, _env_path("SCRATCH")):
            if raw is not None and raw.is_dir() and os.access(raw, os.W_OK):
                return raw
        default = Path("/scratch")
        if default.is_dir() and os.access(default, os.W_OK):
            return default
        return None

    def resolve_save_dir(self) -> Path:
        if self.save_dir is not None:
            return self.save_dir
        custom = _env_path("CANFAR_LAB_SAVE_DIR")
        if custom is not None:
            return custom
        return saves_dir()


def config_file_path() -> Path:
    return config_dir() / "config.yaml"


def _saved_code_dir(scratch: Path | None) -> Path | None:
    from canfar_lab.session.workspace import configured_code_dir

    try:
        return configured_code_dir(scratch=scratch)
    except Exception:  # noqa: BLE001 — a bad settings file must not break path resolution
        return None


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name, "").strip()
    return Path(val) if val else None


def _yaml_settings_source() -> dict:
    path = config_file_path()
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


@lru_cache
def get_settings() -> LabSettings:
    yaml_data = _yaml_settings_source()
    return LabSettings(**yaml_data)
