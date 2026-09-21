"""AstroAI Studio: dsh web coding portal (laptop + CANFAR profiles).

Studio is a thin, opinionated launcher around the DeepSeek Harness. It owns a
dsh profile named ``astroai`` (see :mod:`astroai_lab.studio_profile`) instead of
the shipped ``web`` profile, so AstroAI can add the Agent Teams layers, route
session state off a quota-constrained ``/arc`` home, register the managed preset
root and mount the ``astroai mcp serve`` server that gives a chat real CANFAR
job and cluster tools.

Two resource profiles, nothing more:

``laptop``
    Local resources; state under ``$DSH_HOME/state``; dsh serves loopback.
``canfar``
    A Skaha contributed session (`images.canfar.net/astroai/studio`). State on
    the session scratch, dsh on loopback behind the image's public proxy, and
    extra trusted hosts for the ``/api`` fence.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Literal

from astroai_lab import studio_profile as sp
from astroai_lab.errors import LabError

StudioProfile = Literal["laptop", "canfar"]

#: Pinned harness version. Keep in sync with canfar-containers
#: `dockerfiles/studio/Dockerfile` (ARG DSH_VERSION) and
#: `src/astroai_lab/data/agent/agents/dsh.yaml`.
DSH_VERSION = "0.1.5-rc.2"
DSH_NPM = f"@deepseek-ai/dsh@{DSH_VERSION}"

DEFAULT_PORT = 3080

#: Marker so prepare can refresh only our bash timeout row without wiping user patches.
_STUDIO_BASH_MARK = "# astroai-studio-bash-timeout"

#: Places a global npm install can land, in resolution order.
_DSH_SEARCH_PATHS = (
    "/opt/astroai/bin/dsh",
    # Scratch-canonical managed bin (ASTROAI_LAB_BIN_DIR) — prefer over /arc home.
    "${ASTROAI_LAB_BIN_DIR}/dsh",
    "${SCRATCH}/.local/bin/dsh",
    "~/.npm-global/bin/dsh",
    "~/.local/bin/dsh",
    "/usr/local/bin/dsh",
)


def profile_defaults(profile: StudioProfile) -> dict[str, Any]:
    """Resource knobs per profile; ``bash_timeout_sec`` reaches the harness."""
    timeout = sp.PROFILE_BASH_TIMEOUT_SEC[profile]
    children = sp.PROFILE_MAX_PARALLEL_CHILDREN[profile]
    if profile == "canfar":
        note = (
            "Interactive session CPU/RAM are capped; use the AstroAI hub "
            "→ Start batch compute (ray-manager) for heavy/GPU work. "
            "Session state is on /scratch and dies with the session; /arc is "
            "durable. Export anything that must outlive the session."
        )
    else:
        note = "Local resources; no Skaha session quota."
    return {
        "bash_timeout_sec": timeout,
        "max_parallel_children": children,
        "sandbox": "default",
        "note": note,
    }


def profile_note(profile: StudioProfile) -> str:
    return str(profile_defaults(profile)["note"])


def detect_profile() -> StudioProfile:
    # Match panel.py: platform may set lowercase skaha_sessionid.
    names = {key.upper() for key in os.environ}
    if "SKAHA_SESSIONID" in names or "SKAHA_HOSTNAME" in names:
        return "canfar"
    if os.environ.get("ASTROAI_SESSION_KIND", "").strip().lower() == "studio":
        return "canfar"
    return "laptop"


def resolve_repo(repo: str | Path | None = None) -> Path:
    path = Path.cwd() if repo is None or str(repo).strip() in ("", ".") else Path(repo).expanduser()
    if not path.is_dir():
        raise LabError(f"Not a directory: {path}")
    return path.resolve()


def dsh_binary() -> str | None:
    """Resolve a real ``dsh`` executable, never an npx shim.

    ``npx -y @deepseek-ai/dsh …`` is documented as broken: on npm >= 10 the npm
    argument parser swallows launcher flags (``--profile``, ``--patch``,
    ``--dump-config``), so Studio would silently boot the wrong thing.
    """
    override = os.environ.get("ASTROAI_STUDIO_DSH", "").strip()
    if override:
        candidate = Path(override).expanduser()
        return str(candidate) if candidate.is_file() else shutil.which(override)
    found = shutil.which("dsh")
    if found:
        return found
    for raw in _DSH_SEARCH_PATHS:
        candidate = Path(os.path.expandvars(raw)).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def dsh_missing_error() -> LabError:
    return LabError(
        "No `dsh` executable found.",
        hint=(
            f"Install it globally: npm install -g {DSH_NPM}\n"
            "Do not use `npx -y @deepseek-ai/dsh …`: npm swallows the "
            "launcher flags Studio depends on."
        ),
    )


def apply_studio_bash_timeout(
    home: Path,
    profile: StudioProfile,
    *,
    force: bool = False,
) -> str | None:
    """Apply the profile bash timeout via the machine-level ``~/.dsh`` patch.

    The harness's home-level layer is applied over every profile, so this is
    where a machine-wide knob belongs. Returns an action string when the file
    changed, else None.
    """
    path = sp.dsh_home(home) / sp.PROFILE_PATCH_FILENAME
    timeout_ms = int(profile_defaults(profile)["bash_timeout_sec"]) * 1000
    end_mark = "# /astroai-studio-bash-timeout"
    block = (
        f"{_STUDIO_BASH_MARK}\n"
        f"# Written by `astroai studio --prepare` (profile={profile}).\n"
        "# Targets the shipped base `bash-sandbox` row. A patch row replaces the\n"
        "# targeted row's whole config, so this row stays deliberately tiny: the\n"
        "# shipped default is 60s and bash-sandbox carries no other config.\n"
        "- id: bash-sandbox\n"
        "  config:\n"
        f"    timeoutMs: {timeout_ms}\n"
    )
    stamped = f"{block.rstrip()}\n{end_mark}\n"

    existing = ""
    if path.is_file():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""

    if _STUDIO_BASH_MARK in existing:
        # Replace our previous stamped region (inclusive).
        start = existing.find(_STUDIO_BASH_MARK)
        end = existing.find(end_mark)
        if end >= 0:
            end = end + len(end_mark)
            while end < len(existing) and existing[end] == "\n":
                end += 1
            new = existing[:start] + stamped + existing[end:]
        elif force:
            new = existing[:start] + stamped
        else:
            # Corrupt/partial mark — leave the user's file alone.
            return None
    elif existing.strip():
        # Preserve user patches; append our row.
        new = existing.rstrip() + "\n\n" + stamped
    else:
        new = stamped

    if new == existing:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return f"bash-sandbox timeoutMs → {timeout_ms} ({path})"


def prepare_studio(
    home: Path | None = None,
    *,
    profile: StudioProfile | None = None,
    dry_run: bool = False,
    force: bool = False,
    with_team: bool = True,
    install_bundles: bool = True,
    mcp_bin: str | None = None,
) -> dict[str, Any]:
    """Provision the Studio profile, review-bench, dotenv, settings and dsh layers."""
    from astroai_lab.agent import review_bench as rb

    home = home or Path.home()
    profile = profile or detect_profile()
    actions: list[str] = []
    degraded = False

    if dry_run:
        plan = sp.plan_studio_profile(
            home, profile=profile, with_team=with_team, astroai_bin=mcp_bin
        )
        return {
            "ok": True,
            "profile": profile,
            "actions": [
                "would ensure review-bench + dsh dotenv/settings + studio profile",
                "would write " + ", ".join(str(p) for p in plan.files),
                *([f"would run: {' '.join(plan.commands[0])}"] if plan.commands else []),
            ],
            "profile_plan": plan.to_dict(),
            "dry_run": True,
        }

    if rb.ensure_review_bench(home, force=force):
        actions.append("review-bench installed/updated")
    keys = rb.ensure_dsh_dotenv(home)
    if keys:
        actions.append(f"dotenv keys: {', '.join(sorted(keys))}")
    for provider in rb.ensure_dsh_settings(home):
        actions.append(f"dsh provider ref: {provider}")

    if mcp_bin:
        # Pin it in the shared dotenv, or the next `--prepare` in a fresh shell
        # would bake whatever `astroai` happens to be on PATH instead.
        pin = pin_mcp_bin(home, mcp_bin, dry_run=dry_run)
        if pin:
            actions.append(pin)

    profile_path = sp.managed_studio_dir(home) / "studio-profile.yaml"
    body = _studio_profile_document(profile)
    if force or not profile_path.is_file() or profile_path.read_text(encoding="utf-8") != body:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(body, encoding="utf-8")
        actions.append(f"wrote {profile_path}")

    bash_action = apply_studio_bash_timeout(home, profile, force=force)
    if bash_action:
        actions.append(bash_action)

    plan = sp.plan_studio_profile(home, profile=profile, with_team=with_team, astroai_bin=mcp_bin)
    applied = sp.apply_studio_profile(plan, install_bundles=install_bundles, force=force)
    actions.extend(applied["actions"])
    degraded = bool(applied["degraded"])
    actions.append(f"MCP row: {' '.join(plan.mcp_command)}")

    return {
        "ok": True,
        "profile": profile,
        "actions": actions,
        "degraded": degraded,
        "dsh_profile": plan.name,
        "dsh_profile_dir": str(plan.dir),
        "state_root": str(plan.state.path),
        "bundles": list(applied["bundles"]),
        "keys_present": sorted(keys),
        "mcp_command": list(plan.mcp_command),
        "skills_hint": skills_onboarding_hint(),
    }


def pin_mcp_bin(home: Path, mcp_bin: str, *, dry_run: bool = False) -> str | None:
    """Persist ``ASTROAI_STUDIO_MCP_BIN`` so later prepares keep the same CLI.

    Returns a user-facing action line, or ``None`` when the pin was already in
    place. Clearing the key (an empty value in ``~/.astroai/lab/.env``) restores
    the default: whatever ``astroai`` resolves to on ``PATH``.
    """
    from astroai_lab.agent.setup import (
        _read_dotenv_value,
        _write_dotenv_value,
        openrouter_dotenv_path,
    )

    dotenv = openrouter_dotenv_path(home)
    if _read_dotenv_value(dotenv, sp.MCP_BIN_ENV) == mcp_bin:
        return None
    _write_dotenv_value(dotenv, sp.MCP_BIN_ENV, mcp_bin, dry_run=dry_run)
    if dry_run:
        return f"would pin {sp.MCP_BIN_ENV}={mcp_bin}"
    os.environ[sp.MCP_BIN_ENV] = mcp_bin
    return f"pinned {sp.MCP_BIN_ENV}={mcp_bin} ({dotenv})"


def _studio_profile_document(profile: StudioProfile) -> str:
    defaults = profile_defaults(profile)
    return (
        "# AstroAI Studio profile — written by `astroai studio --prepare`\n"
        f"profile: {profile}\n"
        f"bash_timeout_sec: {defaults['bash_timeout_sec']}\n"
        f"max_parallel_children: {defaults['max_parallel_children']}\n"
        f"sandbox: {defaults['sandbox']}\n"
        "note: |\n"
        f"  {defaults['note']}\n"
        "applied:\n"
        f"  dsh_profile: {sp.STUDIO_PROFILE_NAME}\n"
        "  bash_timeout: ~/.dsh/cordis.patch.yml → bash-sandbox.timeoutMs\n"
        "  state: see `astroai studio --doctor`\n"
    )


def trusted_hosts(home: Path | None = None) -> list[str]:
    """Extra ``/api`` fence authorities for this session.

    dsh refuses to bind anything but loopback by design; on CANFAR the image's
    proxy terminates the public URL and forwards the browser ``Host``, so that
    name has to be trusted here.
    """
    hosts: list[str] = []
    for raw in (os.environ.get("ASTROAI_STUDIO_TRUSTED_HOST", ""), _pod_hostname()):
        for item in str(raw or "").replace(",", " ").split():
            if item and item not in hosts:
                hosts.append(item)
    declared = sp.dsh_home(home or Path.home()) / "studio-trusted-hosts"
    if declared.is_file():
        try:
            for line in declared.read_text(encoding="utf-8").splitlines():
                item = line.strip()
                if item and not item.startswith("#") and item not in hosts:
                    hosts.append(item)
        except OSError:
            pass
    return hosts


def _pod_hostname() -> str:
    for name in ("HOSTNAME", "SKAHA_HOSTNAME"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def studio_web_cmd(
    repo: Path,
    *,
    port: int = DEFAULT_PORT,
    profile: StudioProfile | None = None,
    dsh_bin: str | None = None,
    home: Path | None = None,
) -> list[str]:
    """Build argv for ``dsh --profile astroai``.

    ``--patch`` paths are resolved to absolute paths because dsh resolves them
    relative to its own CLI package directory, not the invoking directory.
    """
    resolved_dsh = dsh_bin or dsh_binary()
    if resolved_dsh is None:
        raise dsh_missing_error()
    patch = repo / ".dsh" / sp.PROFILE_PATCH_FILENAME
    cmd = [resolved_dsh, "--profile", sp.STUDIO_PROFILE_NAME]
    if patch.is_file():
        cmd += ["--patch", str(patch.resolve())]
    cmd += ["--no-open", "--port", str(port)]
    resolved_profile = profile or detect_profile()
    if resolved_profile == "canfar":
        for host in trusted_hosts(home):
            cmd += ["--trusted-host", host]
    return cmd


def studio_env(*, profile: StudioProfile | None = None, home: Path | None = None) -> dict[str, str]:
    """Environment overrides for the launched harness.

    Keeps the pnpm content-addressed store and temp files off the
    quota-constrained home; the profile directory keeps its own
    ``node_modules``, which is where the launcher looks for bundles.
    """
    resolved_profile = profile or detect_profile()
    env: dict[str, str] = {"ASTROAI_STUDIO_PROFILE": resolved_profile}
    state = sp.resolve_state_root(home or Path.home(), profile=resolved_profile)
    if not state.durable:
        store = state.path / "pnpm-store"
        env.setdefault("npm_config_store_dir", str(store))
        env.setdefault("PNPM_HOME", str(state.path / "pnpm-home"))
        env.setdefault("TMPDIR", str(state.path / "tmp"))
    env["ASTROAI_STUDIO_STATE"] = str(state.path)
    return env


def skills_onboarding_hint() -> str:
    return (
        "Skill packs use agentskills.io SKILL.md via skills.sh:\n"
        "  npx skills add astroai/canfar-skills\n"
        f"Studio also loads {sp.managed_bench_dir(Path.home()) / 'skills'} "
        "(review-panel, canfar-session, astroai-team-charter)."
    )


__all__ = [
    "DEFAULT_PORT",
    "DSH_NPM",
    "DSH_VERSION",
    "StudioProfile",
    "apply_studio_bash_timeout",
    "detect_profile",
    "dsh_binary",
    "dsh_missing_error",
    "prepare_studio",
    "profile_defaults",
    "profile_note",
    "resolve_repo",
    "skills_onboarding_hint",
    "studio_env",
    "studio_web_cmd",
    "trusted_hosts",
]
