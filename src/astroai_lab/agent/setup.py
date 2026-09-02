"""Agent config writing + setup orchestration."""

from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astroai_lab.agent.bundle_path import bundle_root
from astroai_lab.agent.inventory import list_bundles, verify_setup
from astroai_lab.errors import LabError
from astroai_lab.utils.json_utils import read_json

OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
_OPENROUTER_DOTENV_MARKER = "# astroai openrouter dotenv"


def openrouter_dotenv_path(home: Path | None = None) -> Path:
    """Single shared secrets file for OpenRouter (marimo + agent CLIs)."""
    return (home or Path.home()) / ".astroai" / "lab" / ".env"


def _read_dotenv_value(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == name:
            return val.strip().strip("'\"") or None
    return None


def _write_dotenv_value(path: Path, name: str, value: str, *, dry_run: bool) -> None:
    """Set NAME=value in a dotenv file; preserve other keys."""
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(raw)
            continue
        key, _, _ = stripped.partition("=")
        if key.strip() == name:
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(raw)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _key_from_marimo_toml(cfg: Path) -> str | None:
    if not cfg.is_file():
        return None
    try:
        from astroai_lab.utils.toml_compat import tomllib

        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        key = _toml_get(data, "ai", "openrouter", "api_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    except Exception:  # noqa: BLE001 — fall through to line scan
        pass
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    in_section = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped == "[ai.openrouter]":
            in_section = True
            continue
        if in_section and stripped.startswith("[") and stripped.endswith("]"):
            break
        if in_section and stripped.startswith("api_key"):
            _, _, val = stripped.partition("=")
            return val.strip().strip("'\"") or None
    return None


def discover_openrouter_key(home: Path | None = None) -> str | None:
    """Resolve OpenRouter key once: env → shared .env → existing ~/.marimo.toml."""
    home = home or Path.home()
    for candidate in (
        os.environ.get(OPENROUTER_KEY_ENV),
        os.environ.get("OPENROUTER_KEY"),
        _read_dotenv_value(openrouter_dotenv_path(home), OPENROUTER_KEY_ENV),
        _key_from_marimo_toml(home / ".marimo.toml"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def ensure_pi_openrouter_auth(home: Path, *, dry_run: bool = False, force: bool = False) -> bool:
    """Seed Pi ``~/.pi/agent/auth.json`` from the shared AstroAI OpenRouter key."""
    key = discover_openrouter_key(home)
    if not key:
        return False
    auth_path = home / ".pi" / "agent" / "auth.json"
    data: dict[str, object] = {}
    if auth_path.is_file():
        from astroai_lab.utils.json_utils import read_json

        try:
            loaded = read_json(auth_path)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError, TypeError):
            data = {}
    existing = data.get("openrouter")
    if isinstance(existing, dict) and existing.get("key") and not force:
        return False
    data["openrouter"] = {"type": "api_key", "key": key}
    if dry_run:
        return True
    from astroai_lab.utils.json_utils import write_json

    auth_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(auth_path, data)
    return True


def ensure_openrouter_dotenv(home: Path, *, dry_run: bool = False) -> str | None:
    """Persist discovered OpenRouter key to ~/.astroai/lab/.env and agent-env.sh.

    One key for marimo AI + every agent that reads OPENROUTER_API_KEY — stop
    pasting the same sk-or-… into each tool.
    """
    key = discover_openrouter_key(home)
    if not key:
        return None
    dotenv = openrouter_dotenv_path(home)
    existing = _read_dotenv_value(dotenv, OPENROUTER_KEY_ENV)
    if existing != key:
        _write_dotenv_value(dotenv, OPENROUTER_KEY_ENV, key, dry_run=dry_run)
    os.environ[OPENROUTER_KEY_ENV] = key

    hook = home / ".astroai" / "lab" / "agent-env.sh"
    source_block = (
        f"{_OPENROUTER_DOTENV_MARKER}\n"
        '_astroai_dotenv="${HOME}/.astroai/lab/.env"\n'
        'if [[ -f "${_astroai_dotenv}" ]]; then\n'
        "  set -a\n"
        "  # shellcheck disable=SC1090\n"
        '  source "${_astroai_dotenv}"\n'
        "  set +a\n"
        "fi\n"
    )
    if not dry_run:
        hook.parent.mkdir(parents=True, exist_ok=True)
        if hook.is_file():
            text = hook.read_text(encoding="utf-8")
            if _OPENROUTER_DOTENV_MARKER not in text:
                from astroai_lab.utils.json_utils import atomic_write_text

                sep = "\n" if text and not text.startswith("\n") else ""
                atomic_write_text(hook, source_block + sep + text)
        else:
            from astroai_lab.utils.json_utils import atomic_write_text

            atomic_write_text(
                hook,
                source_block + "\n# AstroAI lab agent setup: GitHub token for gh + GitHub MCP\n"
                "if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then\n"
                '  export GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"\n'
                "fi\n",
            )
    return key


def install_file(src: Path, dst: Path, *, force: bool, dry_run: bool) -> bool:
    if not src.is_file():
        return False
    if dst.is_file() and not force:
        return False
    if dry_run:
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def install_tree(src_dir: Path, dst_dir: Path, *, force: bool, dry_run: bool) -> int:
    if not src_dir.is_dir():
        return 0
    count = 0
    for src in src_dir.rglob("*"):
        if not src.is_file() or src.name == ".DS_Store":
            continue
        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        if dst.is_file() and not force:
            continue
        if dry_run:
            count += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


def install_skills_tree(src_dir: Path, dst_dir: Path, *, force: bool, dry_run: bool) -> int:
    """Deprecated no-op: skills are installed via ``npx skills``, not AstroAI."""
    return 0


def merge_mcp_servers(src_json: Path, dst_json: Path, *, force: bool, dry_run: bool) -> None:
    """Merge mcpServers from src into dst; never replace the whole destination."""
    from astroai_lab.agent.agent_targets import merge_mcp_file

    merge_mcp_file(src_json, dst_json, key="mcpServers", fmt="json", force=force, dry_run=dry_run)


def merge_claude_json(src_mcp: Path, dst: Path, *, force: bool, dry_run: bool) -> None:
    """Merge mcpServers into ~/.claude.json; preserve other Claude settings."""
    from astroai_lab.agent.agent_targets import merge_mcp_file

    merge_mcp_file(src_mcp, dst, key="mcpServers", fmt="json", force=force, dry_run=dry_run)


def merge_opencode_mcp(src: Path, dst: Path, *, force: bool, dry_run: bool) -> None:
    """Merge OpenCode mcp (+ lsp) without replacing the whole config file."""
    from astroai_lab.agent.agent_targets import merge_mcp_file

    merge_mcp_file(src, dst, key="mcp", fmt="json5", force=force, dry_run=dry_run)


def _toml_get(data: dict[str, Any], *keys: str) -> Any:
    """Walk nested dict keys; return None if any key missing."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _ensure_marimo_toml_kv(
    cfg: Path,
    section: str,
    key: str,
    value: str,
    *,
    dry_run: bool,
    force: bool = False,
) -> None:
    """Ensure ``[section] key = value`` exists in a marimo.toml (line-based)."""
    if dry_run or not cfg.is_file():
        return
    text = cfg.read_text(encoding="utf-8")
    header = f"[{section}]"
    # Quoted string values
    desired = f'{key} = "{value}"' if not value.startswith("[") else f"{key} = {value}"
    lines = text.splitlines()
    section_idx: int | None = None
    next_section_idx: int | None = None
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == header:
            section_idx = i
        elif section_idx is not None and stripped.startswith("[") and stripped.endswith("]"):
            next_section_idx = i
            break
    if section_idx is not None:
        section_end = next_section_idx if next_section_idx is not None else len(lines)
        for i in range(section_idx + 1, section_end):
            left = lines[i].split("=", 1)[0].strip() if "=" in lines[i] else ""
            if left == key:
                if force or not lines[i].split("=", 1)[1].strip().strip("'\""):
                    lines[i] = desired
                    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
        lines.insert(section_idx + 1, desired)
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    sep = "\n\n" if text.rstrip() else ""
    cfg.write_text(f"{text.rstrip()}{sep}{header}\n{desired}\n", encoding="utf-8")


def _ensure_marimo_runtime_dotenv(cfg: Path, dotenv: Path, *, dry_run: bool) -> None:
    """Point marimo [runtime].dotenv at the shared AstroAI secrets file."""
    _ensure_marimo_toml_kv(
        cfg,
        "runtime",
        "dotenv",
        f'["{dotenv}"]',
        dry_run=dry_run,
        force=True,
    )


def _ensure_marimo_package_manager(cfg: Path, *, dry_run: bool, manager: str = "pixi") -> None:
    """Prefer pixi/uv over pip — CANFAR sessions cannot pip-install into the image Python."""
    if dry_run or not cfg.is_file():
        return
    force = True
    try:
        from astroai_lab.utils.toml_compat import tomllib

        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        current = _toml_get(data, "package_management", "manager")
        if isinstance(current, str) and current.strip() and current.strip() not in ("pip",):
            force = False  # keep explicit uv/poetry/rye/pixi
    except Exception:  # noqa: BLE001
        force = True
    _ensure_marimo_toml_kv(
        cfg,
        "package_management",
        "manager",
        manager,
        dry_run=dry_run,
        force=force,
    )


def _merge_marimo_openrouter(cfg: Path, *, force: bool, dry_run: bool) -> None:
    """Ensure ~/.marimo.toml has OpenRouter AI config from the shared key store.

    Merge strategy (never overwrites user settings outside the AI sections):
    1. If file missing, copy template from bundle data.
    2. Discover key (env → ~/.astroai/lab/.env → existing marimo.toml).
    3. Persist to shared .env so agents share one key.
    4. Inject api_key under [ai.openrouter] when missing (or force).
    5. Point [runtime].dotenv at the shared .env.
    6. Default [package_management] manager to pixi (not pip — no root on CANFAR).
    """
    root = bundle_root()
    template = root / "marimo" / "marimo.toml"
    home = Path.home()

    if not cfg.is_file() and not dry_run:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if template.is_file():
            shutil.copy2(template, cfg)
        else:
            cfg.write_text(
                "# Marimo AI assistant — astroai agent setup\n\n"
                "[package_management]\n"
                'manager = "pixi"\n\n'
                "[ai.openrouter]\n"
                'base_url = "https://openrouter.ai/api/v1"\n',
                encoding="utf-8",
            )

    key = ensure_openrouter_dotenv(home, dry_run=dry_run)
    dotenv = openrouter_dotenv_path(home)
    if cfg.is_file():
        _ensure_marimo_runtime_dotenv(cfg, dotenv, dry_run=dry_run)
        _ensure_marimo_package_manager(cfg, dry_run=dry_run)

    if not key:
        return

    # Check if api_key is already set (TOML parse) before line-merging.
    text = cfg.read_text(encoding="utf-8")
    from astroai_lab.utils.toml_compat import tomllib

    try:
        data = tomllib.loads(text)
        current_key = _toml_get(data, "ai", "openrouter", "api_key")
        if current_key and not force:
            _ensure_marimo_runtime_dotenv(cfg, dotenv, dry_run=dry_run)
            _ensure_marimo_package_manager(cfg, dry_run=dry_run)
            return
    except Exception:  # noqa: BLE001 — unparseable TOML falls through to line-based merge
        pass

    if dry_run:
        return

    # Line-based merge: find [ai.openrouter] section and insert/update api_key
    section_header = "[ai.openrouter]"
    lines = text.splitlines()
    section_idx: int | None = None
    next_section_idx: int | None = None

    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == section_header:
            section_idx = i
        elif section_idx is not None and stripped.startswith("[") and stripped.endswith("]"):
            next_section_idx = i
            break

    if section_idx is not None:
        # Section exists — look for existing api_key line within the section
        section_end = next_section_idx if next_section_idx is not None else len(lines)
        api_key_idx: int | None = None
        for i in range(section_idx + 1, section_end):
            if lines[i].strip().startswith("api_key"):
                api_key_idx = i
                break

        if api_key_idx is not None:
            if force:
                indent = len(lines[api_key_idx]) - len(lines[api_key_idx].lstrip())
                lines[api_key_idx] = " " * indent + f'api_key = "{key}"'
                cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
                _ensure_marimo_runtime_dotenv(cfg, dotenv, dry_run=False)
                _ensure_marimo_package_manager(cfg, dry_run=False)
            return

        # No api_key yet — insert right after the section header line
        lines.insert(section_idx + 1, f'api_key = "{key}"')
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        # Section missing — append at end
        sep = "\n\n" if text.rstrip() else "\n"
        cfg.write_text(
            f'{text.rstrip()}{sep}[ai.openrouter]\napi_key = "{key}"\n',
            encoding="utf-8",
        )
    _ensure_marimo_runtime_dotenv(cfg, dotenv, dry_run=False)
    _ensure_marimo_package_manager(cfg, dry_run=False)


def install_goose_config(root: Path, home: Path, *, force: bool, dry_run: bool) -> None:
    src = root / "goose" / "extensions.yaml"
    dst = home / ".config" / "goose" / "config.yaml"
    if dst.is_file() and not force:
        return
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(f"# AstroAI lab — run: goose configure\n{src.read_text()}", encoding="utf-8")


def default_bundle_names(root: Path) -> list[str]:
    manifest = root / "manifest.json"
    if manifest.is_file():
        data = read_json(manifest)
        return list(data.get("bundles", {}).get("all", {}).get("includes", []))
    return ["cursor", "claude", "opencode", "goose", "codex", "copilot", "cli"]


def run_bundle(
    name: str,
    root: Path,
    home: Path,
    project_dir: Path | None,
    *,
    force: bool,
    dry_run: bool,
) -> None:
    if name == "cursor":
        merge_mcp_servers(
            root / "cursor" / "mcp.json",
            home / ".cursor" / "mcp.json",
            force=force,
            dry_run=dry_run,
        )
        install_tree(
            root / "cursor" / "rules",
            home / ".cursor" / "rules",
            force=force,
            dry_run=dry_run,
        )
        # Skills: npx skills add astroai/canfar-skills (not managed by AstroAI).
    elif name == "claude":
        merge_claude_json(
            root / "claude" / "mcp.json",
            home / ".claude.json",
            force=force,
            dry_run=dry_run,
        )
        install_file(
            root / "claude" / "settings.json",
            home / ".claude" / "settings.json",
            force=force,
            dry_run=dry_run,
        )
    elif name == "opencode":
        merge_opencode_mcp(
            root / "opencode" / "opencode.json",
            home / ".config" / "opencode" / "opencode.json",
            force=force,
            dry_run=dry_run,
        )
    elif name == "goose":
        install_goose_config(root, home, force=force, dry_run=dry_run)
        install_file(
            root / "goose" / "goosehints",
            home / ".config" / "goose" / ".goosehints",
            force=force,
            dry_run=dry_run,
        )
    elif name == "kilo":
        install_file(
            root / "kilo" / "kilo.jsonc",
            home / ".config" / "kilo" / "kilo.jsonc",
            force=force,
            dry_run=dry_run,
        )
    elif name == "cline":
        install_file(
            root / "cline" / "cline-notes.md",
            home / ".config" / "cline" / "cline-notes.md",
            force=force,
            dry_run=dry_run,
        )
    elif name == "codewhale":
        install_file(
            root / "codewhale" / "config.toml",
            home / ".codewhale" / "config.toml",
            force=force,
            dry_run=dry_run,
        )
    elif name == "pi":
        install_file(
            root / "pi" / "settings.json",
            home / ".pi" / "agent" / "settings.json",
            force=force,
            dry_run=dry_run,
        )
        ensure_pi_openrouter_auth(home, dry_run=dry_run, force=force)
    elif name in ("omp", "freebuff", "qoder", "swival", "zcode"):
        install_file(
            root / name / "astroai-notes.md",
            home / ".config" / name / "astroai-notes.md",
            force=force,
            dry_run=dry_run,
        )
    elif name == "codex":
        install_file(
            root / "codex" / "config.toml",
            home / ".codex" / "config.toml",
            force=force,
            dry_run=dry_run,
        )
    elif name == "copilot":
        merge_mcp_servers(
            root / "copilot" / "mcp-config.json",
            home / ".copilot" / "mcp-config.json",
            force=force,
            dry_run=dry_run,
        )
    elif name == "cli":
        install_file(
            root / "cli" / "agent-tools.sh",
            home / ".astroai" / "lab" / "agent-tools-reminder.sh",
            force=force,
            dry_run=dry_run,
        )
        ensure_openrouter_dotenv(home, dry_run=dry_run)
        hook = home / ".astroai" / "lab" / "agent-env.sh"
        if (force or not hook.is_file()) and not dry_run:
            from astroai_lab.utils.json_utils import atomic_write_text

            # ensure_openrouter_dotenv may have already created the hook with
            # the dotenv source block; only write the gh stub when still missing.
            if not hook.is_file():
                atomic_write_text(
                    hook,
                    f"{_OPENROUTER_DOTENV_MARKER}\n"
                    '_astroai_dotenv="${HOME}/.astroai/lab/.env"\n'
                    'if [[ -f "${_astroai_dotenv}" ]]; then\n'
                    "  set -a\n"
                    "  # shellcheck disable=SC1090\n"
                    '  source "${_astroai_dotenv}"\n'
                    "  set +a\n"
                    "fi\n"
                    "\n# AstroAI lab agent setup: GitHub token for gh + GitHub MCP\n"
                    "if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then\n"
                    '  export GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"\n'
                    "fi\n",
                )
            elif "GITHUB_TOKEN" not in hook.read_text(encoding="utf-8"):
                with hook.open("a", encoding="utf-8") as fh:
                    fh.write(
                        "\n# AstroAI lab agent setup: GitHub token for gh + GitHub MCP\n"
                        "if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then\n"
                        '  export GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"\n'
                        "fi\n"
                    )
        bashrc = home / ".bashrc"
        marker = "# astroai agent setup"
        source_line = (
            '[[ -f "${HOME}/.astroai/lab/agent-env.sh" ]] '
            '&& source "${HOME}/.astroai/lab/agent-env.sh"'
        )
        if bashrc.exists() and not dry_run:
            text = bashrc.read_text(encoding="utf-8")
            if source_line not in text:
                with bashrc.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n{marker}\n{source_line}\n")
    elif name == "marimo":
        _merge_marimo_openrouter(
            home / ".marimo.toml",
            force=force,
            dry_run=dry_run,
        )
    elif name == "project":
        if project_dir is None:
            raise LabError("Project directory required.", hint="astroai agent setup --project")
        merge_mcp_servers(
            root / "project" / ".cursor" / "mcp.json",
            project_dir / ".cursor" / "mcp.json",
            force=force,
            dry_run=dry_run,
        )
        install_tree(
            root / "project" / ".cursor" / "rules",
            project_dir / ".cursor" / "rules",
            force=force,
            dry_run=dry_run,
        )
        install_file(
            root / "project" / "AGENTS.md",
            project_dir / "AGENTS.md",
            force=force,
            dry_run=dry_run,
        )
        install_file(
            root / "goose" / "goosehints",
            project_dir / ".goosehints",
            force=force,
            dry_run=dry_run,
        )
    else:
        raise LabError(f"Unknown setup name: {name}", hint="astroai agent setup --help")


def ensure_agent_dirs(home: Path, *, dry_run: bool) -> None:
    dirs = [
        home / ".cursor" / "rules",
        home / ".cursor" / "skills",
        home / ".config" / "goose",
        home / ".config" / "opencode",
        home / ".config" / "kilo",
        home / ".codex",
        home / ".copilot",
        home / ".claude",
        home / ".config" / "canfar" / "lab",
        home / ".astroai" / "lab",
        home / ".cache" / "astroai-lab",
    ]
    if dry_run:
        return
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def relocate_agent_runtime_state(home: Path, *, dry_run: bool) -> list[str]:
    """Move agent runtime stores (DBs, transcripts) onto the session scratch.

    Two sessions share /arc/home; concurrent SQLite/append-heavy stores there
    contend or corrupt (NFS flock is unreliable). Config stays on $HOME;
    runtime goes to scratch via symlinks. See astroai_lab.core.home_layout.
    """
    from astroai_lab.core.home_layout import relocate_agent_runtime
    from astroai_lab.shell.session_env import resolve_session_env

    try:
        data_root = resolve_session_env(ensure=False).xdg_data_home
    except Exception:  # noqa: BLE001 — relocation must never block setup
        return []
    return relocate_agent_runtime(home, data_root, dry_run=dry_run)


def write_stamp(home: Path, mode: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    from astroai_lab.agent.setup_state import record_setup_ok

    record_setup_ok(home, mode=mode)


@dataclass(frozen=True)
class SetupResult:
    """Structured result for agent setup (wizard / --json)."""

    ok: bool
    partial: bool
    mode: str
    actions: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    stamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "partial": self.partial,
            "mode": self.mode,
            "actions": list(self.actions),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stamp": self.stamp,
        }

    @property
    def exit_code(self) -> int:
        if self.ok and not self.partial:
            return 0
        if self.partial or self.actions:
            return 2
        return 1


def agent_setup(
    *,
    mode: str = "install",
    bundles: list[str] | None = None,
    project_dir: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    use_lock: bool = True,
    verify: bool = True,
) -> SetupResult:
    from astroai_lab.agent.setup_state import (
        agent_setup_lock,
        record_setup_failed,
        record_setup_ok,
        stamp_path,
    )
    from astroai_lab.core.paths import quota_used_pct

    root = bundle_root()
    home = Path.home()
    names = bundles or default_bundle_names(root)
    if mode == "project":
        names = ["project"]
        verify = False  # project mode does not install home agent configs

    warnings: list[str] = []
    quota = quota_used_pct(home)
    if quota is not None and quota >= 98 and not force and not dry_run:
        raise LabError(
            f"Home quota {quota}% — refusing agent setup",
            hint="Free space under /arc/home (caches, old envs) or pass --force",
        )
    if quota is not None and quota >= 90:
        warnings.append(f"Home quota {quota}% — agent configs may fill /arc/home")

    def _run() -> SetupResult:
        ensure_agent_dirs(home, dry_run=dry_run)
        if mode != "project":
            warnings.extend(
                f"runtime: {a}" for a in relocate_agent_runtime_state(home, dry_run=dry_run)
            )
        succeeded: list[str] = []
        failed: list[tuple[str, str]] = []
        for name in names:
            try:
                run_bundle(name, root, home, project_dir, force=force, dry_run=dry_run)
                succeeded.append(name)
            except Exception as exc:  # noqa: BLE001 — partial success
                failed.append((name, str(exc)))

        actions = [f"bundle:{n}" for n in succeeded]
        errors = [f"{n}: {e}" for n, e in failed]
        partial = bool(succeeded) and bool(failed)
        ok = bool(succeeded) and not failed

        if ok and mode != "project":
            from astroai_lab.agent import plugins as agent_plugins

            for result in agent_plugins.apply_default_plugins(
                home=home,
                force=force,
                dry_run=dry_run,
                installed_only=False,
                assume_locked=use_lock and not dry_run,
            ):
                if result.status == "failed":
                    errors.append(f"plugin {result.plugin} ({result.agent}): {result.detail}")
                    partial = True
                    ok = False
                elif result.status in ("installed", "updated", "would_install"):
                    actions.append(
                        f"plugin {result.status.replace('_', ' ')} {result.plugin} ({result.agent})"
                    )

        if not dry_run and ok and verify and mode != "project":
            issues = verify_setup(home)
            if issues:
                detail = "verify: " + "; ".join(issues)[:480]
                errors.append(detail)
                partial = True
                ok = False

        if not dry_run and mode != "project":
            if ok:
                record_setup_ok(home, mode=mode)
            else:
                detail = "; ".join(errors) if errors else "no bundles succeeded"
                from datetime import datetime, timezone

                from astroai_lab.agent.setup_state import lab_state_dir

                state = lab_state_dir(home)
                state.mkdir(parents=True, exist_ok=True)
                ver = "unknown"
                version_file = root / "VERSION"
                if version_file.is_file():
                    ver = version_file.read_text(encoding="utf-8").strip()
                stamp_path(home).write_text(
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    + f" bundle={ver} mode={mode}\n",
                    encoding="utf-8",
                )
                record_setup_failed(
                    home,
                    exit_code=2 if (partial or succeeded) else 1,
                    detail=detail[:500],
                )

        stamp = None
        sp = stamp_path(home)
        if sp.is_file():
            stamp = sp.read_text(encoding="utf-8").strip()

        return SetupResult(
            ok=ok,
            partial=partial,
            mode=mode,
            actions=tuple(actions),
            errors=tuple(errors),
            warnings=tuple(warnings),
            stamp=stamp,
        )

    if dry_run or not use_lock:
        return _run()
    with agent_setup_lock(home):
        return _run()


def agent_sync(*, dry_run: bool = False) -> None:
    """Refresh bundled agent MCP, rules, and configs."""
    from astroai_lab.agent.setup_state import (
        agent_setup_lock,
        record_setup_ok,
    )

    root = bundle_root()
    home = Path.home()
    names = default_bundle_names(root)

    def _run() -> None:
        ensure_agent_dirs(home, dry_run=dry_run)
        for name in names:
            run_bundle(name, root, home, None, force=True, dry_run=dry_run)
        if not dry_run:
            record_setup_ok(home, mode="sync")

    if dry_run:
        _run()
        return
    with agent_setup_lock(home):
        _run()


def agent_verify() -> None:
    issues = verify_setup(Path.home())
    if issues:
        raise LabError("Agent setup incomplete:\n  " + "\n  ".join(issues))


def agent_list_bundles() -> dict[str, str]:
    return list_bundles()
