"""Reconcile installed agent state with what this lab version ships.

``astroai agent verify --fix`` runs this after the plain repairs.

skills  Legacy cleanup only: skill trees that still carry an
        ``.astroai-managed`` marker (from when AstroAI installed skills)
        are removed. Skills are owned by ``npx skills`` / skills.sh now;
        AstroAI never refreshes or copies SKILL.md trees.

plugins Force-refresh installed mcp / rule plugins so homes match this lab
        version's catalog. CLI ``tool`` plugins are never re-installed here
        (that would re-download binaries on every verify --fix).

paths   MCP server entries whose command points at a file that no longer
        exists are re-pointed at the same binary name found on PATH.
        Entries that cannot be re-resolved are reported, never deleted.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

MARKER = ".astroai-managed"


def is_legacy_managed_skill_dir(path: Path) -> bool:
    """True when a prior AstroAI install left an ``.astroai-managed`` marker."""
    return path.is_dir() and (path / MARKER).exists()


# Back-compat alias for tests / callers that still import the old name.
def is_managed_skill_dir(path: Path) -> bool:
    return is_legacy_managed_skill_dir(path)


def packaged_skill_names(root: Path | None = None) -> set[str]:
    """Empty: AstroAI no longer ships skill trees (use ``npx skills``)."""
    return set()


# ---------------------------------------------------------------------------
# Reconcilers
# ---------------------------------------------------------------------------


def reconcile_skills(home: Path, *, dry_run: bool = False) -> list[dict[str, str]]:
    """Remove skill trees left by old AstroAI skill installs (``.astroai-managed``)."""
    from astroai_lab.agent.agent_targets import AGENT_SKILL_DIRS

    results: list[dict[str, str]] = []
    for agent, rel in sorted(AGENT_SKILL_DIRS.items()):
        skills_dir = home / rel
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if not is_legacy_managed_skill_dir(entry):
                continue
            if dry_run:
                results.append(
                    {
                        "target": f"{agent}:{entry.name}",
                        "status": "would_remove",
                        "detail": f"legacy {MARKER} at {entry}",
                    }
                )
                continue
            shutil.rmtree(entry, ignore_errors=True)
            results.append(
                {
                    "target": f"{agent}:{entry.name}",
                    "status": "removed",
                    "detail": f"legacy {MARKER} cleared",
                }
            )
    return results


def reconcile_plugins(home: Path, *, dry_run: bool = False) -> list[dict[str, str]]:
    """Refresh installed mcp / rule plugins so homes match this version.

    Skips ``kind: tool`` — those install network CLIs; re-running them on
    every ``verify --fix`` is wasteful and surprising.
    """
    from astroai_lab.agent.plugins import (
        load_plugins,
        plugin_installed,
        update_plugin,
    )

    results: list[dict[str, str]] = []
    for plugin in load_plugins():
        kind = plugin.get("kind")
        if kind not in ("mcp", "rule"):
            continue
        pid = str(plugin.get("id") or "")
        if not pid or not plugin_installed(plugin, home):
            continue
        if dry_run:
            results.append(
                {"target": f"plugin:{pid}", "status": "would_refresh", "detail": kind}
            )
            continue
        updated = update_plugin(pid)
        ok = any(r.status in ("installed", "skipped") for r in updated)
        results.append(
            {
                "target": f"plugin:{pid}",
                "status": "refreshed" if ok else "failed",
                "detail": f"{sum(1 for r in updated if r.status == 'installed')} agent(s)",
            }
        )
    return results


def reconcile_mcp_paths(home: Path, *, dry_run: bool = False) -> list[dict[str, str]]:
    """Re-point MCP entries whose command binary vanished; report the rest."""
    from astroai_lab.agent.agent_targets import (
        MCP_TARGETS,
        _read_config,
        _write_config,
    )

    results: list[dict[str, str]] = []
    for agent, target in sorted(MCP_TARGETS.items()):
        path = home / target.relpath
        if not path.is_file():
            continue
        try:
            data = _read_config(path, target.fmt)
        except Exception:  # noqa: BLE001 — syntax errors surface in verify_setup
            continue
        bucket = data.get(target.key)
        if not isinstance(bucket, dict):
            continue
        changed = False
        for server, entry in bucket.items():
            outcome = _fix_entry_command(entry)
            if outcome is None:
                continue
            changed = True
            if isinstance(outcome, tuple):
                old, new = outcome
                results.append(
                    {
                        "target": f"{agent}:{server}",
                        "status": "rewrote-path" if not dry_run else "would_rewrite",
                        "detail": f"{old} -> {new}",
                    }
                )
            else:
                results.append(
                    {
                        "target": f"{agent}:{server}",
                        "status": "unresolved",
                        "detail": f"command {outcome} not found anywhere",
                    }
                )
        if changed and not dry_run:
            _write_config(path, data, target.fmt)
    return results


def _resolve_path(raw: str) -> Path:
    return Path(os.path.expandvars(str(Path(raw).expanduser())))


def _fix_entry_command(entry: Any) -> tuple[str, str] | str | None:
    """Decide what to do with one MCP entry's ``command``.

    Returns ``None`` when the entry is healthy, ``(old, new)`` after
    rewriting the command to a located binary, or the dead command string
    when no replacement can be found (reported, never deleted).
    """
    if not isinstance(entry, dict):
        return None
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    command = command.strip()

    def healthy(raw: str) -> bool:
        expanded = _resolve_path(raw)
        if expanded.is_file():
            return "$" not in raw  # var-based paths get normalized below
        return False

    bare = "$" not in command and not command.startswith(("/", "~"))
    if bare and shutil.which(command):
        return None  # plain name on PATH — healthy
    if healthy(command):
        return None

    base = Path(command).expanduser().name
    found = shutil.which(base)
    if found and _resolve_path(found) != _resolve_path(command):
        entry["command"] = found
        return command, found
    return command


def reconcile_all(
    home: Path | None = None, *, dry_run: bool = False
) -> dict[str, list[dict[str, str]]]:
    """Run every reconciler. Returns {"skills": …, "plugins": …, "paths": …}."""
    home = home or Path.home()
    return {
        "skills": reconcile_skills(home, dry_run=dry_run),
        "plugins": reconcile_plugins(home, dry_run=dry_run),
        "paths": reconcile_mcp_paths(home, dry_run=dry_run),
    }


def drift_issues(home: Path | None = None) -> list[str]:
    """Human-readable drift between installed state and this lab version.

    Read-only (dry-run reconcilers). Used by ``astroai agent verify`` so
    users see *what* ``verify --fix`` would reconcile before running it.
    """
    from astroai_lab.agent.agent_targets import AGENT_SKILL_DIRS

    home = home or Path.home()
    issues: list[str] = []
    for agent, rel in sorted(AGENT_SKILL_DIRS.items()):
        skills_dir = home / rel
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if is_legacy_managed_skill_dir(entry):
                issues.append(
                    f"legacy AstroAI-managed skill {agent}:{entry.name} — "
                    f"remove with verify --fix (skills now via npx skills)"
                )
    for row in reconcile_mcp_paths(home, dry_run=True):
        issues.append(f"stale MCP path {row['target']} — {row['detail']}")
    return issues
