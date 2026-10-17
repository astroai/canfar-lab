"""Desktop app launching for the Desk dock: editor, terminal, browser.

macOS uses ``open -a <App> <dir>`` (Ghostty, iTerm, Terminal all accept a
folder). Linux uses ``$TERMINAL`` or a known emulator with ``cwd=dir``.
Everything is injectable so tests can pin a platform without a display.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
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
    "open_browser",
    "open_editor",
    "open_terminal",
    "terminal_command",
]
