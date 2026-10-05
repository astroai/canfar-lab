"""curl, gh, and npm installers for agent CLIs."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from canfar_lab.agent.install_bins import (
    BINARY_SOURCE_MISSING,
    _bin_dir,
    _path_under,
    _unlink_landing,
    classify_binary,
    clear_legacy_scratch_binary,
)
from canfar_lab.errors import LabError
from canfar_lab.shell.session_env import resolve_session_env


def _facade(name: str, local):
    """Prefer a patched name on ``canfar_lab.agent.install`` when tests replace it."""
    from canfar_lab.agent import install as install_mod

    override = install_mod.__dict__.get(name)
    if override is None or override is local:
        return local
    return override


def run(*args, **kwargs):
    """Call ``install.run`` so tests that patch the facade still intercept subprocesses."""
    from canfar_lab.agent import install as install_mod
    from canfar_lab.utils.subprocess import run as real_run

    impl = install_mod.__dict__.get("run", real_run)
    if impl is run:
        impl = real_run
    return impl(*args, **kwargs)


def _ensure_bin_dir() -> None:
    _bin_dir().mkdir(parents=True, exist_ok=True)


def _session_environ(extra: dict[str, str] | None = None) -> dict[str, str]:
    merged = {**os.environ, **resolve_session_env(ensure=False).exports()}
    if extra:
        merged.update(extra)
    return merged


def _npm_version_tuple() -> tuple[int, int]:
    """Return (major, minor) for the npm on PATH, or (0, 0) if unknown."""
    from canfar_lab.agent import install as install_mod

    override = install_mod.__dict__.get("_npm_version_tuple")
    if override is not None and override is not _npm_version_tuple:
        return override()
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
    """Session env for ``npm install -g`` agent installs."""
    merged = _session_environ(extra)
    merged.setdefault("NPM_CONFIG_UPDATE_NOTIFIER", "false")
    merged.setdefault("NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS", "true")
    return merged


def npm_global_install_cmd(prefix: Path, *packages: str) -> list[str]:
    """Build ``npm install -g --prefix …`` argv for an intentional agent install."""
    if not packages:
        raise ValueError("npm_global_install_cmd requires at least one package")
    cmd = ["npm", "install", "-g", "--prefix", str(prefix)]
    major, minor = _npm_version_tuple()
    if (major, minor) >= (11, 16):
        cmd.append("--dangerously-allow-all-scripts")
    cmd.extend(packages)
    return cmd


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

    Sandbox wins over ``CANFAR_LAB_BIN_DIR`` so a reinstall picks up the new
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

    Upstream scripts (cursor, kilo, opencode, claude, hermes, …) write under
    ``$HOME/.local/bin`` etc. AstroAI sandboxes subprocess HOME onto scratch
    (``installer-home``) so those drops never hit /arc NFS.

    When ``stderr`` is a TTY, installer stdout is streamed live so long
    bootstraps (Hermes: uv + Python + Node + git clone) do not look hung.
    """
    from canfar_lab.agent.setup_state import INSTALL_TIMEOUT_SEC

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
            hint="Retry later or raise CANFAR_LAB_AGENT_INSTALL_TIMEOUT",
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
                hint="Retry later or raise CANFAR_LAB_AGENT_INSTALL_TIMEOUT",
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
    """``~/.local/share`` next to the managed bin dir (Cursor payload, …)."""
    return _bin_dir().parent / "share"


def _land_symlink_payload(src: Path, dst: Path) -> bool:
    """Keep a versioned CLI payload (bundled node + index.js) together.

    Cursor's installer symlinks ``~/.local/bin/agent`` into
    ``~/.local/share/cursor-agent/versions/<ver>/cursor-agent``. That wrapper
    uses ``realpath $0`` to find bundled ``node``. Returns True when ``dst``
    already points at a usable payload (leave upstream layout alone).
    """
    if not src.is_symlink():
        return False
    try:
        target = src.resolve(strict=True)
    except OSError:
        return False
    if not target.is_file() or target.parent == src.parent:
        return False
    # Already under home share next to ~/.local/bin — leave upstream layout.
    home = Path.home()
    if _path_under(src, home) and _path_under(target, home):
        if src.resolve() == dst.resolve() or src == dst:
            return True
        # Need a ~/.local/bin shim pointing at the same payload executable.
        _unlink_landing(dst)
        dst.symlink_to(target)
        return True
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
    _unlink_landing(dst)
    dst.symlink_to(dest_exe)
    with contextlib.suppress(OSError):
        dest_exe.chmod(dest_exe.stat().st_mode | 0o111)
    return True


