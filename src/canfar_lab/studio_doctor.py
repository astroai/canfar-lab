"""Studio doctor and MCP probe."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from canfar_lab.errors import LabError
from canfar_lab.studio_bundles import (
    bundle_installed,
    desired_bundles,
    team_bundles_missing,
)
from canfar_lab.studio_layer import (
    _read_text,
    bundle_basename,
    llm_provider_ids_in_patch,
    mcp_serve_command,
    read_bundles,
)
from canfar_lab.studio_paths import (
    CANFAR_MCP_TOOLS,
    DSH_INSTALL_HINT,
    PROFILE_PATCH_FILENAME,
    STUDIO_PROFILE_NAME,
    TEAM_BUNDLES,
    StateRoot,
    StudioProfile,
    agents_skills_dir,
    dsh_home,
    managed_bench_dir,
    profile_dir,
    resolve_state_root,
)
from canfar_lab.utils.subprocess import run_capture


@dataclass
class Check:
    """One doctor probe: what was checked, what was seen, and what to do."""

    name: str
    ok: bool
    detail: str
    hint: str | None = None
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "hint": self.hint,
            "fatal": self.fatal,
        }


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def dsh_version(dsh_bin: str) -> str | None:
    """``dsh --version``, or None when the binary does not answer."""
    try:
        out = run_capture([dsh_bin, "--version"], timeout=60)
    except LabError:
        return None
    lines = (out or "").strip().splitlines()
    return lines[0] if lines else None


def probe_env() -> dict[str, str]:
    """The environment a Studio session gives the MCP row.

    ``dsh`` merges the row's declared ``env`` over a *scrubbed* ambient one, so a
    probe that inherits this shell's ``PYTHONPATH`` (or ``VIRTUAL_ENV``) would
    answer with the working tree's CLI while the session — which has neither —
    gets whatever the baked path resolves to. That difference is the whole point
    of the check, so the probe scrubs to the same handful of variables.
    """
    keep = ("PATH", "HOME", "USER", "SHELL", "SCRATCH", "PROJECT", "TMPDIR")
    return {name: os.environ[name] for name in keep if os.environ.get(name)}


@dataclass(frozen=True, slots=True)
class McpProbe:
    """What one ``canfar lab mcp serve`` handshake told us about the other end."""

    ok: bool
    server: str = ""
    tools: tuple[str, ...] = ()
    error: str = ""

    @property
    def missing_canfar_tools(self) -> tuple[str, ...]:
        return tuple(name for name in CANFAR_MCP_TOOLS if name not in self.tools)

    def detail(self) -> str:
        if not self.ok:
            return self.error or "no reply"
        text = self.server
        if self.tools:
            text += f" · {len(self.tools)} tool(s)"
            missing = self.missing_canfar_tools
            if missing:
                text += f", missing {', '.join(missing)}"
        return text


def probe_mcp_report(
    command: tuple[str, ...], *, timeout: float = 20.0, env: dict[str, str] | None = None
) -> McpProbe:
    """Handshake ``initialize`` then ``tools/list`` against ``command`` over stdio.

    Reporting the tool inventory is the point: a server that answers but exposes
    only the cluster half of the toolkit is the failure worth catching, so the
    result carries the names rather than a summary string.
    """
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "astroai-studio-doctor", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    try:
        proc = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=probe_env() if env is None else env,
        )
    except OSError as exc:
        return McpProbe(ok=False, error=f"cannot start {command[0]}: {exc}")
    payload = "".join(json.dumps(request) + "\n" for request in requests)
    try:
        stdout, stderr = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return McpProbe(ok=False, error=f"no reply within {timeout:.0f}s")
    info: dict[str, object] = {}
    tools: list[str] = []
    for line in (stdout or "").splitlines():
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = reply.get("result") or {}
        if not info and result.get("serverInfo"):
            info = result["serverInfo"]
        for tool in result.get("tools") or []:
            if tool.get("name"):
                tools.append(str(tool["name"]))
    if info:
        return McpProbe(
            ok=True,
            server=f"{info.get('name')} {info.get('version')}",
            tools=tuple(tools),
        )
    detail_lines = (stderr or "").strip().splitlines()
    error = detail_lines[-1] if detail_lines else "no initialize reply"
    return McpProbe(ok=False, error=error)


def probe_mcp(
    command: tuple[str, ...], *, timeout: float = 20.0, env: dict[str, str] | None = None
) -> tuple[bool, str]:
    """``probe_mcp_report`` as ``(ok, one-line detail)`` for callers that only log."""
    report = probe_mcp_report(command, timeout=timeout, env=env)
    return report.ok, report.detail()


def _skill_count(root: Path) -> int:
    if not root.is_dir():
        return 0
    found = list(root.glob("*/SKILL.md")) + list(root.glob("*.md"))
    return len(found)


def dump_config(
    dsh_bin: str,
    profile: str = STUDIO_PROFILE_NAME,
    *,
    timeout: float = 180.0,
) -> tuple[int, str, str]:
    """``dsh --profile <profile> --dump-config``; returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            [dsh_bin, "--profile", profile, "--dump-config"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def doctor(
    home: Path,
    *,
    profile: StudioProfile,
    port: int = 3080,
    state: StateRoot | None = None,
    dsh_bin: str | None = None,
    probe_handshake: bool = True,
    with_team: bool = True,
) -> dict[str, Any]:
    """Pre-flight every dependency ``canfar lab studio`` has, without booting it.

    Read-only apart from creating the state root. Returns
    ``{checks, ok, fatal, profile, state_root, dsh}``.
    """
    from canfar_lab import studio as studio_mod

    checks: list[Check] = []
    # Same resolution as launch: ASTROAI_STUDIO_DSH, PATH, then image search paths.
    resolved_dsh = dsh_bin or studio_mod.dsh_binary()
    pinned = studio_mod.DSH_VERSION

    if resolved_dsh is None:
        checks.append(
            Check(
                name="dsh",
                ok=False,
                detail="no `dsh` executable found",
                hint=f"{DSH_INSTALL_HINT}  (never `npx -y @deepseek-ai/dsh`: npm swallows "
                "launcher flags)",
                fatal=True,
            )
        )
    else:
        version = dsh_version(resolved_dsh)
        pin_ok = version is not None and pinned in version
        checks.append(
            Check(
                name="dsh",
                ok=version is not None,
                detail=f"{resolved_dsh} {version or '(no --version reply)'}",
                hint=None if version else "the binary exists but does not answer --version",
                fatal=version is None,
            )
        )
        if version is not None:
            checks.append(
                Check(
                    name="dsh-pin",
                    ok=pin_ok,
                    detail=f"want {pinned}" + ("" if pin_ok else f", got {version}"),
                    hint=None
                    if pin_ok
                    else f"{DSH_INSTALL_HINT}  (Studio pin; keep containers Dockerfile in sync)",
                )
            )

    directory = profile_dir(home)
    bundles = read_bundles(directory)
    if not directory.is_dir():
        checks.append(
            Check(
                name="profile",
                ok=False,
                detail=f"{directory} does not exist",
                hint="run `canfar lab studio --prepare`",
                fatal=True,
            )
        )
    else:
        ordered = desired_bundles(bundles, with_team=with_team)
        checks.append(
            Check(
                name="profile",
                ok=True,
                detail=f"{len(bundles)} bundle layers: "
                + " → ".join(bundle_basename(b) for b in bundles),
            )
        )
        checks.append(
            Check(
                name="profile-layer-order",
                ok=bundles == ordered,
                detail="bundle order matches base → web-app → team → astroai extras"
                if bundles == ordered
                else f"expected {[bundle_basename(b) for b in ordered]}",
                hint="`canfar lab studio --prepare` rewrites dsh.profile.bundles in order",
            )
        )
        missing = team_bundles_missing(bundles, with_team=with_team)
        declared = [name for name in TEAM_BUNDLES if name in bundles]
        installed = all(bundle_installed(directory, name) for name in declared)
        if not with_team:
            checks.append(
                Check(
                    name="team-layers",
                    ok=True,
                    detail="disabled by `--no-team` (stock web composition)",
                )
            )
        else:
            checks.append(
                Check(
                    name="team-layers",
                    ok=not missing and installed,
                    detail="Agent Teams host + web layers mounted"
                    if not missing and installed
                    else ("missing from the manifest: " + ", ".join(missing))
                    if missing
                    else "declared but not installed (pnpm has not fetched them)",
                    hint="`dsh plugin --profile astroai add "
                    + " ".join(TEAM_BUNDLES)
                    + "` (needs network + pnpm), or `--no-team`",
                )
            )

    patch = directory / PROFILE_PATCH_FILENAME
    checks.append(
        Check(
            name="layer-content",
            ok=patch.is_file(),
            detail=str(patch) if patch.is_file() else "no patch layer written",
            hint="run `canfar lab studio --prepare`",
        )
    )

    if resolved_dsh is not None and directory.is_dir():
        rc, _out, err = dump_config(resolved_dsh)
        unmatched = [line for line in err.splitlines() if "match" in line.lower()]
        checks.append(
            Check(
                name="composition",
                ok=rc == 0,
                detail="`--dump-config` composed the tree" if rc == 0 else _first_error_line(err),
                hint=None
                if rc == 0
                else (
                    "a declared bundle is not installed (`dsh plugin --profile astroai add …`), "
                    "or a patch row targets a row this dsh no longer ships"
                ),
                fatal=rc != 0,
            )
        )
        if rc == 0 and unmatched:
            checks.append(
                Check(
                    name="patch-targets",
                    ok=False,
                    detail=unmatched[0],
                    hint="a patch row named a row the composition does not have",
                )
            )

    state = state or resolve_state_root(home, profile=profile)
    writable = False
    try:
        state.path.mkdir(parents=True, exist_ok=True)
        writable = os.access(state.path, os.W_OK)
    except OSError:
        writable = False
    checks.append(
        Check(
            name="state-root",
            ok=writable,
            detail=f"{state.path} ({'durable' if state.durable else 'this session only'})",
            hint=state.note,
        )
    )

    checks.append(
        Check(
            name="port",
            ok=_port_free(port),
            detail=f"127.0.0.1:{port} " + ("free" if _port_free(port) else "already in use"),
            hint="pass `--port <n>`; dsh refuses to bind all interfaces by design",
        )
    )

    # Probe what a Studio session will actually run — the command baked into the
    # profile's layer — not whatever `astroai` happens to be on PATH right now.
    command = baked_mcp_command(patch) or mcp_serve_command(home=home)
    if probe_handshake:
        report = probe_mcp_report(command)
        missing = report.missing_canfar_tools
        checks.append(
            Check(
                name="mcp",
                ok=report.ok and not missing,
                detail=f"{' '.join(command)} → {report.detail()}",
                hint=(
                    "job and cluster tools stay unavailable until `canfar lab mcp serve` answers"
                    if not report.ok
                    else "that CLI predates these tools: upgrade it, or point the row at "
                    "a newer one with `canfar lab studio --prepare --mcp-bin <path>`"
                ),
            )
        )
    else:
        checks.append(Check(name="mcp", ok=True, detail="skipped (probe_handshake=False)"))

    bench_skills = managed_bench_dir(home) / "skills"
    agents_skills = agents_skills_dir(home)
    skills = _skill_count(bench_skills) + _skill_count(agents_skills)
    checks.append(
        Check(
            name="skills",
            ok=skills > 0,
            detail=(
                f"{_skill_count(bench_skills)} managed + "
                f"{_skill_count(agents_skills)} user skill(s)"
            ),
            hint="`npx skills add astroai/canfar-skills` installs the CANFAR platform skills",
        )
    )

    available_keys = _available_keys(home)
    routes = sorted({*_settings_routes(home), *_native_routes(available_keys)})
    checks.append(
        Check(
            name="providers",
            ok=bool(routes) or not available_keys,
            detail=", ".join(routes)
            if routes
            else (
                "API keys present but no provider ref in settings.yaml"
                if available_keys
                else "no API key found yet (models chosen in dsh Settings)"
            ),
            hint="`canfar lab studio --prepare` seeds credential refs from support.yaml; "
            "choose provider/model in Settings → Models",
        )
    )
    unserviceable = _unserviceable_routes()
    if unserviceable:
        checks.append(
            Check(
                name="provider-routes",
                ok=False,
                detail="dsh cannot load: " + ", ".join(unserviceable),
                hint="a hand-declared route needs `api`, `base_url`, and `models` in "
                "support.yaml, or add it once in Settings → Add a custom provider",
            )
        )

    if profile == "canfar":
        checks.append(
            Check(
                name="scratch",
                ok=not state.note,
                detail="Studio state is on the session scratch" if not state.note else state.note,
                hint="/scratch dies with the session — export /arc-bound sessions before shutdown",
            )
        )
    else:
        checks.append(Check(name="resources", ok=True, detail=studio_mod.profile_note("laptop")))

    fatal = any(c.fatal and not c.ok for c in checks)
    return {
        "checks": [c.to_dict() for c in checks],
        "ok": all(c.ok for c in checks),
        "fatal": fatal,
        "profile": profile,
        "state_root": str(state.path),
        "state_durable": state.durable,
        "dsh": resolved_dsh,
    }


def baked_mcp_command(patch: Path) -> tuple[str, ...] | None:
    """The ``canfar lab mcp serve`` argv written into a generated layer, if any.

    The layer is ours and has a fixed shape, so this reads the two scalar lines
    of the ``mcp-astroai`` row rather than parsing a document that carries
    ``!!js`` tags the standard YAML loader rejects.
    """
    text = _read_text(patch)
    if text is None:
        return None
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "serverName: astroai")
    except StopIteration:
        return None
    command: str | None = None
    args: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("command:"):
            command = stripped.split("command:", 1)[1].strip().strip("'\"")
        elif stripped.startswith("args:"):
            inner = stripped.split("args:", 1)[1].strip().strip("[]")
            args = [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
            break
    if not command:
        return None
    return (command, *args)


def _first_error_line(stderr: str) -> str:
    """The most informative line of a dsh failure (its last line is often 'Node.js vX')."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for needle in ("Error", "Cannot find", "not found", "unknown bundle", "ENOENT"):
        for line in lines:
            if needle in line:
                return line[:400]
    return lines[0][:400] if lines else "(no stderr)"


def _available_keys(home: Path) -> list[str]:
    """Provider keys Studio can see (env, shared .env, agent auth files)."""
    try:
        from canfar_lab.agent.review_bench import discover_dsh_keys

        return sorted(discover_dsh_keys(home))
    except Exception:  # noqa: BLE001 — the doctor must never fail on a probe
        return []


def _unserviceable_routes() -> list[str]:
    """Router keys in support.yaml that dsh would refuse as written."""
    try:
        from canfar_lab.agent.review_bench import unserviceable_keys

        return sorted(unserviceable_keys())
    except Exception:  # noqa: BLE001 — the doctor must never fail on a probe
        return []


def _native_routes(available_keys: list[str]) -> list[str]:
    """Keyed routes served by a dsh adapter of their own (not in settings.yaml)."""
    try:
        from canfar_lab.agent.support import load_support

        routers = load_support().routers
    except Exception:  # noqa: BLE001 — the doctor must never fail on a probe
        return []
    return [r.provider_id for r in routers if r.native and r.key in available_keys]


def _settings_routes(home: Path) -> list[str]:
    """``llm-pi-ai`` route ids in settings.yaml or the Studio profile patch."""
    import yaml

    found = set(llm_provider_ids_in_patch(home))
    path = dsh_home(home) / "settings.yaml"
    text = _read_text(path)
    if text is not None:
        try:
            doc = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            doc = {}
        providers = (doc.get("llm-pi-ai") or {}).get("providers") or {}
        if isinstance(providers, dict):
            found.update(str(name) for name in providers)
    return sorted(found)
