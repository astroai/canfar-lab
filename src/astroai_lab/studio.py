"""AstroAI Studio: dsh web coding portal (laptop + CANFAR profiles)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Literal

from astroai_lab.errors import LabError

StudioProfile = Literal["laptop", "canfar"]

DSH_NPM = "@deepseek-ai/dsh@0.1.5-rc.2"
DEFAULT_PORT = 3080

# Profile → harness knobs. Written to ~/.dsh/studio-profile.yaml (canfar-session
# skill) and applied to dsh via home-level ~/.dsh/cordis.patch.yml (bash-local
# timeoutMs). max_parallel_children is advisory for the Team skill brief.
PROFILE_DEFAULTS: dict[StudioProfile, dict[str, Any]] = {
    "laptop": {
        "bash_timeout_sec": 600,
        "max_parallel_children": 8,
        "sandbox": "default",
        "note": "Local resources; no Skaha session quota.",
    },
    "canfar": {
        "bash_timeout_sec": 300,
        "max_parallel_children": 4,
        "sandbox": "default",
        "note": (
            "Interactive session CPU/RAM are capped; use AstroAI hub "
            "→ Start batch compute (ray-manager) for heavy/GPU work. "
            "Scratch is per-pod; persist under /arc."
        ),
    },
}

# Marker so prepare can refresh only our bash timeout row without wiping user patches.
_STUDIO_BASH_MARK = "# astroai-studio-bash-timeout"


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


def _studio_bash_patch_body(profile: StudioProfile) -> str:
    timeout_ms = int(PROFILE_DEFAULTS[profile]["bash_timeout_sec"]) * 1000
    # Whole-config replace for bash-sandbox (dsh patches do not deep-merge).
    return (
        f"{_STUDIO_BASH_MARK}\n"
        f"# Written by `astroai studio --prepare` (profile={profile}).\n"
        f"# Targets the shipped web/base `bash-sandbox` row.\n"
        f"- id: bash-sandbox\n"
        f"  config:\n"
        f"    timeoutMs: {timeout_ms}\n"
    )


def apply_studio_bash_timeout(
    home: Path,
    profile: StudioProfile,
    *,
    force: bool = False,
) -> str | None:
    """Apply profile bash timeout via home-level ``~/.dsh/cordis.patch.yml``.

    Returns an action string when the file changed, else None.
    """
    path = home / ".dsh" / "cordis.patch.yml"
    block = _studio_bash_patch_body(profile)
    end_mark = "# /astroai-studio-bash-timeout"
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
        else:
            # Corrupt/partial mark — replace from mark to EOF if force, else skip.
            if not force:
                return None
            new = existing[:start] + stamped
    elif existing.strip():
        # Preserve user patches; append our row.
        new = existing.rstrip() + "\n\n" + stamped
    else:
        new = stamped

    if new == existing:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return f"bash-sandbox timeoutMs → {path}"


def prepare_studio(
    home: Path | None = None,
    *,
    profile: StudioProfile | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Provision review-bench, shared dotenv, dsh settings, and profile file."""
    from astroai_lab.agent import review_bench as rb

    home = home or Path.home()
    profile = profile or detect_profile()
    actions: list[str] = []

    if dry_run:
        actions.append("would ensure review-bench + dsh settings + studio profile + bash timeout")
        return {"ok": True, "profile": profile, "actions": actions, "dry_run": True}

    if rb.ensure_review_bench(home, force=force):
        actions.append("review-bench installed/updated")
    keys = rb.ensure_dsh_dotenv(home)
    if keys:
        actions.append(f"dotenv keys: {', '.join(sorted(keys))}")
    route = rb.ensure_dsh_settings(home)
    if route:
        actions.append(f"dsh settings route={route}")

    profile_path = home / ".dsh" / "studio-profile.yaml"
    body = (
        f"# AstroAI Studio profile — written by `astroai studio --prepare`\n"
        f"profile: {profile}\n"
        f"bash_timeout_sec: {PROFILE_DEFAULTS[profile]['bash_timeout_sec']}\n"
        f"max_parallel_children: {PROFILE_DEFAULTS[profile]['max_parallel_children']}\n"
        f"sandbox: {PROFILE_DEFAULTS[profile]['sandbox']}\n"
        f"note: |\n"
        f"  {PROFILE_DEFAULTS[profile]['note']}\n"
        f"applied:\n"
        f"  bash_timeout: ~/.dsh/cordis.patch.yml → bash-sandbox.timeoutMs\n"
    )
    if force or not profile_path.is_file() or profile_path.read_text(encoding="utf-8") != body:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(body, encoding="utf-8")
        actions.append(f"wrote {profile_path}")

    bash_action = apply_studio_bash_timeout(home, profile, force=force)
    if bash_action:
        actions.append(bash_action)

    return {
        "ok": True,
        "profile": profile,
        "actions": actions,
        "keys_present": sorted(keys),
        "skills_hint": "npx skills add astroai/canfar-skills",
    }


def studio_web_cmd(
    repo: Path,
    *,
    port: int = DEFAULT_PORT,
    profile: StudioProfile | None = None,
) -> list[str]:
    """Build argv for ``dsh --profile web`` (or npx fallback)."""
    patch = repo / ".dsh" / "cordis.patch.yml"
    binary = shutil.which("dsh")
    cmd = [binary, "--profile", "web"] if binary else ["npx", "-y", DSH_NPM, "--profile", "web"]
    if patch.is_file():
        cmd += ["--patch", str(patch)]
    cmd += ["--no-open", "--port", str(port)]
    profile = profile or detect_profile()
    if profile == "canfar":
        host = os.environ.get("ASTROAI_STUDIO_TRUSTED_HOST", "").strip()
        if host:
            cmd += ["--trusted-host", host]
    return cmd


def skills_onboarding_hint() -> str:
    return (
        "Skill packs use agentskills.io SKILL.md via skills.sh:\n"
        "  npx skills add astroai/canfar-skills\n"
        "Studio also loads ~/.astroai/lab/review-bench/skills "
        "(review-panel, canfar-session)."
    )
