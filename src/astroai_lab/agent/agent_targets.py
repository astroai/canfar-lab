"""Per-agent config targets and a single MCP merge policy.

One place owns:
- skill directory layout (``AGENT_SKILL_DIRS``)
- MCP config path / key / format per agent
- merge semantics: never replace a whole config file; merge server entries and
  preserve every other top-level key. ``force`` only means "overwrite this
  server's entry if already present".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from astroai_lab.errors import LabError
from astroai_lab.utils.json_utils import merge_dicts, read_json, read_jsonc, write_json

# agentskills.io SKILL.md layout under the agent home tree.
# Only agents that actually load this convention belong here — a plugin
# listing `skill-hosts` fans out to every key.
AGENT_SKILL_DIRS: dict[str, str] = {
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "cursor": ".cursor/skills",
    "goose": ".config/goose/skills",
    "hermes": ".hermes/skills",
    "openclaw": ".openclaw/skills",
    "opencode": ".config/opencode/skills",
}

McpFormat = Literal["json", "json5", "yaml"]
McpKey = Literal["mcpServers", "mcp"]


@dataclass(frozen=True)
class McpTarget:
    """Where an agent stores MCP server entries."""

    agent: str
    relpath: str
    key: McpKey = "mcpServers"
    fmt: McpFormat = "json"


# Agents that accept MCP merges. Path is home-relative.
MCP_TARGETS: dict[str, McpTarget] = {
    "cursor": McpTarget("cursor", ".cursor/mcp.json"),
    "copilot": McpTarget("copilot", ".copilot/mcp-config.json"),
    "claude": McpTarget("claude", ".claude.json"),
    "opencode": McpTarget("opencode", ".config/opencode/opencode.json", key="mcp", fmt="json5"),
    "openclaw": McpTarget("openclaw", ".openclaw/openclaw.json", fmt="json5"),
    "hermes": McpTarget("hermes", ".hermes/config.yaml", fmt="yaml"),
}


def mcp_target(agent: str) -> McpTarget | None:
    return MCP_TARGETS.get(agent)


def skill_hosts() -> tuple[str, ...]:
    """Agents that load agentskills.io SKILL.md trees from AGENT_SKILL_DIRS."""
    return tuple(sorted(AGENT_SKILL_DIRS))


def mcp_hosts() -> tuple[str, ...]:
    """Agents that accept MCP server merges via MCP_TARGETS."""
    return tuple(sorted(MCP_TARGETS))


def skill_path(home: Path, agent: str, name: str) -> Path | None:
    """``<home>/<skill-dir>/<name>`` for a skill-host agent, else None."""
    rel = AGENT_SKILL_DIRS.get(agent)
    if not rel:
        return None
    return home / rel / name


def expand_home(path: str, home: Path) -> Path:
    """Resolve ``~/…`` or bare relative paths as home-relative."""
    raw = path.strip()
    if raw.startswith("~/") or raw == "~":
        return (home / raw[2:]).resolve() if raw != "~" else home.resolve()
    p = Path(raw)
    if p.is_absolute():
        return p
    return (home / p).resolve()


def _read_config(path: Path, fmt: McpFormat) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        if fmt == "yaml":
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        elif fmt == "json5":
            data = read_jsonc(path)
        else:
            data = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise LabError(
            f"Cannot merge MCP into unreadable config: {path}",
            hint=f"Fix syntax first (`astroai agent verify`): {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise LabError(f"MCP config must be an object: {path}")
    return data


def _write_config(path: Path, data: dict[str, Any], fmt: McpFormat) -> None:
    from astroai_lab.utils.json_utils import atomic_write_text

    if fmt == "yaml":
        atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))
    else:
        write_json(path, data)


def cursor_to_opencode(cfg: dict[str, Any]) -> dict[str, Any]:
    """Translate a cursor-shaped MCP entry to OpenCode's local command shape."""
    cmd = cfg.get("command")
    args = cfg.get("args") or []
    if not cmd:
        return {}
    out: dict[str, Any] = {
        "type": "local",
        "command": [cmd, *args],
        "enabled": True,
    }
    if cfg.get("env"):
        out["environment"] = cfg["env"]
    return out


def merge_mcp_server(
    home: Path,
    agent: str,
    server: str,
    entry: dict[str, Any],
    *,
    force: bool = False,
) -> bool:
    """Merge one MCP server into an agent's config. True when written.

    Preserves all other top-level keys. ``force=False`` skips if ``server``
    already exists. Raises LabError on unreadable configs (never clobbers).
    """
    target = mcp_target(agent)
    if target is None:
        raise LabError(f"No MCP config target for agent {agent}")
    if not entry:
        return False
    path = home / target.relpath
    data = _read_config(path, target.fmt)
    bucket = dict(data.get(target.key) or {})
    if server in bucket and not force:
        return False
    bucket[server] = entry
    data[target.key] = bucket
    _write_config(path, data, target.fmt)
    return True


def merge_mcp_file(
    src: Path,
    dst: Path,
    *,
    key: McpKey = "mcpServers",
    fmt: McpFormat = "json",
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Merge all servers from ``src`` into ``dst`` without replacing ``dst``.

    ``force`` overwrites individual server entries that already exist in ``dst``.
    Never replaces the whole destination file (preserves user/plugin keys).
    Extra top-level keys from ``src`` (e.g. OpenCode ``lsp``) merge when both
    sides are mappings; bool overlays replace.
    """
    if not src.is_file():
        return
    if dry_run:
        return
    overlay = _read_config(src, fmt)
    data = _read_config(dst, fmt) if dst.is_file() else {}
    src_servers = overlay.get(key) or {}
    if isinstance(src_servers, dict) and src_servers:
        base = dict(data.get(key) or {})
        if force:
            data[key] = merge_dicts(base, src_servers)
        else:
            for name, entry in src_servers.items():
                if name not in base:
                    base[name] = entry
            data[key] = base
    # OpenCode-style extras (lsp / formatter): merge mappings, else take overlay.
    for extra in ("lsp", "formatter"):
        if extra not in overlay:
            continue
        ov = overlay[extra]
        base_extra = data.get(extra)
        if isinstance(ov, bool) or not isinstance(base_extra, dict):
            data[extra] = ov
        elif isinstance(ov, dict):
            data[extra] = merge_dicts(base_extra, ov)
    if key == "mcp" and fmt == "json5":
        from astroai_lab.agent.opencode_config import sanitize_opencode_config

        data, _ = sanitize_opencode_config(data)
    _write_config(dst, data, fmt)


def mcp_server_present(home: Path, agent: str, server: str) -> bool:
    target = mcp_target(agent)
    if target is None or not server:
        return False
    path = home / target.relpath
    if not path.is_file():
        return False
    try:
        data = _read_config(path, target.fmt)
    except LabError:
        return False
    bucket = data.get(target.key) or {}
    return isinstance(bucket, dict) and server in bucket


def remove_mcp_server(home: Path, agent: str, server: str) -> bool:
    """Remove one MCP server entry. True when the file changed."""
    target = mcp_target(agent)
    if target is None or not server:
        return False
    path = home / target.relpath
    if not path.is_file():
        return False
    data = _read_config(path, target.fmt)
    bucket = dict(data.get(target.key) or {})
    if server not in bucket:
        return False
    del bucket[server]
    data[target.key] = bucket
    _write_config(path, data, target.fmt)
    return True
