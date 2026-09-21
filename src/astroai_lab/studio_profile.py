"""The owned dsh profile behind ``astroai studio``: content, scaffolding, doctor.

``astroai studio`` boots a profile named :data:`STUDIO_PROFILE_NAME` instead of
the shipped ``web`` profile, so the Studio composition is ours to define:

* the shipped browser composition (``dsh-base`` + ``dsh-web-app``),
* the experimental Agent Teams layers (roster + durable mailbox + shared task
  board) — opt-in upstream, and no shipped profile enables them,
* AstroAI storage routing (sessions, full-text index and spill files off
  ``$HOME``; see :mod:`astroai_lab.core.home_layout`),
* the managed preset root, so the ``AstroAI Studio Team`` preset appears in the
  New-session picker,
* the ``astroai mcp serve`` server, which is what gives a session real CANFAR
  job and cluster tools.

Three files under ``$DSH_HOME/profiles/astroai`` are the whole surface dsh
reads, and this module generates all of them:

``package.json``
    The profile manifest; ``dsh.profile.bundles`` **is** the layer order.
``cordis.patch.yml``
    Our patch layer, applied after every bundle layer.
``pnpm-workspace.yaml``
    ``nodeLinker: hoisted``, so bundle installs land where the launcher looks.

Planning (:func:`plan_studio_profile`) is pure and side-effect free;
:func:`apply_studio_profile` is the only function that writes or shells out.
That split keeps the generated content unit-testable and lets ``--dry-run``
report exactly what a real run would do.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from astroai_lab.core.session_common import user_tag
from astroai_lab.errors import LabError
from astroai_lab.utils.subprocess import run_capture, run_cmd

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
ASTROAI_EXTRA_BUNDLES: tuple[str, ...] = (OPENCODE_SESSION_BUNDLE,)

#: Required layer order for every bundle the Studio profile names.
BUNDLE_ORDER: tuple[str, ...] = WEB_TEMPLATE_BUNDLES + TEAM_BUNDLES + ASTROAI_EXTRA_BUNDLES

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

    Config stays on ``$HOME`` (see :mod:`astroai_lab.core.home_layout`); this is
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


# ---------------------------------------------------------------------------
# Pure planning
# ---------------------------------------------------------------------------


def desired_bundles(
    existing: Iterable[str],
    *,
    with_team: bool,
    required: Iterable[str] = WEB_TEMPLATE_BUNDLES,
) -> list[str]:
    """Normalize a profile's bundle list to the required prefix, then extras.

    ``dsh plugin add`` appends each new dependency to the manifest in install
    order, which is not necessarily the layer order the composition needs, so
    every write goes through this function and the doctor checks the result.

    The ``required`` prefix (the shipped web composition the Studio profile is
    built on) and the Team layers are always present and always first; bundles a
    user added themselves keep their relative order after them, and are never
    dropped. Studio is a web-based profile, so a manifest that lost ``web-app``
    is repaired rather than honoured.
    """
    wanted = [*required, *(TEAM_BUNDLES if with_team else ()), *ASTROAI_EXTRA_BUNDLES]
    extras = [
        name
        for name in existing
        if name not in wanted
        and (with_team or name not in TEAM_BUNDLES)
        and name not in ASTROAI_EXTRA_BUNDLES
    ]
    return wanted + extras


def team_bundles_missing(existing: list[str], *, with_team: bool) -> list[str]:
    """Team bundles that should be declared in the manifest but are not."""
    if not with_team:
        return []
    return [name for name in TEAM_BUNDLES if name not in existing]


def vendored_opencode_session_plugin() -> Path:
    """Packaged ``dsh-opencode-session`` tree (offline CANFAR / --no-install)."""
    return Path(__file__).resolve().parent / "data" / "studio" / "plugins" / OPENCODE_SESSION_BUNDLE


def ensure_opencode_session_plugin(
    profile_dir: Path,
    *,
    dry_run: bool = False,
) -> str | None:
    """Install the OpenCode Go session-header plugin into the Studio profile.

    OpenCode Go returns ``400 MissingSessionID`` unless every chat request
    carries ``x-opencode-session``. Stock dsh does not send it; this plugin
    injects a stable per-conversation value. Always runs — even under
    ``--no-install`` — by copying the vendored package (no pnpm/network).
    """
    import shutil

    src = vendored_opencode_session_plugin()
    if not (src / "package.json").is_file() or not (src / "lib" / "index.js").is_file():
        return None
    dest = profile_dir / "node_modules" / OPENCODE_SESSION_BUNDLE
    if dry_run:
        return f"would install {OPENCODE_SESSION_BUNDLE} → {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return f"installed {OPENCODE_SESSION_BUNDLE} (x-opencode-session header)"


def bundle_installed(directory: Path, name: str) -> bool:
    """Whether a bundle resolves for a profile.

    Either pnpm installed it into the profile's own ``node_modules``, or the
    launcher's module-fallback position (``$DSH_HOME/profiles/node_modules``,
    one level up) carries it because the installation depends on it.
    """
    for base in (directory / "node_modules", directory.parent / "node_modules"):
        if (base / Path(name) / "package.json").is_file():
            return True
    return False


def out_of_tree_bundles(bundles: Iterable[str]) -> list[str]:
    """Bundles the profile has to install itself (everything but the in-box set).

    AstroAI extras (``dsh-opencode-session``) are vendored and copied by
    :func:`ensure_opencode_session_plugin`, so they never go through pnpm.
    """
    return [
        name for name in bundles if name not in IN_BOX_BUNDLES and name not in ASTROAI_EXTRA_BUNDLES
    ]


def uninstalled_bundles(directory: Path, bundles: Iterable[str]) -> list[str]:
    """Declared out-of-tree packs that still need a ``dsh plugin add`` (pnpm)."""
    return [name for name in out_of_tree_bundles(bundles) if not bundle_installed(directory, name)]


def _yaml_scalar(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _js(expression: str) -> str:
    """A dsh ``!!js`` config expression, evaluated by the loader at boot."""
    return f"!!js {expression}"


@dataclass(frozen=True)
class StudioPlan:
    """Everything a real run would write and run, computed without touching disk."""

    name: str
    dir: Path
    profile: StudioProfile
    state: StateRoot
    with_team: bool
    bundles: tuple[str, ...]
    manifest: dict[str, Any]
    layer_yaml: str
    workspace_yaml: str
    mcp_command: tuple[str, ...]
    files: tuple[Path, ...]
    commands: tuple[tuple[str, ...], ...]
    notes: tuple[str, ...] = ()
    fresh_profile: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dir": str(self.dir),
            "profile": self.profile,
            "state_root": str(self.state.path),
            "state_durable": self.state.durable,
            "with_team": self.with_team,
            "bundles": list(self.bundles),
            "mcp_command": list(self.mcp_command),
            "files": [str(p) for p in self.files],
            "commands": [list(c) for c in self.commands],
            "notes": list(self.notes),
            "fresh_profile": self.fresh_profile,
        }


def mcp_serve_command(
    *, astroai_bin: str | None = None, home: Path | None = None
) -> tuple[str, ...]:
    """Argv for ``astroai mcp serve`` (the CANFAR job/cluster MCP server).

    Resolution order: an explicit path, then ``ASTROAI_STUDIO_MCP_BIN`` (in the
    environment or in ``~/.astroai/lab/.env``, so a working checkout can pin the
    row without every later ``--prepare`` reverting it to whatever ``PATH``
    happened to hold), then ``astroai`` on ``PATH``, then this interpreter.
    """
    explicit = astroai_bin or mcp_bin_override(home)
    if explicit:
        return (explicit, "mcp", "serve")
    found = shutil.which("astroai")
    if found:
        return (found, "mcp", "serve")
    return (sys.executable or "python3", "-m", "astroai_lab", "mcp", "serve")


def mcp_bin_override(home: Path | None = None) -> str | None:
    """The pinned Studio MCP binary, from the environment or the lab dotenv."""
    home = home or Path.home()
    value = os.environ.get(MCP_BIN_ENV, "").strip()
    if not value:
        dotenv = home / ".astroai" / "lab" / ".env"
        if dotenv.is_file():
            try:
                from astroai_lab.agent.setup import _read_dotenv_value

                value = (_read_dotenv_value(dotenv, MCP_BIN_ENV) or "").strip()
            except Exception:  # noqa: BLE001 — a pin we cannot read is not fatal
                value = ""
    if not value:
        return None
    expanded = Path(value).expanduser()
    if not expanded.exists():
        return None
    return str(expanded)


def mcp_row_yaml(command: tuple[str, ...]) -> str:
    """The ``dsh-mcp-client`` row, as an ``insert`` entry.

    ``env`` is required by the row schema and is merged over a *scrubbed*
    ambient environment, so the handful of variables the job tools need are
    read at boot with ``!!js`` rather than baked in as literals — no secret and
    no machine-specific path ever lands in the profile directory.
    """
    args = ", ".join(_yaml_scalar(part) for part in command[1:])
    user_expr = _js("process.env.USER ?? ''")
    shell_expr = _js("process.env.SHELL ?? '/bin/bash'")
    scratch_expr = _js("process.env.SCRATCH ?? ''")
    project_expr = _js("process.env.PROJECT ?? ''")
    return "\n".join(
        [
            "# AstroAI hub MCP server: CANFAR job and cluster tools plus this",
            "# session's own resource snapshot, so a Studio chat can launch real",
            "# compute and report on it. An MCP server command is trusted,",
            "# unsandboxed executable code — this one is our own CLI.",
            "- insert:",
            "    - id: mcp-astroai",
            "      name: '@deepseek-ai/dsh-mcp-client'",
            "      config:",
            "        transport: stdio",
            "        serverName: astroai",
            f"        command: {_yaml_scalar(command[0])}",
            f"        args: [{args}]",
            "        env:",
            f"          HOME: {_js('process.env.HOME')}",
            f"          PATH: {_js('process.env.PATH')}",
            f"          USER: {user_expr}",
            f"          SHELL: {shell_expr}",
            f"          SCRATCH: {scratch_expr}",
            f"          PROJECT: {project_expr}",
            "        cwd: " + _js("process.cwd()"),
            f"        toolCallTimeoutMs: {MCP_TOOL_TIMEOUT_MS}",
            "        failOnStartupError: false",
        ]
    )


def studio_layer_yaml(
    home: Path,
    *,
    profile: StudioProfile,
    state: StateRoot,
    command: tuple[str, ...],
    with_team: bool = True,
) -> str:
    """The profile's own ``cordis.patch.yml``.

    A patch row replaces the targeted row's **whole** config (there is no deep
    merge), so every field kept from the bundle layer is restated here. Each
    row below targets a row that exists in ``dsh-base`` or ``dsh-web-app`` at
    dsh 0.1.5-rc.2; the doctor re-checks that with ``--dump-config``.
    """
    timeout_ms = PROFILE_BASH_TIMEOUT_SEC[profile] * 1000
    keep = managed_bench_preset_root(home)
    layer_path = " → ".join(
        bundle_basename(name) for name in desired_bundles([], with_team=with_team)
    )
    lines = [
        "# AstroAI Studio — the `astroai` profile's own patch layer.",
        "#",
        "# Generated by `astroai studio --prepare`; hand edits are kept only until",
        "# the next --prepare regenerates this file. Edit it, then move the change",
        "# into astroai_lab/studio_profile.py so it survives.",
        "#",
        "# Applied after the profile's bundle layers, in order:",
        f"#   {layer_path} → this file → the repo's .dsh/cordis.patch.yml (--patch)",
        "#",
        f"# State root: {state.path}" + (" (durable)" if state.durable else " (this session)"),
    ]
    if state.note:
        lines.append(f"# WARNING: {state.note}")
    lines += [
        "",
        "# ── agent preset roster ────────────────────────────────────────────────",
        "# The row is inserted by dsh-web-app; the shipped standard/minimal/cordis/ptc",
        "# presets stay (includeShippedRoot) and `$DSH_HOME/.agent-presets` still wins",
        "# for user-authored copies (includeUserRoot). Studio Team lives under the",
        "# managed review-bench preset root (ensure_review_bench installs it).",
        "- id: agent-presets",
        "  config:",
        "    default: standard",
        "    roots:",
        f"      - path: {_yaml_scalar(str(keep))}",
        "        trust: system",
        "",
        "# ── session storage ────────────────────────────────────────────────────",
        "# Session logs and the full-text index are append-heavy and unbounded, so",
        "# they follow the state root instead of the quota-constrained $HOME.",
        "- id: session-persistence-jsonl",
        "  config:",
        f"    root: {_yaml_scalar(str(state.path / 'sessions'))}",
        "",
        "- id: session-query-sqlite",
        "  config:",
        f"    path: {_yaml_scalar(str(state.path / 'sessions.sqlite'))}",
        "    openAt: first-search",
        "",
        "# Oversized tool results spill to the state root, not to a temp dir that",
        "# vanishes mid-session; retention stays explicit so scratch cannot fill up.",
        "- id: spill-local",
        "  config:",
        f"    root: {_yaml_scalar(str(state.path / 'spill'))}",
        f"    cleanupPeriodDays: {SPILL_CLEANUP_DAYS}",
        "",
        "# ── shell ──────────────────────────────────────────────────────────────",
        f"# {profile}: {PROFILE_BASH_TIMEOUT_SEC[profile]}s of headroom for pixi solves,",
        "# native rebuilds and bench runs (the shipped default is 60s).",
        "- id: bash-sandbox",
        "  config:",
        f"    timeoutMs: {timeout_ms}",
        "",
        "# ── tools ──────────────────────────────────────────────────────────────",
        mcp_row_yaml(command),
        "",
    ]
    return "\n".join(lines)


def bundle_basename(name: str) -> str:
    """``@deepseek-ai/dsh-web-app`` → ``web-app`` (for comments and reports)."""
    return name.rsplit("/", 1)[-1]


def manifest_document(
    bundles: Iterable[str],
    *,
    profile_name: str = STUDIO_PROFILE_NAME,
    base: dict[str, Any] | None = None,
) -> dict:
    """The profile manifest: our bundle list over whatever dsh and pnpm wrote.

    ``dsh plugin`` records installed packs in ``dependencies`` and rewrites
    ``dsh.profile.bundles`` itself, so this **merges** rather than replaces —
    regenerating the manifest from scratch would silently drop the dependency
    pins that keep a bundle resolvable. On a fresh profile it produces exactly
    what dsh's own ``initializeProfileFromDefault`` writes.
    """
    doc: dict[str, Any] = dict(base or {})
    doc.setdefault("name", f"dsh-profile-{profile_name}")
    doc.setdefault("private", True)
    if not isinstance(doc.get("dependencies"), dict):
        doc["dependencies"] = {}
    section = doc.get("dsh")
    section = dict(section) if isinstance(section, dict) else {}
    profile = section.get("profile")
    profile = dict(profile) if isinstance(profile, dict) else {}
    profile["bundles"] = list(bundles)
    profile.setdefault("patchReload", "live")
    section["profile"] = profile
    doc["dsh"] = section
    return doc


def read_manifest(path: Path) -> dict[str, Any]:
    """Parse an existing profile manifest; ``{}`` when absent or unreadable."""
    text = _read_text(path)
    if text is None:
        return {}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return doc if isinstance(doc, dict) else {}


def workspace_document() -> str:
    """``pnpm-workspace.yaml`` for the profile dir (hoisted, as dsh expects)."""
    return "nodeLinker: hoisted\n"


def read_bundles(path: Path) -> list[str]:
    """Best-effort read of ``dsh.profile.bundles``; ``[]`` when unreadable."""
    text = _read_text(path / PROFILE_MANIFEST_FILENAME)
    if text is None:
        return []
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return []
    bundles = ((doc.get("dsh") or {}).get("profile") or {}).get("bundles")
    if not isinstance(bundles, list):
        return []
    return [str(item) for item in bundles]


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def plan_studio_profile(
    home: Path,
    *,
    profile: StudioProfile,
    with_team: bool = True,
    scratch: Path | None = None,
    env: dict[str, str] | None = None,
    astroai_bin: str | None = None,
) -> StudioPlan:
    """Compute the whole Studio profile without touching disk.

    An existing profile keeps its bundle list (installed, possibly user-edited)
    and only gains the Team layers and the required order; a fresh one starts
    from the shipped ``web`` template's list.
    """
    directory = profile_dir(home)
    existing = read_bundles(directory)
    fresh = not existing
    bundles = desired_bundles(existing, with_team=with_team)

    state = resolve_state_root(home, profile=profile, scratch=scratch, env=env)
    command = mcp_serve_command(astroai_bin=astroai_bin, home=home)
    layer = studio_layer_yaml(
        home, profile=profile, state=state, command=command, with_team=with_team
    )

    notes: list[str] = []
    if state.note:
        notes.append(state.note)
    if not with_team:
        notes.append(
            "Team layers are off (--no-team): the roster, durable mailbox and "
            "shared task board stay unavailable; the Studio Team preset still works."
        )
    uninstalled = uninstalled_bundles(directory, bundles)

    files = (
        directory / PROFILE_MANIFEST_FILENAME,
        directory / PROFILE_PATCH_FILENAME,
        directory / PROFILE_WORKSPACE_FILENAME,
    )
    commands: list[tuple[str, ...]] = []
    if uninstalled:
        # Only `dsh plugin add` can install a bundle, and only pnpm knows how.
        commands.append(("dsh", "plugin", "--profile", STUDIO_PROFILE_NAME, "add", *uninstalled))
    return StudioPlan(
        name=STUDIO_PROFILE_NAME,
        dir=directory,
        profile=profile,
        state=state,
        with_team=with_team,
        bundles=tuple(bundles),
        manifest=manifest_document(
            bundles, base=read_manifest(directory / PROFILE_MANIFEST_FILENAME)
        ),
        layer_yaml=layer,
        workspace_yaml=workspace_document(),
        mcp_command=command,
        files=files,
        commands=tuple(commands),
        notes=tuple(notes),
        fresh_profile=fresh,
    )


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


def apply_studio_profile(
    plan: StudioPlan,
    *,
    dry_run: bool = False,
    install_bundles: bool = True,
    dsh_bin: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write the profile files, then install any declared bundle that is absent.

    Returns ``{actions, degraded, bundles}`` where ``bundles`` is the list the
    profile ends up with. A failed install must not leave a profile that cannot
    boot: the Team layers are dropped, the manifest is rewritten without them,
    and the caller is told — a silent half-install would break every launch.
    """
    actions: list[str] = []
    degraded = False
    if not dry_run:
        plan.dir.mkdir(parents=True, exist_ok=True)

    # OpenCode Go needs x-opencode-session on every chat turn. Install the
    # vendored plugin even when `--no-install` skips pnpm (CANFAR Connect path).
    opencode_action = ensure_opencode_session_plugin(plan.dir, dry_run=dry_run)
    if opencode_action:
        actions.append(opencode_action)

    # `pnpm-workspace.yaml` is create-only: a user may have added an
    # `allowBuilds` entry there to permit a git-hosted plugin's build, and
    # rewriting the file would silently break their installs.
    workspace = plan.dir / PROFILE_WORKSPACE_FILENAME
    if not workspace.exists() and not dry_run:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        workspace.write_text(plan.workspace_yaml, encoding="utf-8")
        actions.append(f"wrote {workspace}")

    # The patch layer is guarded: a profile's `cordis.patch.yml` is also where a
    # person writes their own layer, so a file that is not ours is left alone.
    # The manifest is never guarded — it is machine-written, and its bundle list
    # is exactly what a later install has to repair.
    layer_path = plan.dir / PROFILE_PATCH_FILENAME
    if _is_foreign(layer_path) and not force:
        actions.append(
            f"kept {layer_path} (not generated by Studio — re-run with `--force` to replace)"
        )
    elif _write_if_changed(layer_path, plan.layer_yaml, dry_run=dry_run):
        actions.append(f"wrote {layer_path}")

    manifest_path = plan.dir / PROFILE_MANIFEST_FILENAME
    if _write_if_changed(
        manifest_path, json.dumps(plan.manifest, indent=2) + "\n", dry_run=dry_run
    ):
        actions.append(f"wrote {manifest_path}")

    if plan.fresh_profile:
        actions.append(f"created profile {plan.name} from the shipped `web` template")

    if plan.commands:
        if install_bundles:
            degraded = not _install_bundles(plan, actions, dry_run=dry_run, dsh_bin=dsh_bin)
        elif not dry_run:
            # `--no-install` means "do not fetch", never "leave a profile that
            # cannot boot": an image-baked setup resolves every bundle already.
            unresolved = uninstalled_bundles(plan.dir, plan.bundles)
            if unresolved:
                degraded = True
                actions.append(
                    "declared but not installed: "
                    + ", ".join(unresolved)
                    + " — install them, or re-run without `--no-install`"
                )
        if degraded:
            _drop_team_layers(plan, actions, dry_run=dry_run)

    for note in plan.notes:
        actions.append(f"note: {note}")
    # Every declared bundle resolves unless the install failed and the Team
    # layers were dropped from the manifest.
    effective = (
        [name for name in plan.bundles if name not in TEAM_BUNDLES]
        if degraded
        else list(plan.bundles)
    )
    return {"actions": actions, "degraded": degraded, "bundles": effective}


