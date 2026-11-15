#!/usr/bin/env python3
"""Config & Key Synchronization Tool for AstroAI & CANFAR.

Synchronizes authorized keys, credentials, and dotfiles between the Mac laptop
and CANFAR persistent storage ($HOME at /arc/home/<user>).

Managed Credentials & Configs:
  - SSH keys: ~/.ssh/id_ed25519, ~/.ssh/id_ed25519.pub, ~/.ssh/known_hosts
  - Git config: ~/.gitconfig
  - GitHub CLI auth: ~/.config/gh/hosts.yml
  - CANFAR client auth: ~/.canfar/config.yaml & ~/.ssl/cadcproxy.pem
  - Agent / AI API keys: ~/.canfar/lab/.env

Enforces CANFAR Environment Hygiene:
  - Verifies zero Python packages in /arc/home/<user>/.local
  - Verifies all code resides on /scratch/src, not $HOME/src
  - Verifies PYTHONNOUSERSITE=1 in ~/.bashrc

Commands:
  sync-keys.py check       Check local/session keys and auth validity
  sync-keys.py audit-home  Scan CANFAR $HOME for hygiene violations
  sync-keys.py push        Securely push authorized laptop keys to CANFAR $HOME
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

# Colors
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def c(text: str, color: str) -> str:
    if not (sys.stdout.isatty() and os.environ.get("NO_COLOR") is None):
        return text
    return f"{color}{text}{RESET}"


def is_canfar_session() -> bool:
    """Detect if running inside a CANFAR container session."""
    return Path("/scratch").is_dir() and not sys.platform == "darwin"


def check_ssh_keys() -> tuple[bool, str]:
    home = Path.home()
    ssh_dir = (home / ".ssh").resolve()
    if not ssh_dir.is_dir():
        return False, "Missing ~/.ssh directory"
    found = []
    for item in ssh_dir.iterdir():
        if item.is_file() and item.name.startswith("id_") and not item.name.endswith(".pub") and not item.name.endswith("~"):
            mode = oct(item.stat().st_mode & 0o777)
            found.append(f"{item.name} ({mode})")
    if found:
        return True, f"Found: {', '.join(sorted(found))}"
    return False, "No private keys found in ~/.ssh"


def check_git_config() -> tuple[bool, str]:
    home = Path.home()
    cfg = home / ".gitconfig"
    if not cfg.is_file():
        return False, "Missing ~/.gitconfig"
    try:
        user = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True).stdout.strip()
        email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True).stdout.strip()
        return True, f"User: {user} <{email}>"
    except Exception as exc:
        return True, f"File exists ({exc})"


def check_github_cli() -> tuple[bool, str]:
    gh = shutil.which("gh")
    if not gh:
        # Check ~/.config/gh/hosts.yml
        cfg = Path.home() / ".config" / "gh" / "hosts.yml"
        if cfg.is_file():
            return True, "Found ~/.config/gh/hosts.yml (gh CLI not installed)"
        return False, "gh CLI not installed and ~/.config/gh/hosts.yml missing"
    res = subprocess.run([gh, "auth", "status"], capture_output=True, text=True)
    if res.returncode == 0:
        lines = [ln.strip() for ln in res.stdout.splitlines() if "Logged in to" in ln or "account" in ln]
        return True, " / ".join(lines) or "Authenticated"
    return False, res.stderr.strip() or "Not logged in"


def check_canfar_auth() -> tuple[bool, str]:
    canfar = shutil.which("canfar")
    cert = Path.home() / ".ssl" / "cadcproxy.pem"
    cfg = Path.home() / ".canfar" / "config.yaml"
    details = []

    if cert.is_file():
        # Check cert expiration via openssl if present
        openssl = shutil.which("openssl")
        if openssl:
            res = subprocess.run([openssl, "x509", "-in", str(cert), "-noout", "-enddate"], capture_output=True, text=True)
            if res.returncode == 0:
                details.append(res.stdout.strip())
            else:
                details.append("cadcproxy.pem present")
        else:
            details.append("cadcproxy.pem present")
    else:
        details.append("no cadcproxy.pem")

    if canfar:
        res = subprocess.run([canfar, "auth", "show"], capture_output=True, text=True)
        if res.returncode == 0:
            return True, f"Active: {', '.join(details)}"
        return False, f"Auth expired or invalid ({', '.join(details)})"

    if cfg.is_file() and cert.is_file():
        return True, f"Config & cert present ({', '.join(details)})"
    return False, "CANFAR credentials missing"


def check_ai_env() -> tuple[bool, str]:
    env_file = Path.home() / ".canfar" / "lab" / ".env"
    if env_file.is_file():
        has_or = any("OPENROUTER_API_KEY=" in ln for ln in env_file.read_text().splitlines())
        if has_or:
            return True, "OpenRouter key configured in ~/.canfar/lab/.env"
        return True, "Configured in ~/.canfar/lab/.env"
    return False, "No ~/.canfar/lab/.env"


def cmd_check() -> int:
    env_name = "CANFAR Interactive Session" if is_canfar_session() else "Mac Laptop"
    print(f"\n{BOLD}Authentication & Key Status ({env_name}){RESET}")
    print("-" * 65)

    checks = [
        ("SSH Keys", check_ssh_keys),
        ("Git Config", check_git_config),
        ("GitHub Auth", check_github_cli),
        ("CANFAR Auth", check_canfar_auth),
        ("Agent AI Keys", check_ai_env),
    ]

    all_ok = True
    for name, fn in checks:
        ok, detail = fn()
        if ok:
            status_str = c("✓ READY", GREEN)
        else:
            status_str = c("✗ MISSING", YELLOW)
            all_ok = False
        print(f"  {name:<16} {status_str}  {detail}")

    print("-" * 65)

    if is_canfar_session():
        print(f"\n{BOLD}CANFAR Session Storage Hygiene:{RESET}")
        audit_res = cmd_audit_home(clean=False)
        return 0 if (all_ok and audit_res == 0) else 1

    return 0 if all_ok else 1


def cmd_audit_home(clean: bool = False) -> int:
    """Inspect $HOME on CANFAR for forbidden packages and leaked checkouts."""
    home = Path.home()
    violations = []

    # 1. Check ~/.local/lib/python*
    local_lib = home / ".local" / "lib"
    if local_lib.is_dir():
        py_dirs = list(local_lib.glob("python*"))
        if py_dirs:
            violations.append((
                "User site-packages detected in ~/.local/lib",
                py_dirs,
                "Breaks locked pixi/uv ABIs on CANFAR. Must be removed.",
            ))

    # 2. Check ~/src on CANFAR (should be on /scratch/src)
    if is_canfar_session():
        home_src = home / "src"
        if home_src.is_dir() and not home_src.is_symlink():
            violations.append((
                "Code checkout detected in $HOME/src instead of /scratch/src",
                [home_src],
                "Violates CANFAR storage policy and risks hitting the 10GB Ceph home quota.",
            ))

    # 3. Check PYTHONNOUSERSITE in ~/.bashrc
    bashrc = home / ".bashrc"
    if bashrc.is_file():
        content = bashrc.read_text(encoding="utf-8")
        if "PYTHONNOUSERSITE=1" not in content:
            violations.append((
                "PYTHONNOUSERSITE=1 missing from ~/.bashrc",
                [bashrc],
                "Needed to prevent ambient packages from leaking into isolated environments.",
            ))

    if not violations:
        print(c("  ✓ $HOME hygiene is clean (no user site-packages, code on /scratch).", GREEN))
        return 0

    print(c("  ⚠ Storage Hygiene Warnings Found on $HOME:", RED if clean else YELLOW))
    for title, paths, desc in violations:
        print(f"    - {title}:")
        for p in paths:
            print(f"        {p}")
        print(f"        Note: {desc}")

    if clean:
        print(f"\n{BOLD}Cleaning violations...{RESET}")
        for title, paths, _ in violations:
            if "User site-packages" in title:
                for p in paths:
                    print(f"Removing {p} ...")
                    shutil.rmtree(p, ignore_errors=True)
                print(c("  ✓ Cleaned user site-packages from ~/.local/lib", GREEN))
            elif "PYTHONNOUSERSITE=1 missing" in title:
                with bashrc.open("a", encoding="utf-8") as f:
                    f.write("\n# AstroAI CANFAR Environment Hygiene\nexport PYTHONNOUSERSITE=1\nunset PYTHONPATH\n")
                print(c("  ✓ Injected PYTHONNOUSERSITE=1 into ~/.bashrc", GREEN))

    return len(violations)


def cmd_push(dry_run: bool = False) -> int:
    """Securely push laptop keys and config to CANFAR /arc/home/<user> via a headless session."""
    if is_canfar_session():
        print(c("Cannot push from inside a CANFAR session; run this from your Mac laptop.", RED))
        return 1

    canfar = shutil.which("canfar")
    if not canfar:
        print(c("canfar CLI not found on PATH. Install via OpenCADC and login first.", RED))
        return 1

    home = Path.home()
    files_to_sync = []

    # SSH keys and config
    ssh_dir = (home / ".ssh").resolve()
    if ssh_dir.is_dir():
        for item in sorted(ssh_dir.iterdir()):
            if not item.is_file() or item.name.endswith("~") or item.name.endswith(".old"):
                continue
            if item.name.startswith("id_") or item.name in ("config", "known_hosts"):
                is_secret = not item.name.endswith(".pub") and item.name != "known_hosts"
                files_to_sync.append((item, f".ssh/{item.name}", 0o600 if is_secret else 0o644))

    # Git config
    gitconfig = home / ".gitconfig"
    if gitconfig.is_file():
        files_to_sync.append((gitconfig, ".gitconfig", 0o644))

    # GitHub CLI hosts
    gh_hosts = home / ".config" / "gh" / "hosts.yml"
    if gh_hosts.is_file():
        files_to_sync.append((gh_hosts, ".config/gh/hosts.yml", 0o600))

    # CANFAR config & proxy
    canfar_cfg = home / ".canfar" / "config.yaml"
    if canfar_cfg.is_file():
        files_to_sync.append((canfar_cfg, ".canfar/config.yaml", 0o600))
    cadcproxy = home / ".ssl" / "cadcproxy.pem"
    if cadcproxy.is_file():
        files_to_sync.append((cadcproxy, ".ssl/cadcproxy.pem", 0o600))

    # AI keys
    ai_env = home / ".canfar" / "lab" / ".env"
    if ai_env.is_file():
        files_to_sync.append((ai_env, ".canfar/lab/.env", 0o600))

    print(f"\n{BOLD}Pushing Credentials to CANFAR $HOME (/arc/home/<user>):{RESET}")
    for src, rel, perm in files_to_sync:
        print(f"  - {rel:<28} (perm: {oct(perm)})")

    if dry_run:
        print(c("\nDry-run complete. No changes made.", YELLOW))
        return 0

    # Build tarball in-memory
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        for src, rel, perm in files_to_sync:
            data = src.read_bytes()
            ti = tarfile.TarInfo(name=rel)
            ti.size = len(data)
            ti.mode = perm
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(data))

    tar_gz_b64 = base64.b64encode(tar_buf.getvalue()).decode("ascii")

    # In-container unpack script (base64 encoded to avoid ANY whitespace issues with Skaha)
    container_script = """
