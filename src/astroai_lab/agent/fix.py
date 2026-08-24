"""Auto-fix and repair agent configurations, directories, syntax errors, and state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astroai_lab.agent.clean_agent import clean_agent_state
from astroai_lab.agent.setup_state import failed_path
from astroai_lab.utils.json_utils import read_jsonc, write_json


@dataclass(frozen=True)
class FixResult:
    target: str
    fixed: bool
    detail: str


def fix_agent_setup(*, home: Path | None = None, dry_run: bool = False) -> list[FixResult]:
    """Inspect and auto-repair common agent config issues.

    Fixes broken syntax, missing folders, and stale locks.
    """
    home = home or Path.home()
    results: list[FixResult] = []

    # 1. Clean stale locks & failed markers
    cleans = clean_agent_state(home=home, stale_locks=True, failed_marker=True, dry_run=dry_run)
    for c in cleans:
        results.append(
            FixResult(
                target=c.target, fixed=(c.status in ("removed", "would_remove")), detail=c.detail
            )
        )

    # 2. Ensure directories exist
    dirs_to_create = [
        home / ".cursor" / "skills",
        home / ".cursor" / "rules",
        home / ".config" / "opencode",
        home / ".config" / "kilo",
        home / ".config" / "goose",
        home / ".codex",
        home / ".astroai" / "lab",
    ]
    for d in dirs_to_create:
        if not d.is_dir():
            if dry_run:
                results.append(
                    FixResult(target=d.name, fixed=True, detail=f"Would create directory {d}")
                )
            else:
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    results.append(
                        FixResult(target=d.name, fixed=True, detail=f"Created directory {d}")
                    )
                except OSError as exc:
                    results.append(
                        FixResult(target=d.name, fixed=False, detail=f"Failed creating {d}: {exc}")
                    )

    # 3. Check and repair JSON/JSONC syntax in config files
    json_configs = [
        home / ".cursor" / "mcp.json",
        home / ".config" / "opencode" / "opencode.json",
        home / ".config" / "kilo" / "kilo.jsonc",
        home / ".claude.json",
    ]
    for cfg in json_configs:
        if not cfg.is_file():
            continue
        try:
            parsed = read_jsonc(cfg)
            if not isinstance(parsed, dict):
                raise ValueError("JSON root must be an object")
        except (OSError, ValueError) as exc:
            # File is corrupted - attempt repair or reset
            if dry_run:
                results.append(
                    FixResult(
                        target=cfg.name, fixed=True, detail=f"Would repair syntax in {cfg}: {exc}"
                    )
                )
            else:
                try:
                    # Write valid default JSON
                    default_obj = {"mcpServers": {}} if "mcp" in cfg.name else {}
                    write_json(cfg, default_obj)
                    results.append(
                        FixResult(
                            target=cfg.name, fixed=True, detail=f"Repaired broken JSON in {cfg}"
                        )
                    )
                except OSError as write_err:
                    results.append(
                        FixResult(
                            target=cfg.name,
                            fixed=False,
                            detail=f"Failed repairing {cfg}: {write_err}",
                        )
                    )
            continue

        # 3b. OpenCode semantic sanitize (lsp/formatter booleans → schema objects)
        if cfg.name == "opencode.json":
            from astroai_lab.agent.opencode_config import sanitize_opencode_config

            cleaned, changes = sanitize_opencode_config(parsed)
            if changes:
                if dry_run:
                    results.append(
                        FixResult(
                            target=cfg.name,
                            fixed=True,
                            detail="Would sanitize OpenCode config: " + "; ".join(changes[:4]),
                        )
                    )
                else:
                    try:
                        write_json(cfg, cleaned)
                        results.append(
                            FixResult(
                                target=cfg.name,
                                fixed=True,
                                detail="Sanitized OpenCode config: " + "; ".join(changes[:4]),
                            )
                        )
                    except OSError as write_err:
                        results.append(
                            FixResult(
                                target=cfg.name,
                                fixed=False,
                                detail=f"Failed sanitizing OpenCode config: {write_err}",
                            )
                        )

    # If failed marker exists and no issues remain, unlink it
    fpath = failed_path(home)
    if fpath.is_file() and not dry_run:
        fpath.unlink(missing_ok=True)

    return results


def reconcile_installed_state(
    *, home: Path | None = None, dry_run: bool = False
) -> list[FixResult]:
    """Reconcile skills/plugins/MCP paths with what this lab version ships.

    Used by ``agent verify --fix``. Conservative by design: only content
    astroai itself installed (marker or ``astroai-``/``canfar-`` naming) is
    ever removed; unresolvable MCP entries are reported, never deleted.
    """
    from astroai_lab.agent.reconcile import reconcile_all

    home = home or Path.home()
    results: list[FixResult] = []
    for kind, rows in reconcile_all(home, dry_run=dry_run).items():
        for row in rows:
            results.append(
                FixResult(
                    target=f"{kind}:{row['target']}",
                    fixed=row["status"] not in ("failed", "unresolved"),
                    detail=f"{row['status']}: {row['detail']}",
                )
            )
    return results


def repair_installed_agents(*, home: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Repair shared setup plus every installed registry agent's config.

    Used by ``agent verify --fix``. Also reconciles skills, plugins, and MCP
    command paths against what this lab version ships. Runs under the shared
    agent-setup lock: another session may be mutating the same configs.
    """
    from astroai_lab.agent.setup_state import agent_setup_lock

    home = home or Path.home()
    with agent_setup_lock(home):
        return _repair_installed_agents_locked(home=home, dry_run=dry_run)


def _repair_installed_agents_locked(*, home: Path, dry_run: bool) -> dict[str, Any]:
    from astroai_lab.agent.registry import fix_registry_agent, list_installed_registry_agents
    from astroai_lab.errors import LabError

    setup_results = fix_agent_setup(home=home, dry_run=dry_run)
    reconcile_results = reconcile_installed_state(home=home, dry_run=dry_run)
    actions: list[str] = []
    errors: list[str] = []
    fixed: list[str] = []
    for row in setup_results + reconcile_results:
        if row.fixed:
            actions.append(f"{row.target}: {row.detail}")
        elif row.detail:
            errors.append(f"{row.target}: {row.detail}")
    agents = list_installed_registry_agents(home)
    for agent in agents:
        aid = agent["id"]
        try:
            result = fix_registry_agent(aid, home=home, dry_run=dry_run)
        except LabError as exc:
            errors.append(f"{aid}: {exc}")
            continue
        actions.extend(result["actions"])
        errors.extend(result["errors"])
        # Only count agents that actually changed something (not "healthy" no-ops).
        changed = any(
            any(
                tok in a
                for tok in (
                    "created ",
                    "repaired ",
                    "sanitized ",
                    "would create",
                    "would repair",
                    "would sanitize",
                )
            )
            for a in result["actions"]
        )
        if result["ok"] and changed:
            fixed.append(aid)
    return {
        "ok": not errors,
        "partial": bool(actions) and bool(errors),
        "setup": setup_results,
        "agents": [a["id"] for a in agents],
        "fixed": fixed,
        "actions": actions,
        "errors": errors,
    }