def _install_bundles(
    plan: StudioPlan,
    actions: list[str],
    *,
    dry_run: bool,
    dsh_bin: str | None,
) -> bool:
    """Install the missing bundles; True when every one now resolves."""
    if dry_run:
        actions.append("would run: " + " ".join(plan.commands[0]))
        return True
    dsh = dsh_bin
    if dsh is None:
        from astroai_lab import studio as studio_mod

        dsh = studio_mod.dsh_binary()
    if dsh is None:
        actions.append(
            "TEAM LAYERS UNAVAILABLE: no `dsh` executable — run "
            f"`{DSH_INSTALL_HINT}` and then `astroai studio --prepare` again"
        )
        return False
    missing = uninstalled_bundles(plan.dir, list(plan.bundles))

    # `dsh plugin` forwards to pnpm inside the profile directory and reconciles
    # dsh.profile.bundles itself. pnpm is not optional: it is also what keeps the
    # profile's node_modules from shadowing the installation's own in-box
    # bundles, so a plain `npm install` in the profile dir is *not* an
    # equivalent fallback (it pulls a version-skewed public copy of dsh-base).
    launched = " ".join(("dsh", *plan.commands[0][1:]))
    if shutil.which("pnpm") is None:
        actions.append(
            "TEAM LAYERS UNAVAILABLE: no `pnpm` on PATH (`dsh plugin` needs it). "
            "Enable it with `corepack enable pnpm` and then re-run `astroai studio --prepare`"
        )
        return False
    try:
        run_cmd([dsh, *plan.commands[0][1:]], capture=True, timeout=1800)
    except LabError as exc:
        actions.append(f"TEAM LAYERS UNAVAILABLE: {exc}\n    retry with: {launched}")
        return False
    unresolved = [name for name in missing if not bundle_installed(plan.dir, name)]
    if unresolved:
        actions.append(
            "TEAM LAYERS UNAVAILABLE: still unresolved after install: " + ", ".join(unresolved)
        )
        return False
    actions.append("installed bundles via pnpm: " + ", ".join(missing))
    return True


