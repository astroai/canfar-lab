"""The six Lab agents: status, install/update/remove, setup, keys, restore.

The list lives in ``data/agent/lab-agents.yaml``; install, config and verify
details come from the agent registry (``data/agent/agents/*.yaml``).
Binaries land on ``$SCRATCH`` and die with the session; the list of agents the
user installed is remembered on ``$HOME`` so :func:`restore` can reinstall
them at the next session start. Key values never leave
:mod:`canfar_lab.agent.keys`; everything here reports presence only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from canfar_lab.agent.bundle_path import bundle_root
from canfar_lab.errors import LabError

SPEC_FILE = "lab-agents.yaml"


@dataclass(frozen=True)
class LabAgent:
    id: str
    label: str
    keys: tuple[str, ...]
    login: str
    auth_files: tuple[str, ...] = ()


def _auth_path(raw: str, home: Path) -> Path:
    data = os.environ.get("XDG_DATA_HOME", "").strip() or str(home / ".local" / "share")
    text = raw.replace("{data}", data)
    if text.startswith("~/"):
        return home / text[2:]
    return Path(text)


def signed_in(agent: LabAgent, home: Path) -> bool | None:
    """True when a login file exists, False when none does, None when undetectable."""
    if not agent.auth_files:
        return None
    return any(_auth_path(raw, home).exists() for raw in agent.auth_files)


def lab_agents(root: Path | None = None) -> list[LabAgent]:
    """Load and check the Lab agent list against the registry and key catalog."""
    from canfar_lab.agent.keys import catalog
    from canfar_lab.agent.registry_store import registry_ids

    path = (root or bundle_root()) / SPEC_FILE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise LabError(f"Cannot read Lab agent list {path}: {exc}") from exc
    entries = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise LabError(f"Lab agent list {path} has no agents")
    known_ids = registry_ids(root)
    known_keys = {row["key"] for row in catalog()}
    agents: list[LabAgent] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise LabError(f"Lab agent entry needs an id: {entry!r}")
        agent_id = str(entry["id"])
        if agent_id not in known_ids:
            raise LabError(f"Lab agent {agent_id!r} is not in the agent registry")
        keys = tuple(str(k) for k in entry.get("keys") or ())
        unknown = [k for k in keys if k not in known_keys]
        if unknown:
            raise LabError(f"Lab agent {agent_id!r} lists unknown key(s): {', '.join(unknown)}")
        agents.append(
            LabAgent(
                id=agent_id,
                label=str(entry.get("label") or agent_id),
                keys=keys,
                login=str(entry.get("login") or ""),
                auth_files=tuple(str(p) for p in entry.get("auth_files") or ()),
            )
        )
    if len({a.id for a in agents}) != len(agents):
        raise LabError(f"Lab agent list {path} repeats an id")
    return agents


def get_lab_agent(agent_id: str) -> LabAgent:
    for agent in lab_agents():
        if agent.id == agent_id:
            return agent
    raise LabError(
        f"{agent_id!r} is not a Lab agent.",
        hint="Lab agents: " + ", ".join(a.id for a in lab_agents()),
    )


def _home(home: Path | None) -> Path:
    return home or Path.home()


def _key_rows(home: Path) -> dict[str, dict[str, Any]]:
    from canfar_lab.agent.keys import status

    rows: dict[str, dict[str, Any]] = {}
    for row in status(home):
        # The catalog repeats a key once per router that reads it; keep the first.
        rows.setdefault(row["key"], row)
    return rows


def lab_keys(home: Path | None = None) -> list[dict[str, Any]]:
    """Keys any Lab agent reads: name, label, signup URL, presence, sources."""
    rows = _key_rows(_home(home))
    names = dict.fromkeys(k for agent in lab_agents() for k in agent.keys)
    return [
        {
            "key": name,
            "label": rows[name]["label"],
            "signup_url": rows[name].get("signup_url") or "",
            "present": bool(rows[name]["present"]),
            "sources": list(rows[name].get("sources") or []),
            "used_by": [a.label for a in lab_agents() if name in a.keys],
        }
        for name in names
        if name in rows
    ]


def status(home: Path | None = None, *, probe_version: bool = False) -> dict[str, Any]:
    """Every Lab agent with install/config state, plus the keys they read."""
    from canfar_lab.agent.registry_probe import registry_agent_status
    from canfar_lab.agent.registry_store import get_registry_agent
    from canfar_lab.agent.setup_state import remembered_agents

    home = _home(home)
    keys = _key_rows(home)
    remembered = set(remembered_agents(home))
    rows: list[dict[str, Any]] = []
    for agent in lab_agents():
        reg = get_registry_agent(agent.id)
        assert reg is not None  # lab_agents() checked the registry
        probe = registry_agent_status(reg, home, probe_ver=probe_version)
        agent_keys = [
            {"key": k, "present": bool(keys.get(k, {}).get("present"))} for k in agent.keys
        ]
        login_ok = signed_in(agent, home)
        has_key = any(k["present"] for k in agent_keys)
        rows.append(
            {
                "id": agent.id,
                "label": agent.label,
                "summary": probe["summary"],
                "homepage": reg.get("homepage") or "",
                "installed": bool(probe["binary_ok"]),
                "binary": probe["binary"],
                "binary_path": probe["binary_path"],
                "managed": probe["managed"],
                "home_install": probe["home_install"],
                "config": probe["config"],
                "config_ok": bool(probe["config_ok"]),
                "version": probe["version"],
                "remembered": agent.id in remembered,
                "needs_restore": agent.id in remembered and not probe["binary_ok"],
                "login": agent.login,
                "signed_in": login_ok,
                "keys": agent_keys,
                # Installed and able to call a model: a login file or a key.
                # When the login is undetectable (keyring), trust an install.
                "ready": bool(probe["binary_ok"])
                and (has_key or login_ok is True or login_ok is None),
            }
        )
    return {"ok": True, "agents": rows, "keys": lab_keys(home)}


def install(agent_id: str, *, setup: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Install one Lab agent's CLI, write its MCP/skills/config, remember it."""
    from canfar_lab.agent.registry_lifecycle import (
        install_registry_agent,
        setup_registry_agent,
    )
    from canfar_lab.agent.setup_state import remember_agent

    get_lab_agent(agent_id)
    install_registry_agent(agent_id, dry_run=dry_run)
    actions = [f"install:{agent_id}"]
    errors: list[str] = []
    if setup:
        result = setup_registry_agent(agent_id, dry_run=dry_run)
        actions.extend(str(a) for a in result.get("actions") or [])
        errors.extend(str(e) for e in result.get("errors") or [])
    if not dry_run:
        remember_agent(None, agent_id, installed=True)
    return {"ok": not errors, "agent": agent_id, "actions": actions, "errors": errors}


