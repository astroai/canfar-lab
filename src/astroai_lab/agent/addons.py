"""Install transports for agent plugins (MCP, rules, tools).

Plugin YAML under ``data/agent/plugins/*.yaml`` is the catalog. This module
applies those entries (bundled / github-rule / mcp-snippet / cli-tool) via
``_apply_addon``, also used by ``agent plugins install``.

Skills (SKILL.md) are not installed here — use ``npx skills``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astroai_lab.agent.agent_targets import (
    MCP_TARGETS,
    mcp_server_present,
    mcp_target,
    merge_mcp_server,
)
from astroai_lab.agent.agent_targets import cursor_to_opencode as _cursor_to_opencode
from astroai_lab.agent.install import install_tool, tool_on_path
from astroai_lab.agent.upstream import (
    _refresh_upstream_repo,
    _upstream_cache_root,
)
from astroai_lab.errors import LabError


@dataclass(frozen=True)
class AddonResult:
    id: str
    status: str
    detail: str = ""


def plugin_as_addon(plugin: dict[str, Any]) -> dict[str, Any]:
    """Map a plugin registry entry to the addon dict shape used by transports."""
    install = dict(plugin.get("install") or {})
    return {
        "id": plugin["id"],
        "kind": plugin["kind"],
        "tags": plugin.get("tags", []),
        "summary": plugin.get("summary", ""),
        "homepage": plugin.get("homepage", ""),
        "default": bool(plugin.get("default")),
        "agents": list(plugin.get("agents", [])),
        "install": install,
    }


def addon_installed(item: dict[str, Any], home: Path, agent: str | None = None) -> bool:
    install = item.get("install") or {}
    itype = install.get("type")
    addon_id = item["id"]

    if itype == "bundled":
        if addon_id == "token-efficient":
            return (home / ".cursor" / "rules" / "token-efficient.mdc").is_file()
        if addon_id.startswith("mcp-"):
            server = addon_id.removeprefix("mcp-")
            return _mcp_server_present(home, server, agent=agent)
        return False

    if itype == "github-rule":
        rule = Path(install.get("path", "")).name
        return (home / ".cursor" / "rules" / rule).is_file()

    if itype == "mcp-snippet":
        server = install.get("server", "")
        agents = [agent] if agent else list(item.get("agents") or [])
        hosts = [a for a in agents if mcp_target(a)]
        if not server or not hosts:
            return False
        return all(mcp_server_present(home, a, server) for a in hosts)

    if itype == "cli-tool":
        return tool_on_path(install.get("tool", addon_id))

    return False


def _mcp_server_present(home: Path, server: str, agent: str | None = None) -> bool:
    """True when the given agent (or every MCP host, if omitted) has ``server``."""
    if not server:
        return False
    agents = [agent] if agent else list(MCP_TARGETS)
    return all(mcp_server_present(home, a, server) for a in agents)


def add_addon(
    addon_id: str,
    *,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> AddonResult:
    from astroai_lab.agent.plugins import get_plugin

    plugin = get_plugin(addon_id)
    if plugin is None:
        raise LabError(
            f"Unknown addon: {addon_id}",
            hint="astroai agent plugins list",
        )
    return _apply_addon(plugin_as_addon(plugin), home=home, force=force, dry_run=dry_run)


def _apply_addon(
    item: dict[str, Any],
    *,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    agent: str | None = None,
) -> AddonResult:
    """Apply one addon (plugin-as-addon dict) via its install transport.

    ``agent`` scopes mcp-snippet writes to one support-matrix agent; omit it
    to apply every agent listed on the plugin.
    """
    addon_id = item["id"]
    home = home or Path.home()
    install = item.get("install") or {}
    itype = install.get("type")

    if itype == "bundled":
        return AddonResult(
            addon_id,
            "skipped",
            install.get("note") or "bundled — run: astroai agent setup",
        )

    if not force and addon_installed(item, home, agent=agent):
        return AddonResult(addon_id, "skipped", "already installed")

    if itype == "github-rule":
        return _install_github_rule(item, home=home, force=force, dry_run=dry_run)

    if itype == "mcp-snippet":
        return _install_mcp_snippet(item, home=home, force=force, dry_run=dry_run, agent=agent)

    if itype == "cli-tool":
        tool = install.get("tool")
        if not tool:
            raise LabError(f"Addon {addon_id} missing install.tool")
        if dry_run:
            return AddonResult(addon_id, "dry-run", f"would install CLI {tool}")
        install_tool(tool, dry_run=False)
        return AddonResult(addon_id, "installed", tool)

    raise LabError(f"Addon {addon_id} has unsupported install type: {itype}")


def _install_github_rule(
    item: dict[str, Any],
    *,
    home: Path,
    force: bool,
    dry_run: bool,
) -> AddonResult:
    install = item["install"]
    repo = install["repo"]
    path = install["path"]
    if dry_run:
        return AddonResult(item["id"], "dry-run", path)

    cache_root = _upstream_cache_root(home, repo)
    status, detail = _refresh_upstream_repo(cache_root, repo, path)
    if status == "failed":
        return AddonResult(item["id"], "failed", detail)

    src = cache_root / path
    if not src.is_file():
        return AddonResult(item["id"], "failed", f"missing rule at {path}")
    dst = home / ".cursor" / "rules" / src.name
    if dst.is_file() and not force:
        return AddonResult(item["id"], "skipped", "already installed")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return AddonResult(item["id"], status, str(dst))


def _install_mcp_snippet(
    item: dict[str, Any],
    *,
    home: Path,
    force: bool,
    dry_run: bool,
    agent: str | None = None,
) -> AddonResult:
    install = item["install"]
    server = install["server"]
    cursor_cfg = install.get("cursor") or {}
    opencode_cfg = install.get("opencode") or {}
    agents = list(item.get("agents") or [])
    if not agents:
        raise LabError(
            f"Addon {item['id']} mcp-snippet requires a non-empty agents support matrix",
            hint="Declare agents: [cursor, ...] on the plugin YAML",
        )
    if agent is not None:
        if agent not in agents:
            return AddonResult(
                item["id"], "skipped", f"{agent} not in support matrix ({', '.join(agents)})"
            )
        agents = [agent]

    if dry_run:
        return AddonResult(item["id"], "dry-run", f"mcp:{server} → {', '.join(agents)}")

    written: list[str] = []
    for ag in agents:
        if mcp_target(ag) is None:
            continue
        entry = opencode_cfg or _cursor_to_opencode(cursor_cfg) if ag == "opencode" else cursor_cfg
        if not entry:
            continue
        if merge_mcp_server(home, ag, server, entry, force=force):
            written.append(ag)
    if not written and not force:
        return AddonResult(item["id"], "skipped", f"mcp:{server} already present")
    detail = f"mcp:{server}" + (f" → {', '.join(written)}" if written else "")
    return AddonResult(item["id"], "installed", detail)