def _drop_team_layers(plan: StudioPlan, actions: list[str], *, dry_run: bool) -> None:
    """Rewrite the manifest without the Team bundles so the profile still boots.

    Their ``dependencies`` entries go too: dsh reconciles the bundle list from
    installed dependencies, so a leftover pin would silently re-add a pack that
    just failed to install.
    """
    path = plan.dir / PROFILE_MANIFEST_FILENAME
    current = read_manifest(path) or plan.manifest
    bundles = [
        name
        for name in (current.get("dsh", {}).get("profile", {}).get("bundles") or plan.bundles)
        if name not in TEAM_BUNDLES
    ]
    doc = manifest_document(bundles, base=current)
    dependencies = dict(doc.get("dependencies") or {})
    for name in TEAM_BUNDLES:
        dependencies.pop(name, None)
    doc["dependencies"] = dependencies
    if _write_if_changed(path, json.dumps(doc, indent=2) + "\n", dry_run=dry_run):
        actions.append("team bundles removed from dsh.profile.bundles (profile still boots)")


def _is_foreign(path: Path) -> bool:
    """True when a file exists but was not generated by us."""
    text = _read_text(path)
    return text is not None and text.strip() != "" and LAYER_MARK not in text


def _write_if_changed(path: Path, body: str, *, dry_run: bool) -> bool:
    if _read_text(path) == body:
        return False
    if dry_run:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


