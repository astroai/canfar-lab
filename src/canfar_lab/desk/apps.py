"""Desktop app launching for the Desk dock: editor, terminal, browser.

macOS uses ``open -a <App> <dir>`` (Ghostty, iTerm, Terminal all accept a
folder). Linux uses ``$TERMINAL`` or a known emulator with ``cwd=dir``.
Everything is injectable so tests can pin a platform without a display.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from canfar_lab.errors import LabError

#: editor key → (binary on PATH, macOS app name)
EDITOR_APPS: dict[str, tuple[str, str]] = {
    "vscode": ("code", "Visual Studio Code"),
    "cursor": ("cursor", "Cursor"),
    "windsurf": ("windsurf", "Windsurf"),
}

#: terminal key → macOS app name
TERMINAL_APPS: dict[str, str] = {
    "ghostty": "Ghostty",
    "iterm": "iTerm",
    "terminal": "Terminal",
}

_LINUX_TERMINALS = (
    "x-terminal-emulator",
    "gnome-terminal",
    "konsole",
    "wezterm",
    "alacritty",
    "kitty",
    "xterm",
)

Runner = Callable[..., object]


def _platform() -> str:
    return sys.platform


def _app_installed(app: str) -> bool:
    for root in ("/Applications", "/System/Applications", str(Path.home() / "Applications")):
        if Path(root, f"{app}.app").is_dir():
            return True
    return False


def editor_command(
    path: Path,
    editor: str = "vscode",
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """argv that opens ``path`` in the configured editor."""
    wanted = (editor or "vscode").strip()
    key = wanted.lower()
    if key in EDITOR_APPS:
        binary, mac_app = EDITOR_APPS[key]
    elif " " in wanted:
        parts = shlex.split(wanted)
        return [*parts, str(path)]
    else:
        binary, mac_app = wanted, wanted
    found = which(binary)
    if found:
        return [found, str(path)]
    if (platform or _platform()) == "darwin":
        return ["open", "-a", mac_app, str(path)]
    raise LabError(
        f"Editor not found: {editor}",
        hint=f"Install `{binary}` or set apps_editor in ~/.astroai/lab/config.yaml",
    )


def terminal_command(
    path: Path,
    terminal: str = "auto",
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    app_installed: Callable[[str], bool] = _app_installed,
    environ: dict[str, str] | None = None,
) -> list[str]:
    """argv that opens a terminal in ``path``."""
    plat = platform or _platform()
    env = environ if environ is not None else os.environ
    wanted = (terminal or "auto").strip().lower()

    if plat == "darwin":
        if wanted == "auto":
            for key in ("ghostty", "iterm"):
                app = TERMINAL_APPS[key]
                if app_installed(app):
                    return ["open", "-a", app, str(path)]
            return ["open", "-a", TERMINAL_APPS["terminal"], str(path)]
        if wanted in TERMINAL_APPS:
            app = TERMINAL_APPS[wanted]
            if wanted != "terminal" and not app_installed(app):
                raise LabError(
                    f"Terminal app not installed: {app}",
                    hint="Set apps_terminal to `terminal`, `iterm`, or `ghostty`.",
                )
            return ["open", "-a", app, str(path)]
        raise LabError(f"Unknown terminal: {terminal}", hint="Use auto|ghostty|iterm|terminal")

    if wanted not in ("auto", ""):
        found = which(wanted)
        if found:
            return [found]
        raise LabError(f"Terminal not found: {terminal}", hint="Install it or set apps_terminal.")
    env_terminal = str(env.get("TERMINAL", "")).strip()
    if env_terminal:
        found = which(env_terminal)
        if found:
            return [found]
    for name in _LINUX_TERMINALS:
        found = which(name)
        if found:
            return [found]
    raise LabError(
        "No terminal emulator found",
        hint="Install one or set $TERMINAL / apps_terminal.",
    )


def _default_runner(cmd: list[str], cwd: Path) -> int:
    result = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, timeout=30)
    return result.returncode


def open_editor(
    path: Path,
    editor: str = "vscode",
    *,
    runner: Runner | None = None,
    **kwargs,  # noqa: ANN003 — forwarded to editor_command
) -> str:
    """Launch the editor; returns a human description. Raises LabError."""
    target = Path(path).expanduser().resolve()
    cmd = editor_command(target, editor, **kwargs)
    run = runner or _default_runner
    if run(cmd, target) != 0:  # type: ignore[call-arg]
        raise LabError(f"Editor launch failed: {' '.join(cmd)}")
    return " ".join(cmd)


def open_terminal(
    path: Path,
    terminal: str = "auto",
    *,
    runner: Runner | None = None,
    **kwargs,  # noqa: ANN003 — forwarded to terminal_command
) -> str:
    """Launch a terminal in ``path``; returns a human description."""
    target = Path(path).expanduser().resolve()
    cmd = terminal_command(target, terminal, **kwargs)
    run = runner or _default_runner
    if run(cmd, target) != 0:  # type: ignore[call-arg]
        raise LabError(f"Terminal launch failed: {' '.join(cmd)}")
    return " ".join(cmd)


#: Laptop notebook servers. The desk opens these in the local browser.
LOCAL_APPS: dict[str, dict[str, str | int]] = {
    "jupyter": {"binary": "jupyter", "port": 8888},
    "marimo": {"binary": "marimo", "port": 2718},
}


def local_app_command(tool: str, root: Path, port: int) -> list[str]:
    """argv for a loopback JupyterLab or marimo server rooted at ``root``."""
    folder = str(root)
    if tool == "jupyter":
        return [
            "jupyter",
            "lab",
            "--ip",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-browser",
            "--ServerApp.token=",
            "--ServerApp.password=",
            "--ServerApp.open_browser=False",
            f"--ServerApp.root_dir={folder}",
            f"--ServerApp.preferred_dir={folder}",
        ]
    if tool == "marimo":
        return [
            "marimo",
            "edit",
            "--no-token",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--skip-update-check",
            "--headless",
            folder,
        ]
    raise LabError(f"unknown local app: {tool}")


def _local_url(tool: str, port: int) -> str:
    if tool == "jupyter":
        return f"http://127.0.0.1:{port}/lab"
    return f"http://127.0.0.1:{port}/"


def _state_path(home: Path) -> Path:
    return home / ".astroai" / "lab" / "desk-apps.json"


def _read_state(home: Path) -> dict:
    path = _state_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(home: Path, data: dict) -> None:
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_port(start: int) -> int:
    for port in range(start, start + 20):
        if not _port_open(port):
            return port
    raise LabError(f"No free localhost port from {start}.")


def _wait_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.2)
    return False


def ensure_local_app(
    tool: str,
    root: Path,
    *,
    home: Path,
    opener: Callable[[str], bool],
    popen: Callable[..., object] = subprocess.Popen,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, object]:
    """Start JupyterLab or marimo on this computer and open it in the local browser.

    A second click reuses the server already serving this folder.
    """
    spec = LOCAL_APPS.get(tool)
    if spec is None:
        raise LabError(f"unknown local app: {tool}")
    binary = str(spec["binary"])
    if which(binary) is None:
        raise LabError(
            f"{binary} is not installed on this computer.",
            hint=f"Install {binary} in the project environment, then press the button again.",
        )
    folder = Path(root).expanduser().resolve()
    state = _read_state(home)
    raw = state.get(tool)
    saved = raw if isinstance(raw, dict) else {}
    saved_root = str(saved.get("root") or "")
    try:
        saved_port = int(saved.get("port") or 0)
        saved_pid = int(saved.get("pid") or 0)
    except (TypeError, ValueError):
        saved_port = 0
        saved_pid = 0
    if (
        saved_root == str(folder)
        and saved_port
        and _pid_alive(saved_pid)
        and _port_open(saved_port)
    ):
        url = str(saved.get("url") or _local_url(tool, saved_port))
        return {"url": url, "opened": bool(opener(url)), "started": False}

    port = _free_port(int(spec["port"]))
    cmd = local_app_command(tool, folder, port)
    log_path = home / ".astroai" / "lab" / "logs" / f"{tool}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    try:
        proc = popen(
            cmd,
            cwd=folder,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()
    pid = int(getattr(proc, "pid", 0) or 0)
    if not _wait_port(port):
        if pid > 0:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
        raise LabError(
            f"{binary} did not start.",
            hint=f"See {log_path}",
        )
    url = _local_url(tool, port)
    state[tool] = {"pid": pid, "port": port, "root": str(folder), "url": url}
    _write_state(home, state)
    return {"url": url, "opened": bool(opener(url)), "started": True}


def open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except webbrowser.Error:
        return False


def available(editor: str = "vscode", terminal: str = "auto") -> dict[str, bool]:
    """Best-effort availability for ``desk --doctor``."""
    out = {"editor": False, "terminal": False}
    try:
        editor_command(Path("."), editor)
        out["editor"] = True
    except LabError:
        pass
    try:
        terminal_command(Path("."), terminal)
        out["terminal"] = True
    except LabError:
        pass
    return out


__all__ = [
    "EDITOR_APPS",
    "TERMINAL_APPS",
    "available",
    "editor_command",
    "ensure_local_app",
    "local_app_command",
    "open_browser",
    "open_editor",
    "open_terminal",
    "terminal_command",
]
