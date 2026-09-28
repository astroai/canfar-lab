"""OpenScience (synthetic-sciences/openscience) configuration for CANFAR.

Merges the CANFAR pieces into ``~/.config/openscience/openscience.json``
without replacing the file: the astroai MCP server (Ray compute + astronomy
data tools), a CANFAR primer loaded as instructions, the bundled astronomy
skills, approval prompts for tools that start sessions or write storage,
``autoupdate: false`` (the image pins the binary) and a default of full access
instead of the OS sandbox. Models are left to OpenScience, which picks one from
the provider keys in the environment.

On Linux the OpenScience sandbox (bubblewrap) denies all sockets and mounts only
granted paths, so sandboxed kernels and MCP servers could neither query archives
nor read ``/arc/projects``; Skaha pods may also lack user namespaces, where the
default ``onUnavailable: error`` refuses every command. A CANFAR session is
already a per-user container, so the default matches the terminal and
JupyterLab; users can still pick the contained mode per project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from canfar_lab.utils.json_utils import atomic_write_text, read_jsonc

CONFIG_REL = Path(".config") / "openscience" / "openscience.json"
PRIMER_REL = Path(".config") / "openscience" / "canfar.md"
MCP_SERVER = "astroai"
MCP_TIMEOUT_MS = 600_000

#: MCP tools that launch sessions on shared hardware or write storage.
ASK_TOOLS = (
    "cluster_start",
    "cluster_stop",
    "job_run",
    "job_submit",
    "job_cancel",
    "vospace_copy",
)


def bundle_dir(root: Path) -> Path:
    return root / "openscience"


def managed_config(root: Path, home: Path, mcp_command: tuple[str, ...]) -> dict[str, Any]:
    """The keys this module owns in ``openscience.json``."""
    return {
        "autoupdate": False,
        "mcp": {
            MCP_SERVER: {
                "type": "local",
                "command": list(mcp_command),
                "enabled": True,
                "timeout": MCP_TIMEOUT_MS,
            }
        },
        "instructions": [str(home / PRIMER_REL)],
        "skills": {"paths": [str(bundle_dir(root) / "skills"), "~/.agents/skills"]},
        # MCP calls are checked as permission "mcp" with pattern "<server>_<tool>".
        "permission": {"mcp": {f"{MCP_SERVER}_{tool}": "ask" for tool in ASK_TOOLS}},
        "sandbox": {"enabled": False, "onUnavailable": "warn"},
    }


def merge_config(data: dict[str, Any], managed: dict[str, Any], *, force: bool) -> dict[str, Any]:
    """Overlay ``managed`` onto the user's config.

    Our MCP entry is always refreshed (its command follows the installed CLI);
    list entries are appended when missing; scalar and permission choices the
    user already made win unless ``force``.
    """
    out = dict(data)
    if force or "autoupdate" not in out:
        out["autoupdate"] = managed["autoupdate"]
    mcp = dict(out.get("mcp") or {})
    mcp[MCP_SERVER] = managed["mcp"][MCP_SERVER]
    out["mcp"] = mcp
    instructions = [str(x) for x in out.get("instructions") or []]
    out["instructions"] = instructions + [
        x for x in managed["instructions"] if x not in instructions
    ]
    skills = dict(out.get("skills") or {})
    paths = [str(x) for x in skills.get("paths") or []]
    skills["paths"] = paths + [x for x in managed["skills"]["paths"] if x not in paths]
    out["skills"] = skills
    permission = out.get("permission")
    ours = managed["permission"]["mcp"]
    if isinstance(permission, dict):
        merged = dict(permission)
        rules = merged.get("mcp")
        if isinstance(rules, dict):
            merged["mcp"] = {
                **rules,
                **{k: v for k, v in ours.items() if force or k not in rules},
            }
        elif rules is None or force:
            merged["mcp"] = dict(ours)
        out["permission"] = merged
    elif permission is None:
        out["permission"] = {"mcp": dict(ours)}
    sandbox = out.get("sandbox")
    if isinstance(sandbox, dict):
        out["sandbox"] = {
            **sandbox,
            **{k: v for k, v in managed["sandbox"].items() if force or k not in sandbox},
        }
    else:
        out["sandbox"] = dict(managed["sandbox"])
    return out


def ensure_openscience_config(
    root: Path,
    home: Path,
    *,
    force: bool,
    dry_run: bool,
    mcp_command: tuple[str, ...] | None = None,
) -> list[str]:
    """Write the primer and merge the managed config; return action labels."""
    if mcp_command is None:
        from canfar_lab.studio_profile import mcp_serve_command

        mcp_command = mcp_serve_command(home=home)
    actions: list[str] = []
    primer_src = bundle_dir(root) / "canfar.md"
    primer_dst = home / PRIMER_REL
    text = primer_src.read_text(encoding="utf-8")
    if not primer_dst.is_file() or primer_dst.read_text(encoding="utf-8") != text:
        actions.append("openscience:primer")
        if not dry_run:
            atomic_write_text(primer_dst, text)

    cfg = home / CONFIG_REL
    data: dict[str, Any] = {}
    if cfg.is_file():
        parsed = read_jsonc(cfg)
        if not isinstance(parsed, dict):
            raise ValueError(f"{cfg} is not a JSON object")
        data = parsed
    merged = merge_config(data, managed_config(root, home, mcp_command), force=force)
    if merged != data:
        actions.append("openscience:config")
        if not dry_run:
            import json

            if cfg.is_file():
                backup = cfg.with_name(cfg.name + ".bak")
                backup.write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")
            atomic_write_text(cfg, json.dumps(merged, indent=2) + "\n")
    return actions
