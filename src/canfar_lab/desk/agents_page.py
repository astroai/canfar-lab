"""Local Agents page: shared keys and per-agent config, on this computer.

The CANFAR studio image serves this job at ``/hub/#agents``. The laptop desk
serves the same job at ``/__desk/agents``. Values are written through
:mod:`canfar_lab.agent.keys` and are never returned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from canfar_lab.agent.bundle_path import bundle_root
from canfar_lab.agent.keys import set_key, status, unset_key
from canfar_lab.agent.registry_probe import registry_agent_status
from canfar_lab.agent.registry_store import get_registry_agent
from canfar_lab.agent.setup import run_bundle
from canfar_lab.agent.support import load_support
from canfar_lab.errors import LabError

AGENTS_ASSET = Path(__file__).resolve().parent.parent / "data" / "desk" / "agents.html"


def load_agents_html() -> str:
    return AGENTS_ASSET.read_text(encoding="utf-8")


def snapshot(home: Path) -> dict[str, Any]:
    """Keys (presence only) and the recommended agents on this machine."""
    keys = [
        {
            "key": row["key"],
            "label": row["label"],
            "present": bool(row["present"]),
            "sources": list(row.get("sources") or []),
            "signup_url": row.get("signup_url") or "",
            "used_by": list(row.get("used_by") or []),
            "notes": row.get("notes") or "",
        }
        for row in status(home)
    ]
    agents: list[dict[str, Any]] = []
    for agent_id in load_support().recommended_agents:
        agent = get_registry_agent(agent_id)
        if agent is None:
            continue
        row = registry_agent_status(agent, home)
        agents.append(
            {
                "id": row["id"],
                "name": "AstroAI Assistant" if row["id"] == "dsh" else row["name"],
                "summary": row["summary"],
                "installed": bool(row["binary_ok"]),
                "config_ok": bool(row["config_ok"]),
                "config": row["config"],
                "binary": row["binary"],
                "setup": row["id"] != "dsh",
            }
        )
    return {"ok": True, "keys": keys, "agents": agents}


def change_key(home: Path, name: str, value: str | None) -> dict[str, Any]:
    """Store or clear one shared key. An empty value clears it."""
    if value is None or not str(value).strip():
        result = unset_key(home, name)
    else:
        result = set_key(home, name, str(value))
    return {"ok": True, **result}


def setup_agent(home: Path, agent_id: str) -> dict[str, Any]:
    """Write one agent's home config. Does not install the binary."""
    agent_id = agent_id.strip()
    if agent_id == "dsh":
        return {
            "ok": True,
            "id": agent_id,
            "note": "The assistant is configured by Studio. Pick its model in Settings → Models.",
        }
    if agent_id not in load_support().recommended_agents:
        raise LabError(f"Unknown agent: {agent_id}", hint="Use an agent listed on this page.")
    run_bundle(agent_id, bundle_root(), home, None, force=False, dry_run=False)
    return {"ok": True, "id": agent_id, "note": "Config written in your home directory."}
