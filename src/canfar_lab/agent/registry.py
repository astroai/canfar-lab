"""Agent list entries: YAML under ``data/agent/agents/*.yaml``.

Load, probe, and install live in :mod:`canfar_lab.agent.registry_store`,
:mod:`canfar_lab.agent.registry_probe`, and
:mod:`canfar_lab.agent.registry_lifecycle`. This module re-exports them.
"""

from __future__ import annotations

from canfar_lab.agent.registry_lifecycle import (
    _install_curl,
    _install_gh_release,
    _install_npm,
    _install_uv_tool,
    _run_post_install,
    fix_registry_agent,
    install_registry_agent,
    list_installed_registry_agents,
    remove_registry_agent,
    setup_registry_agent,
    update_registry_agent,
)
from canfar_lab.agent.registry_probe import (
    agent_config_file,
    config_state_paths,
    probe_agent_launch,
    probe_agent_version,
    probe_launch,
    probe_version,
    registry_agent_status,
    registry_verify_issues,
    resolve_agent_binary,
    tool_on_path,
)
from canfar_lab.agent.registry_store import (
    INSTALL_METHODS,
    REQUIRED_KEYS,
    get_registry_agent,
    list_registry_agents,
    load_registry,
    registry_ids,
)

__all__ = [
    "INSTALL_METHODS",
    "REQUIRED_KEYS",
    "_install_curl",
    "_install_gh_release",
    "_install_npm",
    "_install_uv_tool",
    "_run_post_install",
    "agent_config_file",
    "config_state_paths",
    "fix_registry_agent",
    "get_registry_agent",
    "install_registry_agent",
    "list_installed_registry_agents",
    "list_registry_agents",
    "load_registry",
    "probe_agent_launch",
    "probe_agent_version",
    "probe_launch",
    "probe_version",
    "registry_agent_status",
    "registry_ids",
    "registry_verify_issues",
    "remove_registry_agent",
    "resolve_agent_binary",
    "setup_registry_agent",
    "tool_on_path",
    "update_registry_agent",
]
