"""Install, remove, set up, and update registry agents."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from canfar_lab.agent.agent_targets import AGENT_SKILL_DIRS, expand_home
from canfar_lab.agent.bundle_path import bundle_root
from canfar_lab.agent.registry_probe import (
    agent_config_file,
    registry_agent_status,
)
from canfar_lab.agent.registry_store import get_registry_agent, load_registry
from canfar_lab.errors import LabError


def _install_npm(agent: dict[str, Any]) -> str:
    from canfar_lab.agent.install import (
        _link_into_local_bin,
        _npm_prefix,
        _require,
        _verify_cmd,
        npm_global_install_cmd,
        npm_install_environ,
        run,
    )
    from canfar_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    binary = agent["binary"]
    _require("npm")
    run(
        npm_global_install_cmd(_npm_prefix(), str(agent["install"]["source"])),
        env=npm_install_environ(),
        timeout=INSTALL_TIMEOUT_SEC,
    )
    bin_path = _npm_prefix() / "bin" / binary
    _link_into_local_bin(bin_path, binary)
    _verify_cmd(binary, extra_paths=[bin_path])
    return binary


def _install_curl(agent: dict[str, Any]) -> str:
    from canfar_lab.agent.install import (
        _bin_dir,
        _curl_pipe_bash,
        _link_into_local_bin,
        _verify_cmd,
        find_curl_binary,
    )

    binary = str(agent["binary"])
    # Registry installs can pass installer-specific env (e.g. XDG_BIN_DIR,
    # GOOSE_BIN_DIR) with a {bin_dir} token expanded to the session bin dir.
    env = {
        k: str(v).replace("{bin_dir}", str(_bin_dir()))
        for k, v in (agent["install"].get("env") or {}).items()
    }
    raw_args = agent["install"].get("args") or []
    script_args = [str(a) for a in raw_args]
    _curl_pipe_bash(str(agent["install"]["source"]), env=env or None, args=script_args or None)
    extra = [Path(p).expanduser() for p in agent["install"].get("post_binary_paths", [])]
    found = find_curl_binary(binary, extra=extra)
    if found is None:
        raise LabError(
            f"{binary} not found after install — open a new shell",
            hint="Check the installer output; binary should land under "
            "$SCRATCH/.local/bin or the agent's own bin dir",
        )
    _link_into_local_bin(found, binary)
    _verify_cmd(binary, extra_paths=extra)
    return binary


def _install_uv_tool(agent: dict[str, Any]) -> str:
    from canfar_lab.agent.install import _require, _session_environ, _verify_cmd, run
    from canfar_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    binary = agent["binary"]
    _require("uv")
    run(
        ["uv", "tool", "install", "--force", str(agent["install"]["source"])],
        env=_session_environ(),
        timeout=INSTALL_TIMEOUT_SEC,
    )
    _verify_cmd(binary)
    from canfar_lab.agent.install import clear_legacy_scratch_binary

    clear_legacy_scratch_binary(str(binary))
    return binary


def _install_gh_release(agent: dict[str, Any]) -> str:
    import platform

    from canfar_lab.agent.install import _gh_release_bin, _verify_cmd, clear_legacy_scratch_binary

    binary = agent["binary"]
    install = agent["install"]
    # {arch} templates to platform.machine() (x86_64/aarch64) for per-arch assets.
    asset = str(install["asset"]).replace("{arch}", platform.machine())
    _gh_release_bin(
        str(install["repo"]),
        asset,
        binary,
        requires_gh_auth=bool(install.get("requires_gh_auth")),
    )
    _verify_cmd(binary)
    clear_legacy_scratch_binary(str(binary))
    return binary


def install_registry_agent(agent_id: str, *, dry_run: bool = False) -> str:
    """Install a registered agent by id, dispatching on install.method.

    Registered agents that already exist in ``install.TOOLS`` (hermes, openclaw,
    cursor) keep their battle-tested installer via ``install_tool``; future
    registry-only agents dispatch by method here.
    """
    from canfar_lab.agent import registry as registry_mod

    agent = registry_mod.get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="canfar lab agent list")

    from canfar_lab.agent.install import (
        TOOLS,
        install_tool,
        refuse_if_home_owned,
    )
    from canfar_lab.core.home_layout import ensure_agent_runtime_on_scratch

    refuse_if_home_owned(agent_id)
    if not dry_run:
        ensure_agent_runtime_on_scratch(Path.home(), dry_run=False)

    if agent_id in TOOLS:
        install_tool(agent_id, dry_run=dry_run)
        return agent_id

    if dry_run:
        return agent_id

    from canfar_lab.agent.setup_state import agent_setup_lock

    with agent_setup_lock():
        method = agent["install"]["method"]
        if method == "npm":
            return registry_mod._install_npm(agent)
        if method == "curl":
            return registry_mod._install_curl(agent)
        if method == "uv-tool":
            return registry_mod._install_uv_tool(agent)
        if method == "gh-release":
            return registry_mod._install_gh_release(agent)
        raise LabError(f"Agent {agent_id} has unsupported install.method={method!r}")


def remove_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    purge: bool = False,
    clean_home: bool = False,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Uninstall a registered agent by id (Phase 2 `agent remove`).

    Agents that exist in ``install.TOOLS`` (hermes, openclaw, cursor) keep their
    battle-tested uninstaller via ``install.uninstall_tool``; registry-only
    agents are removed by install method here. Returns result dicts for JSON.
    """
    from canfar_lab.agent import registry as registry_mod

    agent = registry_mod.get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="canfar lab agent list")

    from canfar_lab.agent.install import (
        TOOLS,
        clear_legacy_scratch_binary,
        home_bin_candidates,
        uninstall_tool,
    )

    if agent_id in TOOLS:
        results = uninstall_tool(
            agent_id, home=home, purge=purge, clean_home=clean_home, dry_run=dry_run
        )
        return [r.__dict__ for r in results]

    from canfar_lab.agent.setup_state import agent_setup_lock

    with agent_setup_lock(home):
        results = _remove_registry_method(agent, home=home, purge=purge, dry_run=dry_run)
        home = home or Path.home()
        binary = str(agent["binary"])
        from canfar_lab.agent.install import RemoveResult, _remove_file

        if clean_home:
            for home_bin in home_bin_candidates(binary, home=home):
                result = _remove_file(home_bin, f"home-binary:{binary}", dry_run=dry_run)
                if result:
                    results.append(result.__dict__ if isinstance(result, RemoveResult) else result)
            if not dry_run:
                clear_legacy_scratch_binary(binary)
        return results