@dataclass
class Check:
    """One doctor probe: what was checked, what was seen, and what to do."""

    name: str
    ok: bool
    detail: str
    hint: str | None = None
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "hint": self.hint,
            "fatal": self.fatal,
        }


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def dsh_version(dsh_bin: str) -> str | None:
    """``dsh --version``, or None when the binary does not answer."""
    try:
        out = run_capture([dsh_bin, "--version"], timeout=60)
    except LabError:
        return None
    lines = (out or "").strip().splitlines()
    return lines[0] if lines else None


def probe_env() -> dict[str, str]:
    """The environment a Studio session gives the MCP row.

    ``dsh`` merges the row's declared ``env`` over a *scrubbed* ambient one, so a
    probe that inherits this shell's ``PYTHONPATH`` (or ``VIRTUAL_ENV``) would
    answer with the working tree's CLI while the session — which has neither —
    gets whatever the baked path resolves to. That difference is the whole point
    of the check, so the probe scrubs to the same handful of variables.
    """
    keep = ("PATH", "HOME", "USER", "SHELL", "SCRATCH", "PROJECT", "TMPDIR")
    return {name: os.environ[name] for name in keep if os.environ.get(name)}


@dataclass(frozen=True, slots=True)
class McpProbe:
    """What one ``astroai mcp serve`` handshake told us about the other end."""

    ok: bool
    server: str = ""
    tools: tuple[str, ...] = ()
    error: str = ""

    @property
    def missing_canfar_tools(self) -> tuple[str, ...]:
        return tuple(name for name in CANFAR_MCP_TOOLS if name not in self.tools)

    def detail(self) -> str:
        if not self.ok:
            return self.error or "no reply"
        text = self.server
        if self.tools:
            text += f" · {len(self.tools)} tool(s)"
            missing = self.missing_canfar_tools
            if missing:
                text += f", missing {', '.join(missing)}"
        return text