def _link_into_local_bin(src: Path, name: str) -> None:
    """Ensure ``name`` is on PATH via ``CANFAR_LAB_BIN_DIR`` (scratch).

    Curl installers often drop under ``$HOME``; we copy/symlink into the
    managed scratch bin and clear leftover home ``~/.local/bin`` shadows.
    Symlink destinations are unlinked first so we never write through a
    planted symlink.
    """
    if not src.is_file():
        return
    with contextlib.suppress(OSError):
        src.chmod(src.stat().st_mode | 0o111)
    dst = _bin_dir() / name
    home = Path.home()
    try:
        if src.resolve() == dst.resolve():
            clear_legacy_scratch_binary(name)
            return
    except OSError:
        pass
    if _land_symlink_payload(src, dst):
        clear_legacy_scratch_binary(name)
        return
    # Already a home install at a non-bin path (e.g. ~/.opencode/bin/opencode):
    # add a managed-bin shim, keep the original payload.
    if _path_under(src, home):
        try:
            if (dst.exists() or dst.is_symlink()) and dst.resolve() == src.resolve():
                clear_legacy_scratch_binary(name)
                return
        except OSError:
            pass
        _unlink_landing(dst)
        dst.symlink_to(src)
        _copy_installer_siblings(src)
        clear_legacy_scratch_binary(name)
        return
    _unlink_landing(dst)
    try:
        shutil.copy2(src, dst)
        with contextlib.suppress(OSError):
            dst.chmod(dst.stat().st_mode | 0o111)
    except OSError:
        dst.symlink_to(src)
        clear_legacy_scratch_binary(name)
        return
    _copy_installer_siblings(src)
    clear_legacy_scratch_binary(name)


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
        session.canfar_lab_bin_dir / cmd,
        session.canfar_lab_npm_prefix / "bin" / cmd,
        *(extra_paths or []),
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return
    raise LabError(f"{cmd} not found on PATH after install — open a new shell")


def _require(cmd: str) -> None:
    if shutil.which(cmd) is None:
        raise LabError(f"{cmd} is required.", hint=f"Install {cmd} or check PATH")


def _gh_auth_ok() -> bool:
    if shutil.which("gh") is None:
        return False
    try:
        proc = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _download_public_gh_release(repo: str, asset: str, dest: Path) -> None:
    """Fetch a release asset from a public GitHub repo (no ``gh auth login``)."""
    url = f"https://github.com/{repo}/releases/latest/download/{asset}"
    run(["curl", "-fsSL", "-o", str(dest), url])


def _symlink_into_bin(src: Path, name: str) -> None:
    """Point ``$BIN_DIR/name`` at ``src`` (replace any prior file/symlink)."""
    if not src.is_file():
        return
    dst = _bin_dir() / name
    _bin_dir().mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(src.resolve())
    with contextlib.suppress(OSError):
        src.chmod(src.stat().st_mode | 0o111)


def _land_codex_package(package_root: Path, binary: str) -> None:
    """Install OpenAI's canonical Codex package under scratch ``share/codex``.

    Layout (see openai/codex ``scripts/codex_package``)::

        share/codex/current/
          codex-package.json
          bin/codex
          bin/codex-code-mode-host
          codex-resources/bwrap
          codex-path/rg

    Symlinks ``codex``, ``codex-code-mode-host``, and ``bwrap`` into the
    managed bin dir so sibling host resolution and PATH lookups work even when
    ``current_exe()`` returns the symlink path (not the package tree).
    """
    dest = _managed_share_dir() / "codex" / "current"
    if dest.exists() or dest.is_symlink():
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package_root, dest, symlinks=True)

    entry = dest / "bin" / binary
    if not entry.is_file():
        raise LabError(f"Package missing bin/{binary}")
    _symlink_into_bin(entry, binary)

    host = dest / "bin" / "codex-code-mode-host"
    if host.is_file():
        _symlink_into_bin(host, "codex-code-mode-host")

    bwrap = dest / "codex-resources" / "bwrap"
    if bwrap.is_file():
        _symlink_into_bin(bwrap, "bwrap")


def _gh_release_bin(repo: str, asset: str, binary: str, *, requires_gh_auth: bool = False) -> None:
    _require("curl")
    tmp = Path(os.environ.get("TMPDIR", "").strip() or "/tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    archive = tmp / asset
    if _facade("_gh_auth_ok", _gh_auth_ok)():
        _require("gh")
        run(["gh", "release", "download", "-R", repo, "-p", asset, "-D", str(tmp)])
    elif requires_gh_auth:
        raise LabError(
            f"Installing from {repo} requires GitHub CLI authentication.",
            hint="Run: gh auth login",
        )
    else:
        try:
            _facade("_download_public_gh_release", _download_public_gh_release)(
                repo, asset, archive
            )
        except LabError as exc:
            raise LabError(
                f"Could not download {asset} from {repo}.",
                hint="Check network access, or run: gh auth login (private releases)",
            ) from exc
    # Extract into a dedicated dir so rglob does not see sibling downloads.
    extract_root = tmp / f"extract-{asset}"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    if asset.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tf:
            try:
                tf.extractall(extract_root, filter="data")
            except TypeError:  # pragma: no cover — Python < 3.12
                tf.extractall(extract_root)
    elif asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_root)
    else:
        raise LabError(f"Unsupported archive: {asset}")

    package_json = next(extract_root.rglob("codex-package.json"), None)
    if package_json is not None:
        _land_codex_package(package_json.parent, binary)
        archive.unlink(missing_ok=True)
        shutil.rmtree(extract_root, ignore_errors=True)
        return

    found = next(extract_root.rglob(binary), None)
    if found is None:
        # Some releases name the extracted binary after the asset basename
        # (e.g. codex-x86_64-unknown-linux-musl) instead of the bare name.
        stem = asset.removesuffix(".tar.gz").removesuffix(".zip")
        for candidate in (extract_root / stem, extract_root / binary):
            if candidate.is_file():
                found = candidate
                break
    if found is None:
        raise LabError(f"Binary {binary} not found in {asset}")
    _bin_dir().mkdir(parents=True, exist_ok=True)
    dest = _bin_dir() / binary
    _unlink_landing(dest)
    shutil.copy2(found, dest)
    with contextlib.suppress(OSError):
        dest.chmod(dest.stat().st_mode | 0o111)
    archive.unlink(missing_ok=True)
    shutil.rmtree(extract_root, ignore_errors=True)
