"""Load and validate agent registry YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from canfar_lab.agent.bundle_path import bundle_root
from canfar_lab.errors import LabError

INSTALL_METHODS = ("npm", "curl", "gh-release", "uv-tool")
REQUIRED_KEYS = ("id", "name", "homepage", "binary", "install")


def _agents_dir(root: Path | None = None) -> Path:
    return (root or bundle_root()) / "agents"


def _validate(data: dict[str, Any], source: Path) -> dict[str, Any]:
    """Validate + normalize a single registry entry; raise LabError on problems."""
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise LabError(
            f"Agent registry entry {source.name} missing required key(s): {', '.join(missing)}"
        )
    install = data.get("install") or {}
    method = install.get("method")
    if method not in INSTALL_METHODS:
        raise LabError(
            f"Agent {data['id']} has invalid install.method={method!r} "
            f"(expected one of {', '.join(INSTALL_METHODS)}) in {source.name}"
        )
    if method in ("npm", "curl", "uv-tool") and not install.get("source"):
        raise LabError(
            f"Agent {data['id']} install.method={method} requires install.source in {source.name}"
        )
    if method == "gh-release" and not (install.get("repo") and install.get("asset")):
        raise LabError(
            f"Agent {data['id']} install.method=gh-release requires "
            f"install.repo and install.asset in {source.name}"
        )
    if data.get("config") and not data["config"].get("path"):
        raise LabError(f"Agent {data['id']} config requires config.path in {source.name}")
    return data


def load_registry(root: Path | None = None) -> list[dict[str, Any]]:
    """Load + validate every ``agents/*.yaml`` entry, sorted by id."""
    d = _agents_dir(root)
    if not d.is_dir():
        return []
    agents: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise LabError(f"Invalid YAML in agent registry {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise LabError(f"Agent registry entry must be a mapping: {path}")
        agents.append(_validate(raw, path))
    return agents


def list_registry_agents(root: Path | None = None) -> list[dict[str, Any]]:
    return load_registry(root)


def get_registry_agent(agent_id: str, root: Path | None = None) -> dict[str, Any] | None:
    for agent in load_registry(root):
        if agent["id"] == agent_id:
            return agent
    return None


def registry_ids(root: Path | None = None) -> set[str]:
    return {a["id"] for a in load_registry(root)}