def _remove_registry_method(
    agent: dict[str, Any],
    *,
    home: Path | None,
    purge: bool,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Method-based removal for registry agents not present in install.TOOLS."""
    import shutil
    import subprocess

    from canfar_lab.agent.install import (
        RemoveResult,
        _bin_dir,
        _npm_prefix,
        _session_environ,
    )
    from canfar_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    home = home or Path.home()
    agent_id = agent["id"]
    binary = str(agent["binary"])
    method = agent["install"]["method"]
    results: list[dict[str, Any]] = []

    def rm(path: Path, target: str) -> None:
        if not (path.exists() or path.is_symlink()):
            return
        if dry_run:
            results.append(RemoveResult(target, "would_remove", str(path)).__dict__)
        else:
            try:
                path.unlink(missing_ok=True)
                results.append(RemoveResult(target, "removed", str(path)).__dict__)
            except OSError as exc:
                results.append(RemoveResult(target, "error", str(exc)).__dict__)

    def rm_tree(path: Path, target: str) -> None:
        if not path.exists():
            return
        if dry_run:
            results.append(RemoveResult(target, "would_remove", str(path)).__dict__)
        else:
            try:
                shutil.rmtree(path)
                results.append(RemoveResult(target, "removed", str(path)).__dict__)
            except OSError as exc:
                results.append(RemoveResult(target, "error", str(exc)).__dict__)

    # npm-installed: best-effort `npm uninstall -g`, then drop bin links.
    if method == "npm":
        pkg = re.sub(r"@[^@]*$", "", str(agent["install"].get("source", binary)))
        if not dry_run and shutil.which("npm"):
            from canfar_lab.agent.install import run

            with contextlib.suppress(LabError, subprocess.CalledProcessError, OSError):
                run(
                    ["npm", "uninstall", "-g", "--prefix", str(_npm_prefix()), pkg],
                    env=_session_environ(),
                    timeout=INSTALL_TIMEOUT_SEC,
                    quiet=True,  # keep stdout clean for `--json agent remove/wipe`
                )
        rm(_npm_prefix() / "bin" / binary, f"binary:{binary}")

    # curl / gh-release / uv-tool drop a managed binary.
    rm(_bin_dir() / binary, f"binary:{binary}")

    # Config file (registry config.path).
    cfg = agent_config_file(agent, home)
    if cfg is not None:
        rm(cfg, f"config:{cfg}")
        lab_dir = home / ".astroai" / "lab"
        if purge and cfg.parent not in {home, lab_dir}:
            rm_tree(cfg.parent, f"purge:{cfg.parent}")
        if str(agent_id) == "hermes":
            legacy = home / ".hermes" / "config.yaml"
            if legacy != cfg:
                rm(legacy, f"config:{legacy}")

    # Plugin-applied files (Phase 3 recursive removal). Run the precise
    # plugin sweep first so installed plugins report `removed` (not `skipped`),
    # then a broad sweep of ~/.<id>/skills catches any non-plugin skills.
    from canfar_lab.agent import plugins as agent_plugins

    for row in agent_plugins.remove_agent_plugin_files(agent_id, home=home, dry_run=dry_run):
        results.append(row)
    rm_tree(home / f".{agent_id}" / "skills", f"plugins:{agent_id}")

    # Setup state stamps.
    from canfar_lab.agent.setup_state import failed_path, stamp_path

    rm(stamp_path(home), "state:stamp")
    rm(failed_path(home), "state:failed")

    return results


# ---------------------------------------------------------------------------
# Registry-driven setup / update (Phase 2 `agent setup <id>` + `agent update <id>`)
# ---------------------------------------------------------------------------


def list_installed_registry_agents(home: Path | None = None) -> list[dict[str, Any]]:
    """Registry agents with a CLI on PATH (managed, home, or other)."""
    home = home or Path.home()
    return [a for a in load_registry() if registry_agent_status(a, home)["binary_ok"]]


def _config_scaffold(agent: dict[str, Any]) -> str:
    """Minimal scaffold for a missing registry ``config.path``.

    JSON5/JSONC get a ``//`` comment header (JSONC/JSON5 do not support ``#``
    comments — parse_jsonc only strips ``//`` and ``/* */``); strict JSON gets
    a header-free body; YAML/TOML/markdown get ``#`` headers. All bodies parse
    to an empty mapping / empty file respectively.
    """
    fmt = str((agent.get("config") or {}).get("format", "json"))
    name = agent.get("name", agent["id"])
    header = f"# {name} — scaffolded by `canfar lab agent setup {agent['id']}`\n"
    # Muse Code fails every command if settings.json omits schema_version.
    if agent.get("id") == "muse" and fmt == "json":
        return '{"schema_version": 1}\n'
    if fmt == "json":
        return "{}\n"
    if fmt in ("jsonc", "json5"):
        return f"// {name} — scaffolded by `canfar lab agent setup {agent['id']}`\n{{}}\n"
    if fmt == "yaml":
        return header + "{}\n"
    # toml / markdown / unknown: comment-only body stays valid.
    return header + "\n"


# Cold uvx/npx MCP startup on CANFAR (NFS home + first download) often exceeds
# Codex's 30s default — bump known slow servers without clobbering larger values.
_CODEX_MCP_STARTUP_TIMEOUT_SEC = 120
_CODEX_MCP_TIMEOUT_KEYS = (
    "mcp_servers.fetch.startup_timeout_sec",
    "mcp_servers.memory.startup_timeout_sec",
    "mcp_servers.github.startup_timeout_sec",
)


def _ensure_codex_mcp_timeouts(cfg: Path, *, home: Path, dry_run: bool) -> str | None:
    """Ensure Codex MCP servers have a long enough startup_timeout_sec.

    Returns an action string when something would change / changed, else None.
    """
    from canfar_lab.agent import agent_config as agent_config_mod
    from canfar_lab.utils.toml_compat import tomllib

    text = cfg.read_text(encoding="utf-8", errors="replace")
    try:
        data = tomllib.loads(text)
    except Exception:  # noqa: BLE001 — leave broken configs to the repair path
        return None
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None

    needed: dict[str, int] = {}
    for dotted in _CODEX_MCP_TIMEOUT_KEYS:
        parts = dotted.split(".")
        if len(parts) != 3:
            continue
        server = servers.get(parts[1])
        if not isinstance(server, dict):
            continue
        current = server.get("startup_timeout_sec")
        if isinstance(current, (int, float)) and int(current) >= _CODEX_MCP_STARTUP_TIMEOUT_SEC:
            continue
        needed[dotted] = _CODEX_MCP_STARTUP_TIMEOUT_SEC
    if not needed:
        return None
    labels = ", ".join(sorted(k.split(".")[1] for k in needed))
    if dry_run:
        return f"would set Codex MCP startup_timeout_sec ({labels})"
    agent_config_mod.edit_agent_config("codex", home=home, set_items=needed)
    return f"set Codex MCP startup_timeout_sec ({labels})"


def _run_post_install(command: str) -> None:
    """Run a ``setup.post_install`` shell command (interactive agents only)."""
    import subprocess

    from canfar_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    try:
        proc = subprocess.run(command, shell=True, timeout=INSTALL_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        raise LabError(
            f"post_install timed out after {INSTALL_TIMEOUT_SEC}s",
            hint="Re-run with a higher CANFAR_LAB_AGENT_INSTALL_TIMEOUT",
        ) from exc
    if proc.returncode != 0:
        raise LabError(f"post_install exited {proc.returncode}: {command}")


def setup_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    post_install: bool = False,
) -> dict[str, Any]:
    """Write configs, skills, and MCP for one registered agent.

    1. Apply the manifest config bundle when one shares this agent id (real
       starter templates — must run *before* any empty scaffold).
    2. Scaffold the declared config file when still missing (never clobber).
    3. Create the agent's skills dir (AGENT_SKILL_DIRS, when declared).
    4. Re-apply every plugin whose support matrix includes this agent.
    5. Optionally run ``setup.post_install`` (interactive, opt-in).
    6. Record the setup stamp (mode=setup:<id>).

    Returns ``{ok, partial, agent, actions, errors}`` (human-readable action
    strings) for JSON output.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="canfar lab agent list")
    home = home or Path.home()
    actions: list[str] = []
    errors: list[str] = []

    from canfar_lab.agent.inventory import list_bundles
    from canfar_lab.agent.setup import run_bundle
    from canfar_lab.core.home_layout import ensure_agent_runtime_on_scratch

    # Scratch layout first so config scaffolds and plugins write off /arc.
    for label in ensure_agent_runtime_on_scratch(home, dry_run=dry_run):
        actions.append(f"runtime: {label}")

    # Bundle first when one exists — otherwise an empty scaffold would block
    # install_file() from writing the real starter template (new-user footgun).
    if agent_id in list_bundles():
        if dry_run:
            actions.append(f"would apply config bundle ({agent_id})")
        else:
            run_bundle(
                agent_id,
                bundle_root(),
                home,
                None,
                force=force,
                dry_run=False,
            )
            actions.append(f"applied config bundle ({agent_id})")

    cfg = agent_config_file(agent, home)
    if cfg is not None:
        if cfg.is_file():
            actions.append(f"config exists ({cfg})")
        elif dry_run:
            actions.append(f"would create config ({cfg})")
        else:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(_config_scaffold(agent), encoding="utf-8")
            actions.append(f"created config ({cfg})")
            # Mirror onto ~/.hermes when HERMES_HOME is scratch so legacy
            # paths and seed_hermes_home stay consistent.
            if agent_id == "hermes":
                legacy = home / ".hermes" / "config.yaml"
                if cfg.resolve() != legacy.resolve() and not legacy.is_file():
                    legacy.parent.mkdir(parents=True, exist_ok=True)
                    import shutil

                    shutil.copy2(cfg, legacy)

    rel = AGENT_SKILL_DIRS.get(agent_id)
    if rel:
        skills_dir = home / rel
        if skills_dir.is_dir():
            actions.append(f"skills dir present ({skills_dir})")
        elif dry_run:
            actions.append(f"would create skills dir ({skills_dir})")
        else:
            skills_dir.mkdir(parents=True, exist_ok=True)
            actions.append(f"created skills dir ({skills_dir})")

    if agent_id == "codex":
        cfg = expand_home("~/.codex/config.toml", home)
        if cfg.is_file():
            patched = _ensure_codex_mcp_timeouts(cfg, home=home, dry_run=dry_run)
            if patched:
                actions.append(patched)

    from canfar_lab.agent import plugins as agent_plugins

    for result in agent_plugins.apply_agent_plugins(
        agent_id, home=home, force=force, dry_run=dry_run, defaults_only=True
    ):
        if result.status == "failed":
            errors.append(f"plugin {result.plugin} ({result.agent}): {result.detail}")
        elif result.status in ("installed", "would_install", "updated"):
            actions.append(
                f"plugin {result.status.replace('_', ' ')} {result.plugin} ({result.agent})"
            )

    post = (agent.get("setup") or {}).get("post_install")
    if post and post_install:
        if dry_run:
            actions.append(f"would run post-install ({post})")
        else:
            try:
                _run_post_install(str(post))
                actions.append(f"ran post-install ({post})")
            except LabError as exc:
                errors.append(f"post-install: {exc}")

    ok = not errors
    if not dry_run and ok:
        from canfar_lab.agent.setup_state import record_setup_ok

        record_setup_ok(home, mode=f"setup:{agent_id}")
    return {
        "ok": ok,
        "partial": bool(actions) and bool(errors),
        "agent": agent_id,
        "actions": actions,
        "errors": errors,
    }


