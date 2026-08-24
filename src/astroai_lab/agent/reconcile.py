"""Reconcile installed agent state with what this lab version ships.

``astroai agent verify --fix`` runs this after the plain repairs. Three
conservative passes, each reporting every change:

skills  Skill trees under ``AGENT_SKILL_DIRS`` that this package no longer
        ships are removed; shipped skills are refreshed in place. A tree
        counts as *managed* only when astroai installed it: it carries a
        ``.astroai-managed`` marker or its SKILL.md name uses the lab's
        ``astroai-`` / ``canfar-`` prefixes. Anything else is user content
        and is never touched.

plugins Bundled skill plugins installed for managed agents are refreshed
        through the normal plugin installer, so edits to the packaged
        skill reach already-installed homes.

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
_MANAGED_PREFIXES = ("astroai-", "canfar-")


# ---------------------------------------------------------------------------
# Managed-skill detection
# ---------------------------------------------------------------------------


def _front_matter_name(skill_md: Path) -> str | None:
    """Name from a SKILL.md YAML front matter block (``---`` fenced)."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    return None


def is_managed_skill_dir(path: Path) -> bool:
    """True when astroai installed this skill tree (marker or known prefix)."""
    if not path.is_dir():
        return False
    if (path / MARKER).exists():
        return True
    name = _front_matter_name(path / "SKILL.md")
    return bool(name and name.startswith(_MANAGED_PREFIXES))


def packaged_skill_names(root: Path | None = None) -> set[str]:
    """Skill dir names this lab ships: bundle skills + bundled plugin skills."""
    from pathlib import PurePosixPath

    from astroai_lab.agent.bundle_path import bundle_root
    from astroai_lab.agent.plugins import load_plugins

    root = root or bundle_root()
    names: set[str] = set()
    bundle_skills = root / "cursor" / "skills"
    if bundle_skills.is_dir():
        names.update(p.name for p in bundle_skills.iterdir() if p.is_dir())
    for plugin in load_plugins(root=root):
        install = plugin.get("install") or {}
        # kind=skill plugins install as <id>; transports may use the id or a
        # per-skill basename — collect every name we could ever have written.
        names.add(str(plugin.get("id") or ""))
        for rel in install.get("skills") or []:
            names.add(PurePosixPath(str(rel)).name)
        source = str(install.get("source") or "")
        if source and "/" not in source:
            names.add(source)
    names.discard("")
    return names


# ---------------------------------------------------------------------------
# Reconcilers
# ---------------------------------------------------------------------------


def reconcile_skills(home: Path, *, dry_run: bool = False) -> list[dict[str, str]]:
    """Remove obsolete managed skill trees; refresh shipped ones."""
    from astroai_lab.agent.agent_targets import AGENT_SKILL_DIRS

    canonical = packaged_skill_names()
    results: list[dict[str, str]] = []
    for agent, rel in sorted(AGENT_SKILL_DIRS.items()):
        skills_dir = home / rel
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir() or not is_managed_skill_dir(entry):
                continue
            if entry.name in canonical:
                continue
            if dry_run:
                results.append(
                    {
                        "target": f"{agent}:{entry.name}",
                        "status": "would_remove",
                        "detail": str(entry),
                    }
                )
                continue
            shutil.rmtree(entry, ignore_errors=True)
            results.append(
                {"target": f"{agent}:{entry.name}", "status": "removed", "detail": str(entry)}
            )
    # Refresh shipped bundle skills (cursor is the only bundle shipping skills).
    from astroai_lab.agent.bundle_path import bundle_root
    from astroai_lab.agent.setup import install_skills_tree

    root = bundle_root()
    installed = install_skills_tree(
        root / "cursor" / "skills", home / ".cursor" / "skills", force=True, dry_run=dry_run
    )
    if installed:
        results.append(
            {
                "target": "cursor:bundled-skills",
                "status": "refreshed" if not dry_run else "would_refresh",
                "detail": f"{installed} file(s)",
            }
        )
    return results


def reconcile_plugins(home: Path, *, dry_run: bool = False) -> list[dict[str, str]]:
    """Refresh installed bundled skill plugins so homes match this version."""
    from astroai_lab.agent.plugins import (
        bundled_skill_src,
        load_plugins,
        plugin_installed,
        update_plugin,
    )

    results: list[dict[str, str]] = []
    for plugin in load_plugins():
        if plugin.get("kind") != "skill":
            continue
        pid = str(plugin.get("id") or "")
        if not pid or not plugin_installed(plugin, home):
            continue
        try:
            src = bundled_skill_src(str((plugin.get("install") or {}).get("source")))
        except Exception:  # noqa: BLE001 — a broken plugin must not break verify
            continue
        dst_dirs = _installed_plugin_dirs(plugin, home)
        if not any(_tree_differs(src, dst) for dst in dst_dirs):
            continue
        if dry_run:
            results.append(
                {"target": f"plugin:{pid}", "status": "would_refresh", "detail": "stale"}
            )
            continue
        updated = update_plugin(pid)
        ok = any(r.status == "installed" for r in updated)
        results.append(
            {
                "target": f"plugin:{pid}",
                "status": "refreshed" if ok else "failed",
                "detail": f"{sum(1 for r in updated if r.status == 'installed')} agent(s)",
            }
        )
    return results


def _installed_plugin_dirs(plugin: dict[str, Any], home: Path) -> list[Path]:
    from astroai_lab.agent.plugins import _skill_targets

    return [t for t in _skill_targets(plugin, home).values() if (t / "SKILL.md").is_file()]


def _tree_differs(src: Path, dst: Path) -> bool:
    """True when any packaged file differs from (or is missing in) the copy."""
    if not src.is_dir():
        return False
    for src_file in src.rglob("*"):
        if not src_file.is_file():
            continue
        dst_file = dst / src_file.relative_to(src)
        if not dst_file.is_file():
            return True
        try:
            if src_file.read_bytes() != dst_file.read_bytes():
                return True
        except OSError:
            return True
    return False


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
    canonical = packaged_skill_names()
    for agent, rel in sorted(AGENT_SKILL_DIRS.items()):
        skills_dir = home / rel
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir() and is_managed_skill_dir(entry) and entry.name not in canonical:
                issues.append(f"obsolete skill {agent}:{entry.name} — not in this lab version")
    for row in reconcile_mcp_paths(home, dry_run=True):
        issues.append(f"stale MCP path {row['target']} — {row['detail']}")
    return issues
