from __future__ import annotations

import contextlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from astroai_lab.core.paths import npm_prefix_dir, user_bin_dir
from astroai_lab.errors import LabError
from astroai_lab.shell.session_env import resolve_session_env
from astroai_lab.utils.subprocess import run, run_capture

TOOLS = {
    "node": "Node.js + npm (baked into base image; pixi fallback)",
    "cursor": "Cursor Agent (binary: agent)",
    "claude": "Claude Code",
    "agy": "Antigravity CLI",
    "copilot": "GitHub Copilot CLI",
    "qoder": "Qoder CLI (qodercli)",
    "hermes": "Hermes Agent (Nous Research)",
    "openclaw": "OpenClaw (openclaw/openclaw)",
    # Backend for `agent plugins install ast-grep-cli` only (not an agent).
    "ast-grep": "ast-grep (sg)",
    # Backend for `agent plugins install skore-cli` (binary: skore).
    "skore-cli": "Skore CLI (skore — skills + Skore Hub)",
}

# TOOLS entries that are not coding agents (no agents/*.yaml registry row).
# hyperfine is image-baked — do not list or reinstall it.
TOOL_UTILITIES = frozenset({"node", "ast-grep", "skore-cli"})

# CLI binary name when it differs from the install tool key.
TOOL_BINARIES = {
    "ast-grep": "sg",
    "skore-cli": "skore",
    "qoder": "qodercli",
    "cursor": "agent",  # upstream Cursor Agent binary is still named `agent`
}

# Where an on-disk CLI came from relative to lab management.
BINARY_SOURCE_MANAGED = "managed"  # under ASTROAI_LAB_BIN_DIR / npm prefix (scratch)
BINARY_SOURCE_HOME = "home"  # under $HOME (/arc/home) — user-owned, not managed
BINARY_SOURCE_OTHER = "other"  # elsewhere on PATH
BINARY_SOURCE_MISSING = "missing"


def _bin_dir() -> Path:
    return user_bin_dir()


def _npm_prefix() -> Path:
    return npm_prefix_dir()