def update(agent_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Refresh one Lab agent's CLI and re-apply its MCP/skills/config."""
    from canfar_lab.agent.registry_lifecycle import update_registry_agent
    from canfar_lab.agent.setup_state import remember_agent

    get_lab_agent(agent_id)
    result = update_registry_agent(agent_id, dry_run=dry_run)
    if not dry_run and result.get("ok"):
        remember_agent(None, agent_id, installed=True)
    return {
        "ok": bool(result.get("ok")),
        "agent": agent_id,
        "actions": [str(a) for a in result.get("actions") or []],
        "errors": [str(e) for e in result.get("errors") or []],
    }


def remove(agent_id: str, *, purge: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Uninstall one Lab agent's CLI and forget it. Config on $HOME stays unless *purge*."""
    from canfar_lab.agent.registry_lifecycle import remove_registry_agent
    from canfar_lab.agent.setup_state import remember_agent

    get_lab_agent(agent_id)
    results = remove_registry_agent(agent_id, purge=purge, dry_run=dry_run)
    if not dry_run:
        remember_agent(None, agent_id, installed=False)
    errors = [str(r.get("detail")) for r in results if r.get("status") == "error"]
    return {"ok": not errors, "agent": agent_id, "actions": results, "errors": errors}


def setup(agent_id: str, *, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Write CANFAR MCP, skills and config into one Lab agent (no install)."""
    from canfar_lab.agent.registry_lifecycle import setup_registry_agent

    get_lab_agent(agent_id)
    result = setup_registry_agent(agent_id, force=force, dry_run=dry_run)
    errors = [str(e) for e in result.get("errors") or []]
    return {
        "ok": not errors,
        "agent": agent_id,
        "actions": [str(a) for a in result.get("actions") or []],
        "errors": errors,
    }


def pending_restore(home: Path | None = None) -> list[str]:
    """Remembered Lab agents whose CLI is missing from this session."""
    return [row["id"] for row in status(home)["agents"] if row["needs_restore"]]


def restore(home: Path | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """Reinstall every remembered Lab agent missing from this session.

    Configs, MCP and skills persist on ``$HOME``, so only the binary is
    reinstalled. One failure does not stop the others.
    """
    names = pending_restore(home)
    results: list[dict[str, Any]] = []
    for agent_id in names:
        try:
            results.append(install(agent_id, setup=False, dry_run=dry_run))
        except LabError as exc:
            results.append({"ok": False, "agent": agent_id, "actions": [], "errors": [str(exc)]})
    return {
        "ok": all(r["ok"] for r in results),
        "agents": names,
        "results": results,
        "dry_run": dry_run,
    }


def _check_lab_key(name: str) -> None:
    if not any(name in agent.keys for agent in lab_agents()):
        raise LabError(f"{name!r} is not a key any Lab agent reads.")


def set_key(name: str, value: str, home: Path | None = None) -> dict[str, Any]:
    """Store one key (both the shell dotenv and dsh refs). Returns presence only."""
    from canfar_lab.agent import keys

    _check_lab_key(name)
    return {"ok": True, **keys.set_key(_home(home), name, value)}


def unset_key(name: str, home: Path | None = None) -> dict[str, Any]:
    from canfar_lab.agent import keys

    _check_lab_key(name)
    return {"ok": True, **keys.unset_key(_home(home), name)}