import os, sys, base64, io, tarfile

home = os.path.expanduser('~')
payload = base64.b64decode(os.environ['KEY_PAYLOAD'])
with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as tar:
    for member in tar.getmembers():
        target_path = os.path.join(home, member.name)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, 'wb') as f:
            f.write(tar.extractfile(member).read())
        os.chmod(target_path, member.mode)
        print(f'Synced: ~/{member.name} ({oct(member.mode)})')

# Enforce ~/.bashrc environment hygiene
bashrc = os.path.join(home, '.bashrc')
hygiene = '\\n# AstroAI CANFAR Environment Hygiene\\nexport PYTHONNOUSERSITE=1\\nunset PYTHONPATH\\n'
content = open(bashrc).read() if os.path.isfile(bashrc) else ''
if 'PYTHONNOUSERSITE=1' not in content:
    with open(bashrc, 'a') as f:
        f.write(hygiene)
    print('Configured PYTHONNOUSERSITE=1 in ~/.bashrc')

print('CANFAR $HOME key sync successful.')
"""
    script_b64 = base64.b64encode(container_script.strip().encode("utf-8")).decode("ascii")

    # Single-token command without whitespace:
    # python3 -c exec(__import__('base64').b64decode(os.environ['RUN_SCRIPT']).decode())
    eval_arg = "exec(__import__('base64').b64decode(__import__('os').environ['RUN_SCRIPT']).decode())"

    cmd = [
        canfar,
        "create",
        "headless",
        "images.canfar.net/astroai/improc:latest",
        "--name",
        f"sync-keys-{int(time.time()) % 10000}",
        "--cpu",
        "1",
        "--memory",
        "2",
        "-e",
        f"KEY_PAYLOAD={tar_gz_b64}",
        "-e",
        f"RUN_SCRIPT={script_b64}",
        "--",
        "python3",
        "-c",
        eval_arg,
    ]

    print(f"\nLaunching headless micro-session to unpack keys directly into CANFAR $HOME...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(c(f"Failed to launch key sync session: {res.stderr.strip()}", RED))
        return 1

    print(res.stdout.strip())
    print(c("\n✓ Keys and configs successfully synced to CANFAR $HOME.", GREEN))
    print("They will automatically persist across all interactive and headless sessions.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AstroAI & CANFAR Key and Config Synchronization Tool."
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("check", help="Check local/session keys and auth status.")

    audit_p = subparsers.add_parser("audit-home", help="Audit $HOME for storage hygiene violations.")
    audit_p.add_argument("--clean", action="store_true", help="Automatically remove violations.")

    push_p = subparsers.add_parser("push", help="Push laptop keys to CANFAR $HOME via a headless micro-session.")
    push_p.add_argument("--dry-run", action="store_true", help="Show files to sync without uploading.")

    args = parser.parse_args()
    cmd = args.command or "check"

    if cmd == "check":
        return cmd_check()
    elif cmd == "audit-home":
        return cmd_audit_home(clean=args.clean)
    elif cmd == "push":
        return cmd_push(dry_run=args.dry_run)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