def probe_mcp_report(
    command: tuple[str, ...], *, timeout: float = 20.0, env: dict[str, str] | None = None
) -> McpProbe:
    """Handshake ``initialize`` then ``tools/list`` against ``command`` over stdio.

    Reporting the tool inventory is the point: a server that answers but exposes
    only the cluster half of the toolkit is the failure worth catching, so the
    result carries the names rather than a summary string.
    """
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "astroai-studio-doctor", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    try:
        proc = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=probe_env() if env is None else env,
        )
    except OSError as exc:
        return McpProbe(ok=False, error=f"cannot start {command[0]}: {exc}")
    payload = "".join(json.dumps(request) + "\n" for request in requests)
    try:
        stdout, stderr = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return McpProbe(ok=False, error=f"no reply within {timeout:.0f}s")
    info: dict[str, object] = {}
    tools: list[str] = []
    for line in (stdout or "").splitlines():
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = reply.get("result") or {}
        if not info and result.get("serverInfo"):
            info = result["serverInfo"]
        for tool in result.get("tools") or []:
            if tool.get("name"):
                tools.append(str(tool["name"]))
    if info:
        return McpProbe(
            ok=True,
            server=f"{info.get('name')} {info.get('version')}",
            tools=tuple(tools),
        )
    detail_lines = (stderr or "").strip().splitlines()
    error = detail_lines[-1] if detail_lines else "no initialize reply"
    return McpProbe(ok=False, error=error)


