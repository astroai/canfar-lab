"""Inventory: skill/bundle dir scans + config presence/syntax verification."""

from __future__ import annotations

import json
from pathlib import Path

from astroai_lab.agent.bundle_path import bundle_root
from astroai_lab.agent.registry import registry_verify_issues
from astroai_lab.utils.json_utils import read_json, read_jsonc


def _rel_home(path: Path, home: Path) -> str:
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _check_json_file(path: Path, home: Path, *, jsonc: bool = False) -> str | None:
    """Return a syntax-issue string, or None if the file parses."""
    try:
        if jsonc:
            read_jsonc(path)
        else:
            read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        kind = "JSONC" if jsonc else "JSON"
        return f"{kind} syntax error in {_rel_home(path, home)}: {exc}"
    return None


def _check_toml_file(path: Path, home: Path) -> str | None:
    from astroai_lab.utils.toml_compat import tomllib

    try:
        tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return f"TOML syntax error in {_rel_home(path, home)}: {exc}"
    return None


def _check_yaml_file(path: Path, home: Path) -> str | None:
    try:
        import yaml
    except ImportError:
        return None
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return f"YAML syntax error in {_rel_home(path, home)}: {exc}"
    return None


def verify_config_syntax(home: Path) -> list[str]:
    """Parse existing agent config files; report syntax errors only."""
    issues: list[str] = []
    checks: list[tuple[Path, str]] = [
        (home / ".cursor" / "mcp.json", "jsonc"),
        (home / ".claude.json", "json"),
        (home / ".claude" / "settings.json", "json"),
        (home / ".config" / "opencode" / "opencode.json", "jsonc"),
        (home / ".config" / "kilo" / "kilo.jsonc", "jsonc"),
        (home / ".copilot" / "mcp-config.json", "jsonc"),
        (home / ".codex" / "config.toml", "toml"),
        (home / ".marimo.toml", "toml"),
        (home / ".config" / "goose" / "config.yaml", "yaml"),
        (home / ".qoder" / "settings.json", "json"),
    ]
    for path, kind in checks:
        if not path.is_file():
            continue
        if kind == "json":
            err = _check_json_file(path, home, jsonc=False)
        elif kind == "jsonc":
            err = _check_json_file(path, home, jsonc=True)
        elif kind == "toml":
            err = _check_toml_file(path, home)
        else:
            err = _check_yaml_file(path, home)
        if err:
            issues.append(err)
    return issues


def verify_setup(home: Path, *, probe_binaries: bool = False) -> list[str]:
    """Presence + content checks, then syntax validation of configs that exist.

    ``probe_binaries`` is off by default so ``agent list`` / status reports stay
    fast; ``agent verify`` turns it on to exercise installed CLIs.

    Fresh homes with no agents installed pass (no Cursor MCP nag). Presence
    checks for Cursor / Claude / OpenCode only run when that agent's binary
    is on PATH. Goose provider/model is left to ``goose configure``.
    """
    from astroai_lab.agent.install import classify_binary, tool_binary
    from astroai_lab.agent.registry import (
        get_registry_agent,
        list_registry_agents,
    )

    def _agent_installed(agent_id: str) -> bool:
        agent = get_registry_agent(agent_id)
        if agent is None:
            return False
        from astroai_lab.agent.install import TOOLS

        probe = tool_binary(agent_id) if agent_id in TOOLS else str(agent["binary"])
        return classify_binary(probe, home=home)["source"] != "missing"

    issues: list[str] = []
    # Syntax first — broken configs often look "empty" to content checks.
    issues.extend(verify_config_syntax(home))

    if _agent_installed("cursor"):
        mcp = home / ".cursor" / "mcp.json"
        if mcp.is_file():
            try:
                data = read_jsonc(mcp)
                if isinstance(data, dict) and not data.get("mcpServers"):
                    issues.append("Cursor MCP empty (~/.cursor/mcp.json)")
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        else:
            issues.append("Cursor MCP not configured (~/.cursor/mcp.json)")

    if _agent_installed("claude"):
        claude = home / ".claude.json"
        if claude.is_file():
            try:
                data = read_json(claude)
                if not data.get("mcpServers"):
                    issues.append("Claude MCP empty (~/.claude.json)")
            except (OSError, ValueError, json.JSONDecodeError):
                pass

    oc = home / ".config" / "opencode" / "opencode.json"
    if oc.is_file() and _agent_installed("opencode"):
        try:
            data = read_jsonc(oc)
            if isinstance(data, dict) and not data.get("mcp"):
                issues.append("OpenCode MCP empty (~/.config/opencode/opencode.json)")
            if isinstance(data, dict):
                from astroai_lab.agent.opencode_config import opencode_config_issues

                issues.extend(opencode_config_issues(data))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    marimo = home / ".marimo.toml"
    if marimo.is_file():
        try:
            if "openrouter" not in marimo.read_text(encoding="utf-8"):
                issues.append(
                    "marimo.toml missing OpenRouter config — run: astroai agent setup marimo"
                )
        except OSError:
            pass

    # Phase 1 registry: verify config of installed registered agents only, so
    # fresh images without hermes/openclaw don't fail the container gate.
    issues.extend(registry_verify_issues(home, installed_only=True, probe_binaries=probe_binaries))

    legacy_clis: list[str] = []
    for agent in list_registry_agents():
        info = classify_binary(str(agent["binary"]), home=home)
        if info.get("legacy"):
            legacy_clis.append(agent["id"])
    if legacy_clis:
        issues.append(
            "Legacy agent CLIs on $SCRATCH (prefer $HOME): "
            + ", ".join(legacy_clis)
            + ". Reinstall with: astroai agent install NAME"
            + "  (or use the upstream installer)"
        )

    return issues


def list_bundles() -> dict[str, str]:
    manifest = bundle_root() / "manifest.json"
    if not manifest.is_file():
        return {}
    data = read_json(manifest)
    return {k: v.get("description", "") for k, v in data.get("bundles", {}).items()}