def update_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    force_reinstall: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Refresh one registered agent's CLI and configs.

    1. Refresh the CLI binary (install if missing, or always with --reinstall).
    2. Force re-apply every plugin supporting this agent (skills/MCP/config).
    3. Refresh the setup stamp (mode=update:<id>).

    Returns ``{ok, partial, agent, actions, errors}`` for JSON output.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="canfar lab agent list")
    home = home or Path.home()
    actions: list[str] = []
    errors: list[str] = []

    from canfar_lab.agent.setup_state import agent_setup_lock
    from canfar_lab.core.home_layout import ensure_agent_runtime_on_scratch

    with agent_setup_lock(home):
        # Keep scratch redirects even when the binary is already up-to-date.
        for label in ensure_agent_runtime_on_scratch(home, dry_run=dry_run):
            actions.append(f"runtime: {label}")
        status = registry_agent_status(agent, home)
        if status["binary_ok"] and not force_reinstall:
            actions.append(f"binary up-to-date ({agent_id})")
        else:
            verb = "reinstall" if force_reinstall else "install"
            try:
                from canfar_lab.agent import registry as registry_mod

                registry_mod.install_registry_agent(agent_id, dry_run=dry_run)
                actions.append(f"binary {verb} ({agent_id})")
            except LabError as exc:
                errors.append(f"binary {agent_id}: {exc}")

        from canfar_lab.agent import plugins as agent_plugins

        for result in agent_plugins.apply_agent_plugins(
            agent_id, home=home, force=True, dry_run=dry_run, assume_locked=True
        ):
            if result.status == "failed":
                errors.append(f"plugin {result.plugin} ({result.agent}): {result.detail}")
            elif result.status in ("installed", "would_install", "updated", "removed"):
                actions.append(
                    f"plugin {result.status.replace('_', ' ')} {result.plugin} ({result.agent})"
                )

        ok = not errors
        if not dry_run and ok:
            from canfar_lab.agent.setup_state import record_setup_ok

            record_setup_ok(home, mode=f"update:{agent_id}")
        return {
            "ok": ok,
            "partial": bool(actions) and bool(errors),
            "agent": agent_id,
            "actions": actions,
            "errors": errors,
        }