def _npm_version_tuple() -> tuple[int, int]:
    """Return (major, minor) for the npm on PATH, or (0, 0) if unknown."""
    try:
        proc = subprocess.run(
            ["npm", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (0, 0)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    if not text:
        return (0, 0)
    parts = text[0].strip().split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return (0, 0)


def npm_install_environ(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Session env for ``npm install -g`` agent installs.

    - ``UPDATE_NOTIFIER=false`` silences the cosmetic "New major version of
      npm available" notice (npm 11→12) that users read as an install failure.
    - ``DANGEROUSLY_ALLOW_ALL_SCRIPTS=true`` opts into dependency install /
      postinstall scripts for global installs (npm 11.16+ advisory; required
      once npm 12 blocks scripts by default). Agent CLIs like omp pull
      ``sharp`` / ``onnxruntime-node`` which need those scripts.
    """
    merged = _session_environ(extra)
    merged.setdefault("NPM_CONFIG_UPDATE_NOTIFIER", "false")
    # Env form works on npm that understand the setting; harmless if ignored.
    merged.setdefault("NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS", "true")
    return merged


def npm_global_install_cmd(prefix: Path, *packages: str) -> list[str]:
    """Build ``npm install -g --prefix …`` argv for an intentional agent install.

    On npm ≥ 11.16 (and npm 12+), also pass ``--dangerously-allow-all-scripts``
    so native deps (sharp, onnxruntime, protobufjs, …) actually build. Global
    installs have no project ``package.json#allowScripts`` to approve into.
    """
    if not packages:
        raise ValueError("npm_global_install_cmd requires at least one package")
    cmd = ["npm", "install", "-g", "--prefix", str(prefix)]
    major, minor = _npm_version_tuple()
    if (major, minor) >= (11, 16):
        cmd.append("--dangerously-allow-all-scripts")
    cmd.extend(packages)
    return cmd


def list_tools() -> dict[str, str]:
    return dict(TOOLS)


def tool_binary(name: str) -> str:
    return TOOL_BINARIES.get(name, name)


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def managed_bin_roots() -> list[Path]:
    """Dirs where astroai owns agent CLIs (scratch / session, never $HOME)."""
    session = resolve_session_env(ensure=False)
    # Include `_bin_dir()` / `_npm_prefix()` so test monkeypatches and the
    # live session resolver always agree on "managed".
    roots = [
        session.astroai_lab_bin_dir,
        session.astroai_lab_npm_prefix / "bin",
        _bin_dir(),
        _npm_prefix() / "bin",
    ]
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        try:
            key = root.resolve()
        except OSError:
            key = root
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def home_bin_candidates(binary: str, *, home: Path | None = None) -> list[Path]:
    """Typical user-owned CLI locations under $HOME (/arc/home on CANFAR)."""
    home = home or Path.home()
    return [
        home / ".local" / "bin" / binary,
        home / f".{binary}" / "bin" / binary,
        home / ".npm-global" / "bin" / binary,
    ]


def classify_binary(
    binary: str,
    *,
    home: Path | None = None,
) -> dict[str, object]:
    """Locate a CLI and classify ownership for list/install/remove policy.

    Config may live on ``$HOME`` (/arc/home); managed binaries live under
    ``ASTROAI_LAB_BIN_DIR`` (scratch). A home-tree CLI is user-owned: lab will
    not install/overwrite it, but ``agent remove --clean-home`` can delete it.

    Special case: Linux ``/usr/bin/sg`` is shadow-utils ``newgrp``, not
    ast-grep — treat it as missing unless a managed/home ``sg`` or ``ast-grep``
    is present.
    """
    home = home or Path.home()
    managed_roots = managed_bin_roots()
    managed_hit: Path | None = None
    for root in managed_roots:
        candidate = root / binary
        if candidate.is_file():
            managed_hit = candidate
            break
        # ast-grep install also drops an ``ast-grep`` symlink next to ``sg``.
        if binary == "sg":
            alt = root / "ast-grep"
            if alt.is_file():
                managed_hit = alt
                break

    home_hit = next(
        (p for p in home_bin_candidates(binary, home=home) if p.is_file()),
        None,
    )
    if home_hit is None and binary == "sg":
        home_hit = next(
            (p for p in home_bin_candidates("ast-grep", home=home) if p.is_file()),
            None,
        )

    which = shutil.which(binary)
    which_path = Path(which) if which else None
    if which_path is not None and binary == "sg" and _is_system_sg_impostor(which_path):
        which_path = None
        # Prefer a real ast-grep binary on PATH when sg is the impostor.
        alt_which = shutil.which("ast-grep")
        if alt_which is not None:
            which_path = Path(alt_which)

    if managed_hit is not None:
        path = managed_hit
        source = BINARY_SOURCE_MANAGED
    elif home_hit is not None:
        path = home_hit
        source = BINARY_SOURCE_HOME
    elif which_path is not None and _path_under(which_path, home):
        path = which_path
        source = BINARY_SOURCE_HOME
    elif which_path is not None:
        # Prefer marking as home when which resolves inside $HOME even if not
        # in the candidate list (e.g. ~/bin).
        path = which_path
        source = BINARY_SOURCE_OTHER
    else:
        path = None
        source = BINARY_SOURCE_MISSING

    return {
        "binary": binary,
        "path": str(path) if path else None,
        "source": source,
        "managed": source == BINARY_SOURCE_MANAGED,
        "home_install": home_hit is not None
        or (which_path is not None and _path_under(which_path, home)),
        "home_path": str(home_hit) if home_hit else None,
    }


def _is_system_sg_impostor(path: Path) -> bool:
    """True when ``path`` is the Linux shadow-utils ``sg`` (newgrp), not ast-grep."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    # ``sg`` is commonly a symlink to ``newgrp``.
    if resolved.name == "newgrp":
        return True
    text = str(resolved)
    return resolved.name == "sg" and (
        text in ("/usr/bin/sg", "/bin/sg", "/usr/sbin/sg")
        or text.endswith(("/usr/bin/sg", "/bin/sg"))
    )


def refuse_if_home_owned(name: str, *, home: Path | None = None) -> None:
    """Block install when the user already has this CLI under $HOME."""
    binary = tool_binary(name)
    info = classify_binary(binary, home=home)
    if info["managed"]:
        return
    if not info["home_install"]:
        return
    where = info.get("home_path") or info.get("path") or f"~/.local/bin/{binary}"
    raise LabError(
        f"{name} is already installed under your home ({where}). "
        "astroai manages CLIs on $SCRATCH ($ASTROAI_LAB_BIN_DIR), not /arc/home.",
        hint=f"astroai agent remove {name} --clean-home   # then: agent install {name}",
    )


def tool_on_path(name: str) -> bool:
    """True when any copy of the binary is available (managed, home, or PATH)."""
    binary = tool_binary(name)
    info = classify_binary(binary)
    if info["source"] != BINARY_SOURCE_MISSING:
        return True
    session = resolve_session_env(ensure=False)
    candidates = [
        session.astroai_lab_bin_dir / binary,
        session.astroai_lab_npm_prefix / "bin" / binary,
    ]
    return any(path.is_file() and os.access(path, os.X_OK) for path in candidates)


def list_tools_status() -> list[dict[str, object]]:
    """Installable tools with whether their binary is currently available."""
    rows: list[dict[str, object]] = []
    for name, desc in TOOLS.items():
        binary = tool_binary(name)
        info = classify_binary(binary)
        rows.append(
            {
                "name": name,
                "binary": binary,
                "description": desc,
                "installed": info["source"] != BINARY_SOURCE_MISSING,
                "source": info["source"],
                "managed": info["managed"],
                "home_install": info["home_install"],
                "path": info["path"],
            }
        )
    return rows


def _ensure_bin_dir() -> None:
    _bin_dir().mkdir(parents=True, exist_ok=True)


def _session_environ(extra: dict[str, str] | None = None) -> dict[str, str]:
    merged = {**os.environ, **resolve_session_env(ensure=False).exports()}
    if extra:
        merged.update(extra)
    return merged


def installer_sandbox_home() -> Path:
    """Scratch ``HOME`` for curl install scripts (never /arc/home).

    Cursor/kilo/opencode/claude installers hardcode ``$HOME/.local/bin``,
    ``$HOME/.kilo/bin``, ``$HOME/.opencode/bin``. Pointing the subprocess HOME
    at scratch keeps those droppings off the Ceph quota. Real ``~/.config``
    stays via ``XDG_CONFIG_HOME``.
    """
    root = _bin_dir().parent / "installer-home"
    root.mkdir(parents=True, exist_ok=True)
    return root


def curl_installer_environ(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Env for an upstream curl|bash installer: binaries on scratch, configs on $HOME."""
    sandbox = installer_sandbox_home()
    merged = _session_environ(extra)
    merged["HOME"] = str(sandbox)
    merged["XDG_CONFIG_HOME"] = str(Path.home() / ".config")
    merged.setdefault("XDG_BIN_DIR", str(_bin_dir()))
    return merged


def find_curl_binary(binary: str, extra: list[Path] | None = None) -> Path | None:
    """Locate a CLI just dropped by a curl installer (sandbox, scratch, or leftover home).

    Sandbox wins over ``ASTROAI_LAB_BIN_DIR`` so a reinstall picks up the new
    drop, not a stale wrapper already sitting in the managed bin dir.
    """
    sandbox = installer_sandbox_home()
    home = Path.home()
    candidates = [
        sandbox / ".local" / "bin" / binary,
        sandbox / f".{binary}" / "bin" / binary,
        sandbox / ".kilo" / "bin" / binary,
        sandbox / ".opencode" / "bin" / binary,
        sandbox / ".hermes" / "bin" / binary,
        sandbox / ".agy" / "bin" / binary,
        _bin_dir() / binary,
        *(extra or []),
        home / ".local" / "bin" / binary,
        home / f".{binary}" / "bin" / binary,
        home / ".kilo" / "bin" / binary,
        home / ".opencode" / "bin" / binary,
        home / ".hermes" / "bin" / binary,
    ]
    return next((p for p in candidates if p.is_file()), None)


def _installer_noise(line: str) -> bool:
    """Upstream installers often spam glog / PATH chatter we already handle."""
    text = line.strip()
    if not text:
        return True
    lower = text.lower()
    if lower.startswith("error: logging before google.init"):
        return True
    if "path verification:" in lower:
        return True
    # Final land path is reported by `agent install` after we copy into scratch.
    if "installed successfully at" in lower or "installed agy" in lower:
        return True
    return text.startswith("Run '") and " to start" in lower


def _raise_curl_install_failure(url: str, text: str) -> None:
    useful = "\n".join(line for line in text.splitlines() if not _installer_noise(line))
    raise LabError(
        f"Install failed for {url}" + (f":\n{useful}" if useful else ""),
        hint="Check network / auth, then retry",
    )


def _curl_pipe_bash(
    url: str,
    *,
    env: dict[str, str] | None = None,
    args: list[str] | None = None,
    stream: bool | None = None,
) -> None:
    """Fetch an install script and run it.

    Upstream scripts (cursor, kilo, opencode, claude, hermes, …) hardcode
    ``$HOME/.local`` or ``$HOME/.<name>/bin``. The subprocess HOME is a scratch
    sandbox so those writes never land on /arc/home. ``XDG_CONFIG_HOME`` still
    points at the real ``~/.config``.

    When ``stderr`` is a TTY, installer stdout is streamed live so long
    bootstraps (Hermes: uv + Python + Node + git clone) do not look hung.
    """
    from astroai_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    _require("curl")
    merged = curl_installer_environ(env)
    # Keep curl + bash within INSTALL_TIMEOUT_SEC total (including curl's +5 slack).
    total = max(60, INSTALL_TIMEOUT_SEC)
    curl_budget = max(20, (total - 5) // 3)
    bash_budget = max(30, total - curl_budget - 5)
    if stream is None:
        stream = sys.stderr.isatty()
    cmd = ["bash", "-s", "--", *(args or [])]
    try:
        if stream:
            print(f"Downloading installer from {url}…", file=sys.stderr, flush=True)
        script = subprocess.run(
            ["curl", "-fsSL", "--max-time", str(curl_budget), url],
            capture_output=True,
            check=True,
            env=merged,
            timeout=curl_budget + 5,
        ).stdout
    except subprocess.TimeoutExpired as exc:
        raise LabError(
            f"Install timed out after {total}s fetching {url}",
            hint="Retry later or raise ASTROAI_LAB_AGENT_INSTALL_TIMEOUT",
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (
            (exc.stderr or b"").decode(errors="replace").strip()
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        raise LabError(
            f"Install failed for {url}" + (f": {detail}" if detail else ""),
            hint="Check network / auth, then retry",
        ) from exc

    if stream:
        print(
            "Running installer (self-bootstrapping agents can take 5–15 minutes)…",
            file=sys.stderr,
            flush=True,
        )
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=merged,
        )
        assert proc.stdin is not None
        proc.stdin.write(script)
        proc.stdin.close()
        assert proc.stdout is not None
        chunks: list[bytes] = []
        for raw in iter(proc.stdout.readline, b""):
            chunks.append(raw)
            line = raw.decode(errors="replace").rstrip("\r\n")
            if line and not _installer_noise(line):
                sys.stderr.buffer.write(raw)
                if not raw.endswith(b"\n"):
                    sys.stderr.buffer.write(b"\n")
                sys.stderr.buffer.flush()
        try:
            proc.wait(timeout=bash_budget)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            raise LabError(
                f"Install timed out after {total}s running installer from {url}",
                hint="Retry later or raise ASTROAI_LAB_AGENT_INSTALL_TIMEOUT",
            ) from exc
        text = b"".join(chunks).decode(errors="replace").strip()
        if proc.returncode != 0:
            _raise_curl_install_failure(url, text)
        return

    proc = subprocess.run(
        cmd,
        input=script,
        capture_output=True,
        check=False,
        env=merged,
        timeout=bash_budget,
    )
    out = b""
    if isinstance(proc.stdout, bytes):
        out += proc.stdout
    if isinstance(proc.stderr, bytes):
        out += proc.stderr
    text = out.decode(errors="replace").strip()
    if proc.returncode != 0:
        _raise_curl_install_failure(url, text)


def _managed_share_dir() -> Path:
    """Scratch ``share/`` next to the managed bin dir (Cursor payload, …)."""
    return _bin_dir().parent / "share"


def _land_symlink_payload(src: Path, dst: Path) -> bool:
    """Keep a versioned CLI payload (bundled node + index.js) together.

    Cursor's installer symlinks ``~/.local/bin/agent`` into
    ``~/.local/share/cursor-agent/versions/<ver>/cursor-agent``. That wrapper
    uses ``realpath $0`` to find bundled ``node``. Copying only the wrapper
    into the bin dir makes it exec ``$BIN_DIR/node``, which is missing.
    Returns True when ``dst`` now points at the landed payload.
    """
    if not src.is_symlink():
        return False
    try:
        target = src.resolve(strict=True)
    except OSError:
        return False
    if not target.is_file() or target.parent == src.parent:
        return False
    payload = target.parent
    dest_payload = _managed_share_dir() / target.name / payload.name
    if dest_payload.resolve() != payload.resolve():
        if dest_payload.exists() or dest_payload.is_symlink():
            if dest_payload.is_dir() and not dest_payload.is_symlink():
                shutil.rmtree(dest_payload)
            else:
                dest_payload.unlink()
        dest_payload.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(payload), str(dest_payload))
        except OSError:
            shutil.copytree(payload, dest_payload, symlinks=True)
    dest_exe = dest_payload / target.name
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(dest_exe)
    with contextlib.suppress(OSError):
        dest_exe.chmod(dest_exe.stat().st_mode | 0o111)
    return True


def _link_into_local_bin(src: Path, name: str) -> None:
    """Land ``src`` under the managed scratch bin dir.

    Upstream installers often drop into ``~/.local/bin``. We copy into
    ``ASTROAI_LAB_BIN_DIR`` (scratch) and remove that home dropping so the
    CLI is not left on /arc/home. Pre-existing user home installs are gated
    earlier by ``refuse_if_home_owned``.

    When ``src`` is a symlink into a payload directory (Cursor: wrapper +
    bundled node), keep that tree together under scratch ``share/`` and
    point the bin name at the real executable.
    """
    if not src.is_file():
        return
    with contextlib.suppress(OSError):
        src.chmod(src.stat().st_mode | 0o111)
    dst = _bin_dir() / name
    if _land_symlink_payload(src, dst):
        return
    try:
        if src.resolve() == dst.resolve():
            return
    except OSError:
        pass
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    # Prefer a real file in scratch (survives without the installer path).
    try:
        shutil.copy2(src, dst)
        with contextlib.suppress(OSError):
            dst.chmod(dst.stat().st_mode | 0o111)
    except OSError:
        dst.symlink_to(src)
        return
    _copy_installer_siblings(src)
    home = Path.home()
    if _path_under(src, home):
        with contextlib.suppress(OSError):
            src.unlink()


def _copy_installer_siblings(src: Path) -> None:
    """Copy sidecar files next to a curl-dropped CLI (e.g. kilo ``tree-sitter``)."""
    parent = src.parent
    dst_dir = _bin_dir()
    if parent == dst_dir or parent.name != "bin":
        return
    try:
        src_key = src.resolve()
    except OSError:
        src_key = src
    for child in parent.iterdir():
        try:
            if child.resolve() == src_key:
                continue
        except OSError:
            if child.name == src.name:
                continue
        dest = dst_dir / child.name
        if dest.exists() or dest.is_symlink():
            continue
        with contextlib.suppress(OSError):
            if child.is_dir():
                shutil.copytree(child, dest)
            elif child.is_file():
                shutil.copy2(child, dest)


def _verify_cmd(cmd: str, *, extra_paths: list[Path] | None = None) -> None:
    if classify_binary(cmd)["source"] != BINARY_SOURCE_MISSING:
        return
    session = resolve_session_env(ensure=False)
    candidates = [
        session.astroai_lab_bin_dir / cmd,
        session.astroai_lab_npm_prefix / "bin" / cmd,
        *(extra_paths or []),
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return
    raise LabError(f"{cmd} not found on PATH after install — open a new shell")


def _require(cmd: str) -> None:
    if shutil.which(cmd) is None:
        raise LabError(f"{cmd} is required.", hint=f"Install {cmd} or check PATH")


def _gh_release_bin(repo: str, asset: str, binary: str) -> None:
    _require("gh")
    _require("curl")
    run_capture(["gh", "auth", "status"])
    tmp = Path(os.environ.get("TMPDIR", "").strip() or "/tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    run(["gh", "release", "download", "-R", repo, "-p", asset, "-D", str(tmp)])
    archive = tmp / asset
    if asset.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(tmp)
    elif asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
    else:
        raise LabError(f"Unsupported archive: {asset}")
    found = next(tmp.rglob(binary), None)
    if found is None:
        # Some releases name the extracted binary after the asset basename
        # (e.g. codex-x86_64-unknown-linux-musl) instead of the bare name.
        stem = asset.removesuffix(".tar.gz").removesuffix(".zip")
        candidate = tmp / stem
        if candidate.is_file():
            found = candidate
    if found is None:
        raise LabError(f"Binary {binary} not found in {asset}")
    shutil.copy2(found, _bin_dir() / binary)
    with contextlib.suppress(OSError):
        (_bin_dir() / binary).chmod((_bin_dir() / binary).stat().st_mode | 0o111)
    archive.unlink(missing_ok=True)


def install_tool(name: str, *, dry_run: bool = False) -> None:
    if name == "hyperfine":
        raise LabError(
            "hyperfine is image-baked on AstroAI base (already on PATH)",
            hint="hyperfine --version",
        )
    if name not in TOOLS:
        raise LabError(f"Unknown tool: {name}", hint="astroai agent list")
    refuse_if_home_owned(name)
    if dry_run:
        return
    from astroai_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

    resolve_session_env(ensure=True)
    _ensure_bin_dir()
    arch = platform.machine()
    npm_timeout = INSTALL_TIMEOUT_SEC

    if name == "node":
        # Node LTS + npm are baked into the base image (canfar-containers), so
        # this is normally a no-op on CANFAR sessions; keep the pixi fallback
        # for bare environments where node is not already on PATH.
        if shutil.which("node") is not None and shutil.which("npm") is not None:
            return
        _require("pixi")
        session = resolve_session_env(ensure=False)
        pixi_bin = session.pixi_home / "bin"
        bin_dir = _bin_dir()
        run(["pixi", "global", "install", "nodejs"], env=_session_environ(), timeout=npm_timeout)
        for cmd in ("node", "npm", "npx"):
            src = pixi_bin / cmd
            if src.is_file():
                (bin_dir / cmd).unlink(missing_ok=True)
                (bin_dir / cmd).symlink_to(src)
        _verify_cmd("node")
        _verify_cmd("npm")
    elif name == "cursor":
        # Upstream binary remains `agent`; registry / TOOLS id is `cursor`.
        _curl_pipe_bash("https://cursor.com/install")
        found = find_curl_binary("agent")
        if found is not None:
            _link_into_local_bin(found, "agent")
        _verify_cmd("agent")
    elif name == "claude":
        _curl_pipe_bash("https://claude.ai/install.sh")
        found = find_curl_binary("claude")
        if found is not None:
            _link_into_local_bin(found, "claude")
        _verify_cmd("claude")
    elif name == "agy":
        _curl_pipe_bash("https://antigravity.google/cli/install.sh")
        found = find_curl_binary("agy")
        if found is not None:
            _link_into_local_bin(found, "agy")
        _verify_cmd("agy")
    elif name == "copilot":
        env = {"PREFIX": str(_npm_prefix()), "CI": "1"}
        with contextlib.suppress(subprocess.CalledProcessError, LabError):
            _curl_pipe_bash("https://gh.io/copilot-install", env=env)
        copilot_bin = _npm_prefix() / "bin" / "copilot"
        if not copilot_bin.is_file() and shutil.which("copilot") is None:
            _require("npm")
            run(
                npm_global_install_cmd(_npm_prefix(), "@github/copilot@latest"),
                env=npm_install_environ(),
                timeout=npm_timeout,
            )
            copilot_bin = _npm_prefix() / "bin" / "copilot"
        _link_into_local_bin(copilot_bin, "copilot")
        _verify_cmd("copilot", extra_paths=[copilot_bin])
    elif name == "hermes":
        # Nous Research Hermes Agent — self-contained installer (bootstraps its
        # own Python/uv/Node), first-class OpenRouter + headless `hermes -z`.
        _curl_pipe_bash("https://hermes-agent.nousresearch.com/install.sh")
        found = find_curl_binary("hermes")
        if found is not None:
            _link_into_local_bin(found, "hermes")
        _verify_cmd("hermes")
    elif name == "openclaw":
        # Requires Node >= 24.15 — Node 24.18.1 LTS is baked into the base image.
        _require("npm")
        run(
            npm_global_install_cmd(_npm_prefix(), "openclaw@latest"),
            env=npm_install_environ(),
            timeout=npm_timeout,
        )
        openclaw_bin = _npm_prefix() / "bin" / "openclaw"
        _link_into_local_bin(openclaw_bin, "openclaw")
        _verify_cmd("openclaw", extra_paths=[openclaw_bin])
    elif name == "qoder":
        env = {"XDG_BIN_DIR": str(_bin_dir())}
        with contextlib.suppress(subprocess.CalledProcessError, LabError):
            _curl_pipe_bash("https://qoder.com/install", env=env)
        found = find_curl_binary("qodercli")
        if found is not None:
            _link_into_local_bin(found, "qodercli")
            _link_into_local_bin(found, "qoder")
        if shutil.which("qodercli") is None and not (_bin_dir() / "qodercli").is_file():
            _require("npm")
            run(
                npm_global_install_cmd(_npm_prefix(), "@qoder-ai/qodercli@latest"),
                env=npm_install_environ(),
                timeout=npm_timeout,
            )
            npm_bin = _npm_prefix() / "bin" / "qodercli"
            _link_into_local_bin(npm_bin, "qodercli")
            _link_into_local_bin(npm_bin, "qoder")
        _verify_cmd("qodercli")
    elif name == "ast-grep":
        if arch not in ("x86_64", "aarch64"):
            raise LabError(f"Unsupported architecture: {arch}")
        asset = f"app-{arch}-unknown-linux-gnu.zip"
        _gh_release_bin("ast-grep/ast-grep", asset, "sg")
        (_bin_dir() / "ast-grep").unlink(missing_ok=True)
        (_bin_dir() / "ast-grep").symlink_to(_bin_dir() / "sg")
        _verify_cmd("sg")
    elif name == "skore-cli":
        _require("uv")
        run(
            ["uv", "tool", "install", "--force", "skore-cli"],
            env=_session_environ(),
            timeout=npm_timeout,
        )
        _verify_cmd("skore")
    else:
        raise LabError(f"Unknown tool: {name}", hint="astroai agent install  (or agent list)")


# ---------------------------------------------------------------------------
# Removal (Phase 2: `agent remove`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoveResult:
    target: str
    status: str  # removed | would_remove | error
    detail: str = ""


# npm package name per tool (mirrors the `npm install -g` calls in install_tool).
TOOL_NPM_PACKAGES = {
    "openclaw": "openclaw",
    "copilot": "@github/copilot",
    "qoder": "@qoder-ai/qodercli",
}

# Home-relative config files a tool owns (removed on `agent remove`).
# Registry-driven agents carry their own config.path in agents/*.yaml.
TOOL_CONFIG_PATHS = {
    "copilot": [".copilot/mcp-config.json"],
    "qoder": [".qoder/settings.json"],
    "hermes": [".hermes/config.yaml"],
    "openclaw": [".openclaw/openclaw.json"],
}


def _remove_file(path: Path, target: str, *, dry_run: bool) -> RemoveResult | None:
    """Unlink a file/symlink; None when it doesn't exist."""
    if not (path.exists() or path.is_symlink()):
        return None
    if dry_run:
        return RemoveResult(target, "would_remove", str(path))
    try:
        path.unlink(missing_ok=True)
        return RemoveResult(target, "removed", str(path))
    except OSError as exc:
        return RemoveResult(target, "error", str(exc))


def _remove_tree(path: Path, target: str, *, dry_run: bool) -> RemoveResult | None:
    """Remove a directory tree; None when it doesn't exist."""
    if not path.exists():
        return None
    if dry_run:
        return RemoveResult(target, "would_remove", str(path))
    try:
        shutil.rmtree(path)
        return RemoveResult(target, "removed", str(path))
    except OSError as exc:
        return RemoveResult(target, "error", str(exc))


def uninstall_tool(
    name: str,
    *,
    home: Path | None = None,
    purge: bool = False,
    clean_home: bool = False,
    dry_run: bool = False,
) -> list[RemoveResult]:
    """Uninstall a CLI tool: binaries, config files, plugin files, setup stamps.

    By default only **managed** (scratch) binaries are removed. Home-tree CLIs
    under ``$HOME`` (/arc/home) are left alone unless ``clean_home=True``.
    ``purge`` additionally removes the tool's whole home config dir (e.g.
    ``~/.hermes``, ``~/.openclaw``). Dry-run reports ``would_remove`` without
    touching the filesystem. Returns one result per target.
    """
    if name not in TOOLS:
        raise LabError(f"Unknown tool: {name}", hint="astroai agent list")
    home = home or Path.home()
    results: list[RemoveResult] = []
    binary = tool_binary(name)
    info = classify_binary(binary, home=home)

    if (
        info["home_install"]
        and not info["managed"]
        and not clean_home
        and info["source"] != BINARY_SOURCE_MISSING
    ):
        where = info.get("home_path") or info.get("path")
        raise LabError(
            f"{name} is installed under your home ({where}), not managed by astroai-lab",
            hint=f"astroai agent remove {name} --clean-home",
        )

    # 1. Managed binaries from the session bin dir + npm prefix bin.
    share_root = _managed_share_dir()
    for bin_path in (_bin_dir() / binary, _npm_prefix() / "bin" / binary):
        payload = None
        if bin_path.is_symlink():
            with contextlib.suppress(OSError):
                landed = bin_path.resolve().parent
                if _path_under(landed, share_root):
                    payload = landed
        result = _remove_file(bin_path, f"binary:{binary}", dry_run=dry_run)
        if result:
            results.append(result)
        if payload is not None:
            result = _remove_tree(payload, f"payload:{payload}", dry_run=dry_run)
            if result:
                results.append(result)

    # Convenience aliases created at install time (id name ≠ binary name).
    if name == "qoder":
        result = _remove_file(_bin_dir() / "qoder", "binary:qoder", dry_run=dry_run)
        if result:
            results.append(result)
    if name == "ast-grep":
        result = _remove_file(_bin_dir() / "ast-grep", "binary:ast-grep", dry_run=dry_run)
        if result:
            results.append(result)

    # 1b. Optional: user-owned home CLIs.
    if clean_home:
        for home_bin in home_bin_candidates(binary, home=home):
            result = _remove_file(home_bin, f"home-binary:{binary}", dry_run=dry_run)
            if result:
                results.append(result)

    # 2. Best-effort npm uninstall for npm-installed tools (binary removal
    #    above is authoritative; this just cleans the node_modules tree).
    pkg = TOOL_NPM_PACKAGES.get(name)
    if pkg and not dry_run and shutil.which("npm"):
        from astroai_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

        with contextlib.suppress(LabError, subprocess.CalledProcessError, OSError):
            run(
                ["npm", "uninstall", "-g", "--prefix", str(_npm_prefix()), pkg],
                env=_session_environ(),
                timeout=INSTALL_TIMEOUT_SEC,
                quiet=True,  # keep stdout clean for `--json agent remove/wipe`
            )

    # 3. Config files owned by the tool (persistent under $HOME — only when
    #    removing a managed install or explicitly cleaning home).
    if info["managed"] or clean_home or purge:
        for rel in TOOL_CONFIG_PATHS.get(name, []):
            result = _remove_file(home / rel, f"config:{rel}", dry_run=dry_run)
            if result:
                results.append(result)

        # 4. Plugin-created files for this agent (precise sweep), then any
        #    leftover ~/.<id>/skills tree.
        from astroai_lab.agent import plugins as agent_plugins

        for row in agent_plugins.remove_agent_plugin_files(name, home=home, dry_run=dry_run):
            results.append(
                RemoveResult(
                    row.get("target", f"plugins:{name}"),
                    row.get("status", "removed"),
                    row.get("detail", ""),
                )
            )
        plugin_dir = home / f".{name}" / "skills"
        result = _remove_tree(plugin_dir, f"plugins:{plugin_dir}", dry_run=dry_run)
        if result:
            results.append(result)

    # 5. Setup state stamps (only when tearing down managed or cleaning home).
    if info["managed"] or clean_home:
        from astroai_lab.agent.setup_state import failed_path, stamp_path

        for spath, target in (
            (stamp_path(home), "state:stamp"),
            (failed_path(home), "state:failed"),
        ):
            result = _remove_file(spath, target, dry_run=dry_run)
            if result:
                results.append(result)

    # 6. --purge: remove the tool's whole home config dir (parent of config).
    if purge:
        for rel in TOOL_CONFIG_PATHS.get(name, []):
            d = (home / rel).parent
            lab_dir = home / ".astroai" / "lab"
            if d not in {home, lab_dir}:
                result = _remove_tree(d, f"purge:{d}", dry_run=dry_run)
                if result:
                    results.append(result)

    return results
