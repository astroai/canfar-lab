"""Plugin registry: MCP / tools / rules across agents.

Skills (SKILL.md trees) are owned by skills.sh / ``npx skills`` — AstroAI
plugins do not install or manage them.

One YAML file per plugin under ``data/agent/plugins/*.yaml`` declares the
support matrix (which agents can host it) and how it is applied. ``agent
plugins install/update/remove/configure`` drive every kind; ``install``
applies to every *installed* agent in the matrix by default and ``--agent``
scopes it.

Example::

    id: ray-manager-mcp
    kind: mcp
    tags: [science, ray, canfar, mcp]
    summary: Ray cluster and job tools
    agents: [mcp-hosts]
    install:
      server: ray-manager
      entry:
        command: astroai
        args: [mcp, serve]

Kinds:
  mcp     merge an ``mcpServers`` entry into each agent's config
  tool    cli-tool transport (install a CLI binary)
  rule    bundled/github-rule transport (Cursor rules)

Plugins with an ``install.type`` (bundled / github-rule / mcp-snippet /
cli-tool) go through ``addons._apply_addon``. ``addon: true`` marks curated
plugins for ``agent plugins list``. Support-matrix aliases: ``mcp-hosts``
(MCP merge agents); ``skill-hosts`` remains as an expand alias for any
legacy matrix entries but plugins no longer copy SKILL.md.

Removal is recursive: dropping an agent removes its plugin-applied files
(see ``remove_agent_plugin_files``). Dynamic URLs only: an mcp ``entry``
must reference env vars (e.g. ``$ASTROAI_RAY_JOBS_ADDRESS``), never a
hardcoded per-session manager URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from astroai_lab.agent.bundle_path import bundle_root
from astroai_lab.errors import LabError

PLUGIN_KINDS = ("mcp", "tool", "rule")
REQUIRED_KEYS = ("id", "kind", "summary", "agents", "install")

# Install transports dispatched through addons._apply_addon.
ADDON_TRANSPORTS = (
    "bundled",
    "github-rule",
    "mcp-snippet",
    "cli-tool",
)

# Pseudo-agent: the global Cursor workspace. Addon transports install into
# ~/.cursor (rules/mcp.json) regardless of any specific agent CLI, so
# `cursor` counts as always installed for installed_only filtering.
CURSOR_AGENT = "cursor"


@dataclass(frozen=True)
class PluginResult:
    plugin: str
    agent: str
    status: str  # installed | removed | skipped | would_install | would_remove | failed | no-op
    detail: str = ""


def _plugins_dir(root: Path | None = None) -> Path:
    return (root or bundle_root()) / "plugins"


def expand_agent_matrix(agents: list[str]) -> list[str]:
    """Expand ``skill-hosts`` / ``mcp-hosts`` aliases; preserve order, drop dupes."""
    from astroai_lab.agent.agent_targets import mcp_hosts, skill_hosts

    aliases = {"skill-hosts": skill_hosts, "mcp-hosts": mcp_hosts}
    out: list[str] = []
    seen: set[str] = set()
    for name in agents:
        expanded = list(aliases[name]()) if name in aliases else [name]
        for item in expanded:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _validate(data: dict[str, Any], source: Path) -> dict[str, Any]:
    """Validate + normalize a single plugin entry; raise LabError on problems."""
    # Presence check uses `is None` so an empty list (agents: []) or empty
    # mapping (install: {}) reaches the kind-specific validation below with a
    # precise message instead of a generic "missing key" error.
    missing = [k for k in REQUIRED_KEYS if data.get(k) is None]
    if missing:
        raise LabError(
            f"Plugin registry entry {source.name} missing required key(s): {', '.join(missing)}"
        )
    kind = data["kind"]
    if kind not in PLUGIN_KINDS:
        raise LabError(
            f"Plugin {data['id']} has invalid kind={kind!r} "
            f"(expected one of {', '.join(PLUGIN_KINDS)}) in {source.name}"
        )
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise LabError(f"Plugin {data['id']} requires a non-empty agents support matrix")
    data["agents"] = expand_agent_matrix([str(a) for a in agents])
    if not data["agents"]:
        raise LabError(f"Plugin {data['id']} support matrix expanded to empty")
    install = data.get("install") or {}
    if not isinstance(install, dict):
        raise LabError(f"Plugin {data['id']} install must be a mapping in {source.name}")
    transport = install.get("type")
    if transport:
        return _validate_transport(data, install, transport, source)
    if kind == "mcp" and not (install.get("server") and install.get("entry")):
        raise LabError(
            f"Plugin {data['id']} kind=mcp requires install.server and install.entry "
            f"in {source.name}"
        )
    if kind in ("tool", "rule"):
        raise LabError(f"Plugin {data['id']} kind={kind} requires install.type in {source.name}")
    return data


def _validate_transport(
    data: dict[str, Any], install: dict[str, Any], transport: str, source: Path
) -> dict[str, Any]:
    """Validate an install transport block."""
    if transport not in ADDON_TRANSPORTS:
        raise LabError(
            f"Plugin {data['id']} has invalid install.type={transport!r} "
            f"(expected one of {', '.join(ADDON_TRANSPORTS)}) in {source.name}"
        )
    if transport == "github-rule" and not (install.get("repo") and install.get("path")):
        raise LabError(
            f"Plugin {data['id']} install.type=github-rule requires repo and path in {source.name}"
        )
    if transport == "mcp-snippet" and not install.get("server"):
        raise LabError(
            f"Plugin {data['id']} install.type=mcp-snippet requires server in {source.name}"
        )
    if transport == "cli-tool" and not install.get("tool"):
        raise LabError(f"Plugin {data['id']} install.type=cli-tool requires tool in {source.name}")
    return data


def load_plugins(root: Path | None = None) -> list[dict[str, Any]]:
    """Load + validate every ``plugins/*.yaml`` entry, sorted by id."""
    d = _plugins_dir(root)
    if not d.is_dir():
        return []
    plugins: list[dict[str, Any]] = []
    for path in d.glob("*.yaml"):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise LabError(f"Invalid YAML in plugin registry {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise LabError(f"Plugin registry entry must be a mapping: {path}")
        plugins.append(_validate(raw, path))
    plugins.sort(key=lambda p: p["id"])
    return plugins


def get_plugin(plugin_id: str, root: Path | None = None) -> dict[str, Any] | None:
    for plugin in load_plugins(root):
        if plugin["id"] == plugin_id:
            return plugin
    return None


def plugin_ids(root: Path | None = None) -> set[str]:
    return {p["id"] for p in load_plugins(root)}


# ---------------------------------------------------------------------------
# Installed status
# ---------------------------------------------------------------------------


def _mcp_present(agent: str, server: str, home: Path) -> bool:
    """True when an mcp server entry is already merged into that agent's config."""
    from astroai_lab.agent.agent_targets import mcp_server_present

    return mcp_server_present(home, agent, server)


def _agent_installed(agent_id: str, home: Path | None = None) -> bool:
    """Is this agent's CLI installed? Registry agents use binary_ok; the rest PATH."""
    from astroai_lab.agent.install import tool_on_path
    from astroai_lab.agent.registry import get_registry_agent, registry_agent_status

    if agent_id == CURSOR_AGENT:
        return True
    home = home or Path.home()
    agent = get_registry_agent(agent_id)
    if agent is not None:
        return registry_agent_status(agent, home)["binary_ok"]
    return tool_on_path(agent_id)


def plugin_installed(plugin: dict[str, Any], home: Path, agent: str | None = None) -> bool:
    """Installed status for one agent (or any agent when ``agent`` is None)."""
    kind = plugin["kind"]
    install = plugin.get("install") or {}
    agents = plugin.get("agents", [])
    if install.get("type"):
        from astroai_lab.agent.addons import addon_installed, plugin_as_addon

        return addon_installed(plugin_as_addon(plugin), home, agent=agent)
    if kind == "mcp":
        server = install["server"]
        if agent:
            return _mcp_present(agent, server, home)
        return any(_mcp_present(a, server, home) for a in agents)
    return False


def plugin_status(plugin: dict[str, Any], home: Path) -> dict[str, Any]:
    """Status row for ``agent plugins list``."""
    by_agent = {agent: plugin_installed(plugin, home, agent) for agent in plugin.get("agents", [])}
    return {
        "id": plugin["id"],
        "kind": plugin["kind"],
        "tags": plugin.get("tags", []),
        "summary": plugin.get("summary", ""),
        "homepage": plugin.get("homepage", ""),
        "default": bool(plugin.get("default")),
        "agents": plugin.get("agents", []),
        "installed": by_agent,
        "any_installed": any(by_agent.values()),
    }


def list_plugins(
    *,
    kind: str | None = None,
    agent: str | None = None,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    home = home or Path.home()
    rows: list[dict[str, Any]] = []
    for plugin in load_plugins():
        if kind and plugin["kind"] != kind:
            continue
        status = plugin_status(plugin, home)
        if agent and not status["installed"].get(agent):
            continue
        rows.append(status)
    return rows


# ---------------------------------------------------------------------------
# Install / update / remove / configure
# ---------------------------------------------------------------------------


def _selected_agents(plugin: dict[str, Any], agent: str | None) -> list[str]:
    """Support-matrix agents to apply to: --agent scopes, else every agent.

    Caller is responsible for the "only installed agents" default (install);
    remove/configure act on the full matrix (or the scoped --agent).
    """
    matrix = list(plugin.get("agents", []))
    if agent:
        if agent not in matrix:
            raise LabError(
                f"Plugin {plugin['id']} does not support agent {agent!r}",
                hint="supported: " + ", ".join(matrix),
            )
        return [agent]
    return matrix


def _configure_mcp(
    plugin: dict[str, Any], agent: str, home: Path, *, force: bool, dry_run: bool
) -> PluginResult:
    from astroai_lab.agent.agent_targets import cursor_to_opencode, mcp_target, merge_mcp_server

    install = plugin["install"]
    server = str(install["server"])
    entry = install["entry"]
    if not isinstance(entry, dict):
        return PluginResult(plugin["id"], agent, "failed", "install.entry must be a mapping")
    target = mcp_target(agent)
    if target is None:
        return PluginResult(plugin["id"], agent, "skipped", f"no MCP config for agent {agent}")
    if dry_run:
        return PluginResult(
            plugin["id"],
            agent,
            "would_install",
            f"merge {target.key}.{server} into {agent} config",
        )
    if not force and _mcp_present(agent, server, home):
        return PluginResult(
            plugin["id"], agent, "skipped", f"already merged ({home / target.relpath})"
        )
    payload = cursor_to_opencode(entry) if agent == "opencode" else entry
    if not payload:
        return PluginResult(plugin["id"], agent, "failed", "empty MCP entry")
    merge_mcp_server(home, agent, server, payload, force=True)
    return PluginResult(
        plugin["id"],
        agent,
        "installed",
        f"{target.key}.{server} -> {home / target.relpath}",
    )


def _apply(
    plugin: dict[str, Any],
    agent: str,
    home: Path,
    *,
    force: bool,
    dry_run: bool,
) -> PluginResult:
    install = plugin.get("install") or {}
    if install.get("type"):
        from astroai_lab.agent.addons import _apply_addon, plugin_as_addon

        result = _apply_addon(
            plugin_as_addon(plugin), home=home, force=force, dry_run=dry_run, agent=agent
        )
        status = _addon_status_to_plugin(result.status, dry_run)
        return PluginResult(plugin["id"], agent, status, result.detail)
    kind = plugin["kind"]
    if kind == "mcp":
        return _configure_mcp(plugin, agent, home, force=force, dry_run=dry_run)
    return PluginResult(plugin["id"], agent, "failed", f"unsupported kind {kind}")


def _addon_status_to_plugin(status: str, dry_run: bool) -> str:
    """Map an AddonResult.status to the PluginResult vocabulary."""
    if dry_run and status == "dry-run":
        return "would_install"
    if status in ("installed", "cloned", "updated"):
        return "installed"
    if status in ("skipped", "failed", "no-op"):
        return status
    return status


def install_plugin(
    plugin_id: str,
    *,
    agent: str | None = None,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    installed_only: bool = True,
    assume_locked: bool = False,
) -> list[PluginResult]:
    """Install a plugin. Default applies to installed agents in the matrix;
    ``--agent`` scopes to one agent. ``installed_only=False`` (configure) acts
    on the full support matrix."""
    from astroai_lab.agent.setup_state import agent_setup_lock

    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise LabError(f"Unknown plugin: {plugin_id}", hint="astroai agent plugins list")
    home = home or Path.home()

    def _run() -> list[PluginResult]:
        return _install_plugin_locked(
            plugin_id,
            plugin,
            agent=agent,
            home=home,
            force=force,
            dry_run=dry_run,
            installed_only=installed_only,
        )

    if assume_locked:
        return _run()
    with agent_setup_lock(home):
        return _run()


def _install_plugin_locked(
    plugin_id: str,
    plugin: dict[str, Any],
    *,
    agent: str | None,
    home: Path,
    force: bool,
    dry_run: bool,
    installed_only: bool,
) -> list[PluginResult]:
    selected = _selected_agents(plugin, agent)
    if installed_only:
        selected = [a for a in selected if _agent_installed(a, home)]
    if not selected:
        return [PluginResult(plugin_id, "", "skipped", "no installed agent in support matrix")]
    results = []
    for a in selected:
        results.append(_apply(plugin, a, home, force=force, dry_run=dry_run))
    return results


def update_plugin(
    plugin_id: str,
    *,
    agent: str | None = None,
    home: Path | None = None,
    dry_run: bool = False,
) -> list[PluginResult]:
    """Refresh a plugin: force re-apply to every installed agent in the matrix."""
    return install_plugin(
        plugin_id,
        agent=agent,
        home=home,
        force=True,
        dry_run=dry_run,
        installed_only=True,
    )


def apply_agent_plugins(
    agent_id: str,
    *,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    installed_only: bool = True,
    defaults_only: bool = False,
    assume_locked: bool = False,
) -> list[PluginResult]:
    """Apply every plugin whose support matrix includes this agent.

    Used by ``agent setup <id>`` (``defaults_only=True``: only ``default: true``
    plugins) and ``agent update <id>`` (force refresh of the full matrix).
    ``installed_only`` keeps the setup surface quiet for agents whose CLI is
    not installed yet.
    """
    home = home or Path.home()
    results: list[PluginResult] = []
    for plugin in load_plugins():
        if agent_id not in plugin.get("agents", []):
            continue
        if defaults_only and not plugin.get("default"):
            continue
        results.extend(
            install_plugin(
                plugin["id"],
                agent=agent_id,
                home=home,
                force=force,
                dry_run=dry_run,
                installed_only=installed_only,
                assume_locked=assume_locked,
            )
        )
    return results


def apply_default_plugins(
    *,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    installed_only: bool = False,
    assume_locked: bool = False,
) -> list[PluginResult]:
    """Apply every plugin marked ``default: true`` to its support matrix."""
    home = home or Path.home()
    results: list[PluginResult] = []
    for plugin in load_plugins():
        if not plugin.get("default"):
            continue
        results.extend(
            install_plugin(
                plugin["id"],
                home=home,
                force=force,
                dry_run=dry_run,
                installed_only=installed_only,
                assume_locked=assume_locked,
            )
        )
    return results


def remove_plugin(
    plugin_id: str,
    *,
    agent: str | None = None,
    home: Path | None = None,
    dry_run: bool = False,
) -> list[PluginResult]:
    """Remove a plugin from the support matrix (or one --agent)."""
    from astroai_lab.agent.setup_state import agent_setup_lock

    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise LabError(f"Unknown plugin: {plugin_id}", hint="astroai agent plugins list")
    home = home or Path.home()
    with agent_setup_lock(home):
        return _remove_plugin_locked(plugin, agent=agent, home=home, dry_run=dry_run)


def _remove_plugin_locked(
    plugin: dict[str, Any], *, agent: str | None, home: Path, dry_run: bool
) -> list[PluginResult]:
    selected = _selected_agents(plugin, agent)
    results: list[PluginResult] = []
    for a in selected:
        results.append(_remove_from_agent(plugin, a, home, dry_run=dry_run))
    return results


def _remove_from_agent(
    plugin: dict[str, Any], agent: str, home: Path, *, dry_run: bool
) -> PluginResult:
    if (plugin.get("install") or {}).get("type"):
        return PluginResult(
            plugin["id"],
            agent,
            "no-op",
            "no automated removal; remove files manually (agent plugins list)",
        )
    kind = plugin["kind"]
    if kind == "mcp":
        server = str(plugin["install"]["server"])
        if not _mcp_present(agent, server, home):
            return PluginResult(plugin["id"], agent, "skipped", "not merged")
        if dry_run:
            return PluginResult(
                plugin["id"], agent, "would_remove", f"mcpServers.{server} from {agent} config"
            )
        from astroai_lab.agent.agent_targets import remove_mcp_server

        remove_mcp_server(home, agent, server)
        return PluginResult(plugin["id"], agent, "removed", f"mcpServers.{server}")
    return PluginResult(
        plugin["id"],
        agent,
        "no-op",
        "no automated removal; remove files manually (agent plugins list)",
    )


def configure_plugin(
    plugin_id: str,
    *,
    agent: str | None = None,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> list[PluginResult]:
    """Per-agent config merge (kind: mcp).

    Applies to the full support matrix (or --agent); other kinds point at
    install/update instead.
    """
    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise LabError(f"Unknown plugin: {plugin_id}", hint="astroai agent plugins list")
    home = home or Path.home()
    selected = _selected_agents(plugin, agent)
    results: list[PluginResult] = []
    for a in selected:
        if (plugin.get("install") or {}).get("type"):
            results.append(_apply(plugin, a, home, force=force, dry_run=dry_run))
            continue
        kind = plugin["kind"]
        if kind == "mcp":
            results.append(_configure_mcp(plugin, a, home, force=force, dry_run=dry_run))
        else:
            results.append(PluginResult(plugin_id, a, "no-op", "use `plugins install/update`"))
    return results


# ---------------------------------------------------------------------------
# Recursive agent removal (wired into registry._remove_registry_method)
# ---------------------------------------------------------------------------


def remove_agent_plugin_files(
    agent_id: str,
    *,
    home: Path | None = None,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Drop every plugin-applied file for one agent (recursive removal).

    Called by ``agent remove <agent>`` so uninstalling an agent also removes
    its plugin-created files. Returns ``RemoveResult``-shaped dicts
    (target / status / detail) for the registry to surface.
    """
    home = home or Path.home()
    results: list[dict[str, str]] = []
    for plugin in load_plugins():
        if agent_id not in plugin.get("agents", []):
            continue
        res = _remove_from_agent(plugin, agent_id, home, dry_run=dry_run)
        # Only surface actionable rows — a plugin the agent never installed
        # would otherwise add a noisy `skipped` line to `agent remove`.
        if res.status in ("removed", "would_remove"):
            results.append(
                {
                    "target": f"plugins:{agent_id}:{plugin['id']}",
                    "status": res.status,
                    "detail": res.detail,
                }
            )
    return results