def fix_registry_agent(
    agent_id: str,
    *,
    home: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Regenerate or sanitize one registered agent's config.

    Regenerate/sanitize ONE registered agent's config from the registry,
    reusing ``fix.py``'s repair pattern (syntax check → reset to a minimal
    valid body) with the format-aware parse from ``agent_config``:

    1. Missing config → scaffold it (format-aware; JSONC/JSON5 keep a ``//``
       header, strict JSON gets ``{}\n``, YAML/TOML/markdown comment bodies).
    2. Present but unparseable → reset to the scaffold (markdown is read-only:
       no repair).
    3. Present + parseable → nothing to fix.
    4. Ensure the agent's skills dir exists (like `agent setup <id>`).
    5. Refresh the setup stamp / clear the failed marker when healthy.

    A repaired (reset) config loses plugin-written entries — run
    `agent update <id>` afterwards to force re-apply the agent's plugins.

    Returns ``{ok, partial, agent, actions, errors}`` for JSON output.
    """
    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="canfar lab agent list")
    home = home or Path.home()
    actions: list[str] = []
    errors: list[str] = []

    config = agent.get("config") or {}
    cfg = agent_config_file(agent, home)
    if cfg is not None:
        fmt = str(config.get("format", "json"))
        if not cfg.is_file():
            if dry_run:
                actions.append(f"would create config ({cfg})")
            else:
                cfg.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_text(_config_scaffold(agent), encoding="utf-8")
                actions.append(f"created config ({cfg})")
        elif fmt == "markdown":
            actions.append(f"config healthy (markdown read-only) ({cfg})")
        else:
            from canfar_lab.agent import agent_config as agent_config_mod

            text = cfg.read_text(encoding="utf-8", errors="replace")
            try:
                agent_config_mod.validate_config_text(agent_id, text, home=home)
            except LabError:
                if dry_run:
                    actions.append(f"would repair broken {fmt} config ({cfg})")
                else:
                    cfg.parent.mkdir(parents=True, exist_ok=True)
                    cfg.write_text(_config_scaffold(agent), encoding="utf-8")
                    actions.append(f"repaired broken {fmt} config ({cfg})")
            else:
                # Parseable — still may need semantic sanitize (OpenCode lsp maps).
                if agent_id == "opencode":
                    from canfar_lab.agent.opencode_config import sanitize_opencode_config
                    from canfar_lab.utils.json_utils import parse_jsonc, write_json

                    parsed = parse_jsonc(text)
                    if isinstance(parsed, dict):
                        cleaned, changes = sanitize_opencode_config(parsed)
                        if changes:
                            if dry_run:
                                actions.append(
                                    "would sanitize OpenCode config: " + "; ".join(changes[:4])
                                )
                            else:
                                write_json(cfg, cleaned)
                                actions.append(
                                    "sanitized OpenCode config: " + "; ".join(changes[:4])
                                )
                        else:
                            actions.append(f"config healthy ({cfg})")
                    else:
                        actions.append(f"config healthy ({cfg})")
                elif agent_id == "codex":
                    patched = _ensure_codex_mcp_timeouts(cfg, home=home, dry_run=dry_run)
                    if patched:
                        actions.append(patched)
                    else:
                        actions.append(f"config healthy ({cfg})")
                else:
                    actions.append(f"config healthy ({cfg})")
    else:
        actions.append("no config declared")

    rel = AGENT_SKILL_DIRS.get(agent_id)
    if rel:
        skills_dir = home / rel
        if skills_dir.is_dir():
            actions.append(f"skills dir present ({skills_dir})")
        elif dry_run:
            actions.append(f"would create skills dir ({skills_dir})")
        else:
            skills_dir.mkdir(parents=True, exist_ok=True)
            actions.append(f"created skills dir ({skills_dir})")

    ok = not errors
    if not dry_run and ok:
        from canfar_lab.agent.setup_state import record_setup_ok

        record_setup_ok(home, mode=f"repair:{agent_id}")
    return {
        "ok": ok,
        "partial": bool(actions) and bool(errors),
        "agent": agent_id,
        "actions": actions,
        "errors": errors,
    }
