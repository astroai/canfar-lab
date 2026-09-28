"""OpenScience (synthetic-sciences/openscience) configuration for CANFAR.

Merges the CANFAR pieces into ``~/.config/openscience/openscience.json``
without replacing the file: the astroai MCP server (Ray compute + astronomy
data tools), a CANFAR primer loaded as instructions, the bundled astronomy
skills, approval prompts for tools that start sessions or write storage,
``autoupdate: false`` (the image pins the binary) and a default of full access
instead of the OS sandbox. Models are left to OpenScience, which picks one from
the provider keys in the environment; an OpenAI-compatible endpoint named by
``ASTROAI_LLM_BASE_URL`` + ``ASTROAI_LLM_MODELS`` (environment or
``~/.astroai/lab/.env``) is added as the ``canfar`` provider.

It also installs an astronomy specialist agent and CANFAR slash commands into
``~/.config/openscience/{agent,command}/``. A stamp file records what was
written, so files the user edited are left alone (unless ``force``) and files
still as shipped follow the bundle.

On Linux the OpenScience sandbox (bubblewrap) denies all sockets and mounts only
granted paths, so sandboxed kernels and MCP servers could neither query archives
nor read ``/arc/projects``; Skaha pods may also lack user namespaces, where the
default ``onUnavailable: error`` refuses every command. A CANFAR session is
already a per-user container, so the default matches the terminal and
JupyterLab; users can still pick the contained mode per project.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from canfar_lab.utils.json_utils import atomic_write_text, read_jsonc

CONFIG_DIR_REL = Path(".config") / "openscience"
CONFIG_REL = CONFIG_DIR_REL / "openscience.json"
PRIMER_REL = CONFIG_DIR_REL / "canfar.md"
STAMP_NAME = ".canfar-managed.json"
#: Bundle subdirectories copied into the config dir (OpenScience scans both).
MANAGED_DIRS = ("agent", "command")
MCP_SERVER = "astroai"
MCP_TIMEOUT_MS = 600_000

LLM_PROVIDER = "canfar"
LLM_PROVIDER_NAME = "CANFAR LLM endpoint"
LLM_BASE_URL_ENV = "ASTROAI_LLM_BASE_URL"
LLM_MODELS_ENV = "ASTROAI_LLM_MODELS"
LLM_API_KEY_ENV = "ASTROAI_LLM_API_KEY"

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


def file_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lab_setting(home: Path, name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    from canfar_lab.agent.setup import _read_dotenv_value

    return (_read_dotenv_value(home / ".astroai" / "lab" / ".env", name) or "").strip()


def llm_provider(home: Path) -> dict[str, Any] | None:
    """The ``canfar`` provider block, or None when no endpoint is configured.

    The API key stays an ``{env:…}`` reference that OpenScience resolves at
    start-up (the wrapper sources the lab dotenv), so it never lands in the file.
    """
    base_url = _lab_setting(home, LLM_BASE_URL_ENV)
    models = [m.strip() for m in _lab_setting(home, LLM_MODELS_ENV).split(",") if m.strip()]
    if not base_url or not models:
        return None
    # The OpenAI-compatible SDK refuses an empty key; most self-hosted servers ignore it.
    api_key = f"{{env:{LLM_API_KEY_ENV}}}" if _lab_setting(home, LLM_API_KEY_ENV) else "canfar"
    return {
        "npm": "@ai-sdk/openai-compatible",
        "name": LLM_PROVIDER_NAME,
        "options": {"baseURL": base_url, "apiKey": api_key},
        "models": {m: {"name": m} for m in models},
    }


def managed_files(root: Path) -> dict[str, str]:
    """``agent/astronomy.md``-style paths → text for every file we install."""
    out: dict[str, str] = {}
    for sub in MANAGED_DIRS:
        for path in sorted((bundle_dir(root) / sub).glob("*.md")):
            out[f"{sub}/{path.name}"] = path.read_text(encoding="utf-8")
    return out


def sync_managed_files(root: Path, home: Path, *, force: bool, dry_run: bool) -> bool:
    """Install bundle agents/commands; return whether any file changed.

    A file is (re)written when missing, or when it still matches the digest we
    recorded last time (unedited) and the bundle changed. A file with no record
    or a different digest is the user's and is kept unless ``force``. Files we
    stopped shipping are removed while unedited.
    """
    cfg_dir = home / CONFIG_DIR_REL
    stamp_path = cfg_dir / STAMP_NAME
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamp = {}
    if not isinstance(stamp, dict):
        stamp = {}
    files = managed_files(root)
    new_stamp: dict[str, str] = {}
    changed = False
    for rel, text in files.items():
        dst = cfg_dir / rel
        if dst.is_file():
            current = dst.read_text(encoding="utf-8")
            if current == text:
                new_stamp[rel] = file_digest(text)
                continue
            if stamp.get(rel) != file_digest(current) and not force:
                if rel in stamp:
                    new_stamp[rel] = stamp[rel]
                continue
        changed = True
        new_stamp[rel] = file_digest(text)
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(dst, text)
    for rel in set(stamp) - set(files):
        dst = cfg_dir / rel
        if dst.is_file() and file_digest(dst.read_text(encoding="utf-8")) == stamp[rel]:
            changed = True
            if not dry_run:
                dst.unlink()
    if new_stamp != stamp and not dry_run:
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(stamp_path, json.dumps(new_stamp, indent=2, sort_keys=True) + "\n")
    return changed


def managed_config(root: Path, home: Path, mcp_command: tuple[str, ...]) -> dict[str, Any]:
    """The keys this module owns in ``openscience.json``."""
    managed: dict[str, Any] = {
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
    provider = llm_provider(home)
    if provider:
        managed["provider"] = {LLM_PROVIDER: provider}
    return managed


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
    providers = dict(out.get("provider") or {})
    ours = (managed.get("provider") or {}).get(LLM_PROVIDER)
    if ours:
        providers[LLM_PROVIDER] = ours
    elif (providers.get(LLM_PROVIDER) or {}).get("name") == LLM_PROVIDER_NAME:
        del providers[LLM_PROVIDER]
    if providers:
        out["provider"] = providers
    else:
        out.pop("provider", None)
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
    if sync_managed_files(root, home, force=force, dry_run=dry_run):
        actions.append("openscience:files")

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
            if cfg.is_file():
                backup = cfg.with_name(cfg.name + ".bak")
                backup.write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")
            atomic_write_text(cfg, json.dumps(merged, indent=2) + "\n")
    return actions
