"""Studio profile paths and shared constants."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from canfar_lab.core.session_common import user_tag

StudioProfile = Literal["laptop", "canfar"]

#: dsh profile directory name (``$DSH_HOME/profiles/<name>``).
STUDIO_PROFILE_NAME = "astroai"

#: The shipped ``web`` template's bundle list — the profile ``astroai`` starts from.
WEB_TEMPLATE_BUNDLES: tuple[str, ...] = (
    "@deepseek-ai/dsh-base",
    "@deepseek-ai/dsh-web-app",
)

#: Experimental Agent Teams layers. Order matters: the Host layer supplies the
#: Team domain and its tools, the Web layer adds the roster/task-board UI on top
#: of it, and both must follow ``dsh-web-app``.
TEAM_BUNDLES: tuple[str, ...] = (
    "@deepseek-ai/dsh-experimental-agent-team-profile",
    "@deepseek-ai/dsh-experimental-agent-team-web-profile",
)

#: Injects ``x-opencode-session`` for OpenCode Go (avoids 400 MissingSessionID).
#: Vendored under ``data/studio/plugins/`` so CANFAR ``--no-install`` boots still
#: get it without a pnpm fetch.
OPENCODE_SESSION_BUNDLE = "dsh-opencode-session"
#: AstroAI mark/name in the sidebar and blank-chat hero (replaces the official
#: brand row). Vendored alongside the OpenCode plugin for the same reason.
BRAND_BUNDLE = "dsh-astroai-brand"
ASTROAI_EXTRA_BUNDLES: tuple[str, ...] = (OPENCODE_SESSION_BUNDLE, BRAND_BUNDLE)

#: Required layer order for every bundle the Studio profile names.
BUNDLE_ORDER: tuple[str, ...] = WEB_TEMPLATE_BUNDLES + TEAM_BUNDLES + ASTROAI_EXTRA_BUNDLES

#: Plugins an image bakes in: a pnpm project (hoisted) whose ``node_modules``
#: holds each plugin with its dependencies, so a ``--no-install`` boot resolves
#: them without a fetch. Every dependency that declares a dsh bundle is enabled.
BAKED_PLUGINS_ENV = "ASTROAI_STUDIO_BAKED_PLUGINS"
BAKED_PLUGINS_DIR = Path("/opt/astroai/dsh-plugins")

#: The community plugin market (Settings → Plugin Market).
MARKET_BUNDLE = "dshmarket"

#: The row shape the market and dsh's own Plugins page append to a profile's
#: patch layer to switch a plugin off or on.
PLUGIN_SWITCH_RE = re.compile(
    r"^- id: ['\"]?([A-Za-z0-9_.-]+)['\"]?\n  disabled: (true|false)[ \t]*(?:\n|\Z)", re.MULTILINE
)

#: Bundles that ship inside the dsh installation itself. They resolve from the
#: running `dsh`, never from the profile's `node_modules`, so they must never be
#: installed or reported missing.
IN_BOX_BUNDLES: tuple[str, ...] = (
    "@deepseek-ai/dsh-base",
    "@deepseek-ai/dsh-web-app",
    "@deepseek-ai/dsh-headless",
    "@deepseek-ai/dsh-sdk-app",
    "@deepseek-ai/dsh-sdk-minimal",
    "@deepseek-ai/dsh-acp-app",
)

#: Managed (AstroAI-owned) preset and skill roots under ``$HOME``.
MANAGED_STUDIO_REL = Path(".astroai") / "lab" / "studio"
MANAGED_BENCH_REL = Path(".astroai") / "lab" / "review-bench"

#: ``~/.agents/skills`` — dsh's ``user-agents`` root (rank 500), where
#: ``npx skills add astroai/canfar-skills`` installs.
AGENTS_SKILLS_REL = Path(".agents") / "skills"

#: Seconds of bash headroom per resource profile (pixi solves, native rebuilds).
PROFILE_BASH_TIMEOUT_SEC: dict[StudioProfile, int] = {"laptop": 600, "canfar": 300}

#: Advisory fan-out ceiling per resource profile, for Team briefs.
PROFILE_MAX_PARALLEL_CHILDREN: dict[StudioProfile, int] = {"laptop": 8, "canfar": 4}

#: Spill-file retention (days) on the session scratch; never ``0`` (unbounded).
SPILL_CLEANUP_DAYS = 7

#: Per-tool MCP call ceiling. A job submit that blocks on a cold Ray cluster
#: login legitimately takes minutes.
MCP_TOOL_TIMEOUT_MS = 600_000

#: The half of the MCP surface that a Studio chat needs in order to launch and
#: report on real CANFAR compute. The doctor names these when the handshake
#: finds them, so a stale CLI on the row is visible rather than silent.
CANFAR_MCP_TOOLS = (
    "job_submit",
    "job_run",
    "job_status",
    "job_list",
    "job_logs",
    "job_cancel",
    "cluster_start",
    "cluster_status",
    "cluster_stop",
    "dashboard_url",
    "session_resources",
    "jobs_report",
)

#: Env var (or ``~/.astroai/lab/.env`` key) pinning the ``astroai`` binary the
#: Studio MCP row runs, for a checkout whose CLI is ahead of the installed one.
MCP_BIN_ENV = "ASTROAI_STUDIO_MCP_BIN"

#: Banner marking a file as ours, so a hand-written file is never clobbered.
LAYER_MARK = "# AstroAI Studio"

PROFILE_PATCH_FILENAME = "cordis.patch.yml"
PROFILE_MANIFEST_FILENAME = "package.json"
PROFILE_WORKSPACE_FILENAME = "pnpm-workspace.yaml"

# Pin stays in sync with studio.DSH_VERSION / agents/dsh.yaml.
DSH_INSTALL_HINT = "npm install -g @deepseek-ai/dsh@0.1.5-rc.2"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def profile_dir(home: Path, name: str = STUDIO_PROFILE_NAME) -> Path:
    """``$DSH_HOME/profiles/<name>`` (``$DSH_HOME`` defaults to ``~/.dsh``)."""
    return dsh_home(home) / "profiles" / name


def dsh_home(home: Path) -> Path:
    """The harness home dsh itself resolves (``$DSH_HOME`` wins over ``~/.dsh``)."""
    override = os.environ.get("DSH_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return home / ".dsh"


def managed_studio_dir(home: Path) -> Path:
    """AstroAI-owned Studio assets (presets, skills) under ``$HOME``."""
    return home / MANAGED_STUDIO_REL


def managed_bench_dir(home: Path) -> Path:
    """The managed review-bench tree, whose skills carry the Studio skills."""
    return home / MANAGED_BENCH_REL


def managed_studio_preset_root(home: Path) -> Path:
    return managed_studio_dir(home) / "presets"


def managed_bench_preset_root(home: Path) -> Path:
    return managed_bench_dir(home) / "presets"


def agents_skills_dir(home: Path) -> Path:
    return home / AGENTS_SKILLS_REL


@dataclass(frozen=True)
class StateRoot:
    """Where one Studio profile keeps runtime state, and why."""

    path: Path
    durable: bool
    note: str | None = None


def resolve_state_root(
    home: Path,
    *,
    profile: StudioProfile,
    scratch: Path | None = None,
    env: dict[str, str] | None = None,
) -> StateRoot:
    """Runtime-state root: session logs, full-text index and spill files.

    Config stays on ``$HOME`` (see :mod:`canfar_lab.core.home_layout`); this is
    the bulk, append-heavy half. On CANFAR it lands on the session scratch and
    dies with the session — that is the documented trade, and ``/arc`` is where
    a durable copy belongs. Without a scratch, the platform temp dir is used
    rather than risking a full quota-constrained ``/arc`` home.
    """
    environ = env if env is not None else dict(os.environ)
    if profile == "canfar":
        root = scratch or _scratch_dir(environ)
        if root is not None:
            return StateRoot(path=root / f".studio-{user_tag()}", durable=False)
        tmp = environ.get("TMPDIR", "").strip() or "/tmp"
        return StateRoot(
            path=Path(tmp) / f".studio-{user_tag()}",
            durable=False,
            note=(
                "No writable /scratch found: Studio state falls back to "
                f"{tmp}, which is not persisted. Export durable sessions to "
                "/arc before the session ends."
            ),
        )
    return StateRoot(path=home / ".dsh" / "state", durable=True)


def _scratch_dir(environ: dict[str, str]) -> Path | None:
    candidates: list[Path] = []
    raw = environ.get("SCRATCH", "").strip()
    if raw:
        candidates.append(Path(raw))
    candidates.append(Path("/scratch"))
    for candidate in candidates:
        try:
            if candidate.is_dir() and os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    return None