def probe_mcp(
    command: tuple[str, ...], *, timeout: float = 20.0, env: dict[str, str] | None = None
) -> tuple[bool, str]:
    """``probe_mcp_report`` as ``(ok, one-line detail)`` for callers that only log."""
    report = probe_mcp_report(command, timeout=timeout, env=env)
    return report.ok, report.detail()


def _skill_count(root: Path) -> int:
    if not root.is_dir():
        return 0
    found = list(root.glob("*/SKILL.md")) + list(root.glob("*.md"))
    return len(found)


def dump_config(
    dsh_bin: str,
    profile: str = STUDIO_PROFILE_NAME,
    *,
    timeout: float = 180.0,
) -> tuple[int, str, str]:
    """``dsh --profile <profile> --dump-config``; returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            [dsh_bin, "--profile", profile, "--dump-config"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def doctor(
    home: Path,
    *,
    profile: StudioProfile,
    port: int = 3080,
    state: StateRoot | None = None,
    dsh_bin: str | None = None,
    probe_handshake: bool = True,
    with_team: bool = True,
) -> dict[str, Any]:
    """Pre-flight every dependency ``astroai studio`` has, without booting it.

    Read-only apart from creating the state root. Returns
    ``{checks, ok, fatal, profile, state_root, dsh}``.
    """
    from astroai_lab import studio as studio_mod

    checks: list[Check] = []
    # Same resolution as launch: ASTROAI_STUDIO_DSH, PATH, then image search paths.
    resolved_dsh = dsh_bin or studio_mod.dsh_binary()
    pinned = studio_mod.DSH_VERSION

    if resolved_dsh is None:
        checks.append(
            Check(
                name="dsh",
                ok=False,
                detail="no `dsh` executable found",
                hint=f"{DSH_INSTALL_HINT}  (never `npx -y @deepseek-ai/dsh`: npm swallows "
                "launcher flags)",
                fatal=True,
            )
        )
    else:
        version = dsh_version(resolved_dsh)
        pin_ok = version is not None and pinned in version
        checks.append(
            Check(
                name="dsh",
                ok=version is not None,
                detail=f"{resolved_dsh} {version or '(no --version reply)'}",
                hint=None if version else "the binary exists but does not answer --version",
                fatal=version is None,
            )
        )
        if version is not None:
            checks.append(
                Check(
                    name="dsh-pin",
                    ok=pin_ok,
                    detail=f"want {pinned}" + ("" if pin_ok else f", got {version}"),
                    hint=None
                    if pin_ok
                    else f"{DSH_INSTALL_HINT}  (Studio pin; keep containers Dockerfile in sync)",
                )
            )

    directory = profile_dir(home)
    bundles = read_bundles(directory)
    if not directory.is_dir():
        checks.append(
            Check(
                name="profile",
                ok=False,
                detail=f"{directory} does not exist",
                hint="run `astroai studio --prepare`",
                fatal=True,
            )
        )
    else:
        ordered = desired_bundles(bundles, with_team=with_team)
        checks.append(
            Check(
                name="profile",
                ok=True,
                detail=f"{len(bundles)} bundle layers: "
                + " → ".join(bundle_basename(b) for b in bundles),
            )
        )
        checks.append(
            Check(
                name="profile-layer-order",
                ok=bundles == ordered,
                detail="bundle order matches base → web-app → team host → team web"
                if bundles == ordered
                else f"expected {[bundle_basename(b) for b in ordered]}",
                hint="`astroai studio --prepare` rewrites dsh.profile.bundles in order",
            )
        )
        missing = team_bundles_missing(bundles, with_team=with_team)
        declared = [name for name in TEAM_BUNDLES if name in bundles]
        installed = all(bundle_installed(directory, name) for name in declared)
        if not with_team:
            checks.append(
                Check(
                    name="team-layers",
                    ok=True,
                    detail="disabled by `--no-team` (stock web composition)",
                )
            )
        else:
            checks.append(
                Check(
                    name="team-layers",
                    ok=not missing and installed,
                    detail="Agent Teams host + web layers mounted"
                    if not missing and installed
                    else ("missing from the manifest: " + ", ".join(missing))
                    if missing
                    else "declared but not installed (pnpm has not fetched them)",
                    hint="`dsh plugin --profile astroai add "
                    + " ".join(TEAM_BUNDLES)
                    + "` (needs network + pnpm), or `--no-team`",
                )
            )

    patch = directory / PROFILE_PATCH_FILENAME
    checks.append(
        Check(
            name="layer-content",
            ok=patch.is_file(),
            detail=str(patch) if patch.is_file() else "no patch layer written",
            hint="run `astroai studio --prepare`",
        )
    )

    if resolved_dsh is not None and directory.is_dir():
        rc, _out, err = dump_config(resolved_dsh)
        unmatched = [line for line in err.splitlines() if "match" in line.lower()]
        checks.append(
            Check(
                name="composition",
                ok=rc == 0,
                detail="`--dump-config` composed the tree" if rc == 0 else _first_error_line(err),
                hint=None
                if rc == 0
                else (
                    "a declared bundle is not installed (`dsh plugin --profile astroai add …`), "
                    "or a patch row targets a row this dsh no longer ships"
                ),
                fatal=rc != 0,
            )
        )
        if rc == 0 and unmatched:
            checks.append(
                Check(
                    name="patch-targets",
                    ok=False,
                    detail=unmatched[0],
                    hint="a patch row named a row the composition does not have",
                )
            )

    state = state or resolve_state_root(home, profile=profile)
    writable = False
    try:
        state.path.mkdir(parents=True, exist_ok=True)
        writable = os.access(state.path, os.W_OK)
    except OSError:
        writable = False
    checks.append(
        Check(
            name="state-root",
            ok=writable,
            detail=f"{state.path} ({'durable' if state.durable else 'this session only'})",
            hint=state.note,
        )
    )

    checks.append(
        Check(
            name="port",
            ok=_port_free(port),
            detail=f"127.0.0.1:{port} " + ("free" if _port_free(port) else "already in use"),
            hint="pass `--port <n>`; dsh refuses to bind all interfaces by design",
        )
    )

    # Probe what a Studio session will actually run — the command baked into the
    # profile's layer — not whatever `astroai` happens to be on PATH right now.
    command = baked_mcp_command(patch) or mcp_serve_command(home=home)
    if probe_handshake:
        report = probe_mcp_report(command)
        missing = report.missing_canfar_tools
        checks.append(
            Check(
                name="mcp",
                ok=report.ok and not missing,
                detail=f"{' '.join(command)} → {report.detail()}",
                hint=(
                    "job and cluster tools stay unavailable until `astroai mcp serve` answers"
                    if not report.ok
                    else "that CLI predates these tools: upgrade it, or point the row at "
                    "a newer one with `astroai studio --prepare --mcp-bin <path>`"
                ),
            )
        )
    else:
        checks.append(Check(name="mcp", ok=True, detail="skipped (probe_handshake=False)"))

    bench_skills = managed_bench_dir(home) / "skills"
    agents_skills = agents_skills_dir(home)
    skills = _skill_count(bench_skills) + _skill_count(agents_skills)
    checks.append(
        Check(
            name="skills",
            ok=skills > 0,
            detail=(
                f"{_skill_count(bench_skills)} managed + "
                f"{_skill_count(agents_skills)} user skill(s)"
            ),
            hint="`npx skills add astroai/canfar-skills` installs the CANFAR platform skills",
        )
    )

    routes = _settings_routes(home)
    available_keys = _available_keys(home)
    checks.append(
        Check(
            name="providers",
            ok=bool(routes) or not available_keys,
            detail=", ".join(routes)
            if routes
            else (
                "API keys present but no provider ref in settings.yaml"
                if available_keys
                else "no API key found yet (models chosen in dsh Settings)"
            ),
            hint="`astroai studio --prepare` seeds credential refs from support.yaml; "
            "choose provider/model in Settings → Models",
        )
    )
    unserviceable = _unserviceable_routes()
    if unserviceable:
        checks.append(
            Check(
                name="provider-routes",
                ok=False,
                detail="dsh cannot load: " + ", ".join(unserviceable),
                hint="a hand-declared route needs `api`, `base_url`, and `models` in "
                "support.yaml, or add it once in Settings → Add a custom provider",
            )
        )

    if profile == "canfar":
        checks.append(
            Check(
                name="scratch",
                ok=not state.note,
                detail="Studio state is on the session scratch" if not state.note else state.note,
                hint="/scratch dies with the session — export /arc-bound sessions before shutdown",
            )
        )
    else:
        checks.append(Check(name="resources", ok=True, detail=studio_mod.profile_note("laptop")))

    fatal = any(c.fatal and not c.ok for c in checks)
    return {
        "checks": [c.to_dict() for c in checks],
        "ok": all(c.ok for c in checks),
        "fatal": fatal,
        "profile": profile,
        "state_root": str(state.path),
        "state_durable": state.durable,
        "dsh": resolved_dsh,
    }


def baked_mcp_command(patch: Path) -> tuple[str, ...] | None:
    """The ``astroai mcp serve`` argv written into a generated layer, if any.

    The layer is ours and has a fixed shape, so this reads the two scalar lines
    of the ``mcp-astroai`` row rather than parsing a document that carries
    ``!!js`` tags the standard YAML loader rejects.
    """
    text = _read_text(patch)
    if text is None:
        return None
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "serverName: astroai")
    except StopIteration:
        return None
    command: str | None = None
    args: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("command:"):
            command = stripped.split("command:", 1)[1].strip().strip("'\"")
        elif stripped.startswith("args:"):
            inner = stripped.split("args:", 1)[1].strip().strip("[]")
            args = [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
            break
    if not command:
        return None
    return (command, *args)


def _first_error_line(stderr: str) -> str:
    """The most informative line of a dsh failure (its last line is often 'Node.js vX')."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for needle in ("Error", "Cannot find", "not found", "unknown bundle", "ENOENT"):
        for line in lines:
            if needle in line:
                return line[:400]
    return lines[0][:400] if lines else "(no stderr)"


