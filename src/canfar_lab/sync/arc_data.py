"""Carry the sync doorbell through ``canfar data`` (vosfs).

Git still moves the commits. The mailbox is a few JSON files under
``arc:/home/<user>/.astroai/lab/sync``. On a session that directory is the
POSIX home. On a laptop, ``canfar data cp`` reads and writes the same files.
A vosfs mount is not required. ``canfar data`` ships with the CANFAR client
v1.5 beta; an older ``canfar`` leaves this transport off.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from canfar_lab.core.cadc_auth import cadc_cert_path

Runner = Any  # Callable[[list[str]], tuple[int, str]]


def arc_user() -> str | None:
    """CANFAR account for ``arc:/home/<user>``.

    ``CANFAR_LAB_ARC_USER`` wins. A session home ``/arc/home/<user>`` is next.
    Otherwise the CADC proxy certificate's CN, which is that account.
    """
    explicit = os.environ.get("CANFAR_LAB_ARC_USER", "").strip()
    if explicit:
        return explicit
    home = Path.home()
    if len(home.parts) >= 3 and home.parts[-3:-1] == ("arc", "home"):
        return home.name
    cert = cadc_cert_path()
    if cert is None:
        return None
    try:
        proc = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-noout", "-subject", "-nameopt", "RFC2253"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = f"{proc.stdout}\n{proc.stderr}"
    for part in text.replace("subject=", "").split(","):
        item = part.strip()
        if item.startswith("CN="):
            name = item[3:].strip()
            return name or None
    return None


def _default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, f"{proc.stdout or ''}{proc.stderr or ''}"


def _supports(runner: Runner) -> bool:
    code, text = runner(["canfar", "data", "--help"])
    return code == 0 and ("IDENTIFIER" in text or "local:" in text)


def remote_root(user: str) -> str:
    return f"arc:/home/{user}/.astroai/lab/sync"


def _local(path: Path) -> str:
    return "local:" + str(path.resolve())


def bridge(
    local: Path,
    *,
    direction: str,
    user: str | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Copy mailbox files to or from ``arc:/home``.

    ``direction`` is ``push`` (desk requests up) or ``pull`` (session events down).
    """
    run = runner or _default_runner
    if not _supports(run):
        return {
            "ok": False,
            "transport": "off",
            "hint": "This canfar has no `canfar data` (client v1.5). Git sync still works.",
        }
    account = user if user is not None else arc_user()
    if not account or "/" in account or ":" in account:
        return {
            "ok": False,
            "transport": "off",
            "hint": (
                "Set CANFAR_LAB_ARC_USER to the CANFAR account so the doorbell can use arc:/home."
            ),
        }
    remote = remote_root(account)
    local = Path(local)
    copied: list[str] = []
    if direction == "push":
        jobs = [
            ["canfar", "data", "mkdir", "-p", remote],
            ["canfar", "data", "mkdir", "-p", f"{remote}/state"],
        ]
        requests = local / "requests"
        if requests.is_dir():
            jobs.append(["canfar", "data", "cp", "-R", _local(requests), remote])
        desk_state = local / "state" / "desk.json"
        if desk_state.is_file():
            jobs.append(
                [
                    "canfar",
                    "data",
                    "cp",
                    _local(desk_state),
                    f"{remote}/state/desk.json",
                ]
            )
    elif direction == "pull":
        (local / "events").mkdir(parents=True, exist_ok=True)
        (local / "state").mkdir(parents=True, exist_ok=True)
        jobs = [
            ["canfar", "data", "cp", "-R", f"{remote}/events", _local(local)],
            ["canfar", "data", "cp", f"{remote}/ready.json", _local(local / "ready.json")],
            [
                "canfar",
                "data",
                "cp",
                f"{remote}/state/session.json",
                _local(local / "state" / "session.json"),
            ],
        ]
    else:
        return {"ok": False, "transport": "off", "error": f"unknown direction: {direction}"}

    for argv in jobs:
        code, text = run(argv)
        if code == 0:
            copied.append(argv[2])
            continue
        if argv[2] == "mkdir":
            return {
                "ok": False,
                "transport": "arc",
                "remote": remote,
                "error": text.strip()[:300] or f"exit {code}",
            }
    return {"ok": True, "transport": "arc", "remote": remote, "copied": copied}