def _available_keys(home: Path) -> list[str]:
    """Provider keys Studio can see (env, shared .env, agent auth files)."""
    try:
        from astroai_lab.agent.review_bench import discover_dsh_keys

        return sorted(discover_dsh_keys(home))
    except Exception:  # noqa: BLE001 — the doctor must never fail on a probe
        return []


def _unserviceable_routes() -> list[str]:
    """Router keys in support.yaml that dsh would refuse as written."""
    try:
        from astroai_lab.agent.review_bench import unserviceable_keys

        return sorted(unserviceable_keys())
    except Exception:  # noqa: BLE001 — the doctor must never fail on a probe
        return []


def _settings_routes(home: Path) -> list[str]:
    """``llm-pi-ai`` route ids in ``$DSH_HOME/settings.yaml`` (best effort)."""
    import yaml

    path = dsh_home(home) / "settings.yaml"
    text = _read_text(path)
    if text is None:
        return []
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    providers = (doc.get("llm-pi-ai") or {}).get("providers") or {}
    return sorted(str(name) for name in providers) if isinstance(providers, dict) else []


__all__ = [
    "AGENTS_SKILLS_REL",
    "BUNDLE_ORDER",
    "Check",
    "DSH_INSTALL_HINT",
    "MANAGED_BENCH_REL",
    "MANAGED_STUDIO_REL",
    "MCP_TOOL_TIMEOUT_MS",
    "PROFILE_BASH_TIMEOUT_SEC",
    "PROFILE_MANIFEST_FILENAME",
    "PROFILE_MAX_PARALLEL_CHILDREN",
    "PROFILE_PATCH_FILENAME",
    "PROFILE_WORKSPACE_FILENAME",
    "SPILL_CLEANUP_DAYS",
    "STUDIO_PROFILE_NAME",
    "TEAM_BUNDLES",
    "WEB_TEMPLATE_BUNDLES",
    "StateRoot",
    "StudioPlan",
    "StudioProfile",
    "agents_skills_dir",
    "apply_studio_profile",
    "bundle_basename",
    "desired_bundles",
    "doctor",
    "dsh_home",
    "dsh_version",
    "dump_config",
    "managed_bench_dir",
    "managed_bench_preset_root",
    "managed_studio_dir",
    "managed_studio_preset_root",
    "manifest_document",
    "mcp_row_yaml",
    "mcp_serve_command",
    "plan_studio_profile",
    "probe_mcp",
    "profile_dir",
    "read_bundles",
    "resolve_state_root",
    "studio_layer_yaml",
    "team_bundles_missing",
    "workspace_document",
]
