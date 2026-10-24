from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from canfar_lab.desk import apps
from canfar_lab.errors import LabError

MAC = "darwin"
LINUX = "linux"


def _which_factory(found: dict[str, str]):
    def which(name: str) -> str | None:
        return found.get(name)

    return which


def test_editor_command_uses_binary_on_path(tmp_path):
    which = _which_factory({"code": "/usr/bin/code"})
    assert apps.editor_command(tmp_path, "vscode", which=which) == ["/usr/bin/code", str(tmp_path)]


def test_editor_command_unknown_binary_lookup(tmp_path):
    which = _which_factory({"zed": "/opt/zed/zed"})
    assert apps.editor_command(tmp_path, "zed", which=which) == ["/opt/zed/zed", str(tmp_path)]


def test_editor_command_macos_falls_back_to_open(tmp_path):
    which = _which_factory({})
    cmd = apps.editor_command(tmp_path, "cursor", platform=MAC, which=which)
    assert cmd == ["open", "-a", "Cursor", str(tmp_path)]


def test_editor_command_linux_missing_raises(tmp_path):
    which = _which_factory({})
    with pytest.raises(LabError) as err:
        apps.editor_command(tmp_path, "vscode", platform=LINUX, which=which)
    assert "apps_editor" in (err.value.hint or "")


def test_editor_command_custom_command_line(tmp_path):
    cmd = apps.editor_command(tmp_path, "emacs -nw", which=_which_factory({}))
    assert cmd == ["emacs", "-nw", str(tmp_path)]


def test_terminal_darwin_auto_prefers_ghostty(tmp_path):
    installed = {"Ghostty": True, "iTerm": True}
    cmd = apps.terminal_command(
        tmp_path,
        "auto",
        platform=MAC,
        app_installed=lambda app: installed.get(app, False),
    )
    assert cmd == ["open", "-a", "Ghostty", str(tmp_path)]


def test_terminal_darwin_auto_skips_missing_apps(tmp_path):
    cmd = apps.terminal_command(tmp_path, "auto", platform=MAC, app_installed=lambda app: False)
    assert cmd == ["open", "-a", "Terminal", str(tmp_path)]


def test_terminal_darwin_explicit_missing_raises(tmp_path):
    with pytest.raises(LabError):
        apps.terminal_command(tmp_path, "ghostty", platform=MAC, app_installed=lambda app: False)


def test_terminal_darwin_unknown_raises(tmp_path):
    with pytest.raises(LabError):
        apps.terminal_command(tmp_path, "tmux", platform=MAC)


def test_terminal_linux_explicit(tmp_path):
    which = _which_factory({"kitty": "/usr/bin/kitty"})
    cmd = apps.terminal_command(tmp_path, "kitty", platform=LINUX, which=which)
    assert cmd == ["/usr/bin/kitty"]


def test_terminal_linux_explicit_missing_raises(tmp_path):
    with pytest.raises(LabError):
        apps.terminal_command(tmp_path, "kitty", platform=LINUX, which=_which_factory({}))


def test_terminal_linux_env_preference(tmp_path):
    which = _which_factory({"wezterm": "/usr/bin/wezterm", "xterm": "/usr/bin/xterm"})
    cmd = apps.terminal_command(
        tmp_path, "auto", platform=LINUX, which=which, environ={"TERMINAL": "wezterm"}
    )
    assert cmd == ["/usr/bin/wezterm"]


def test_terminal_linux_fallback_list(tmp_path):
    which = _which_factory({"x-terminal-emulator": "/usr/bin/x-terminal-emulator"})
    cmd = apps.terminal_command(tmp_path, "auto", platform=LINUX, which=which, environ={})
    assert cmd == ["/usr/bin/x-terminal-emulator"]


def test_terminal_linux_none_found_raises(tmp_path):
    with pytest.raises(LabError):
        apps.terminal_command(
            tmp_path, "auto", platform=LINUX, which=_which_factory({}), environ={}
        )


def test_open_editor_runs_runner(tmp_path):
    seen: list[tuple[list[str], Path]] = []

    def runner(cmd, cwd):
        seen.append((list(cmd), Path(cwd)))
        return 0

    which = _which_factory({"code": "/usr/bin/code"})
    description = apps.open_editor(tmp_path, "vscode", runner=runner, which=which)
    assert description == f"/usr/bin/code {tmp_path}"
    assert seen and seen[0][1] == tmp_path.resolve()


def test_open_editor_failure_raises(tmp_path):
    with pytest.raises(LabError):
        apps.open_editor(
            tmp_path,
            "vscode",
            runner=lambda cmd, cwd: 1,
            which=_which_factory({"code": "/usr/bin/code"}),
        )


def test_open_terminal_runs_runner(tmp_path):
    which = _which_factory({"kitty": "/usr/bin/kitty"})
    description = apps.open_terminal(
        tmp_path, "kitty", runner=lambda cmd, cwd: 0, platform=LINUX, which=which
    )
    assert description == "/usr/bin/kitty"


def test_open_browser_swallows_webbrowser_errors(monkeypatch):
    opener = MagicMock(return_value=True)
    monkeypatch.setattr(apps.webbrowser, "open", opener)
    assert apps.open_browser("http://127.0.0.1:5080/") is True

    def boom(url):
        raise apps.webbrowser.Error("no display")

    monkeypatch.setattr(apps.webbrowser, "open", boom)
    assert apps.open_browser("http://127.0.0.1:5080/") is False


def test_local_app_command_is_loopback(tmp_path):
    jupyter = apps.local_app_command("jupyter", tmp_path, 8888)
    marimo = apps.local_app_command("marimo", tmp_path, 2718)
    assert jupyter[:3] == ["jupyter", "lab", "--ip"]
    assert "127.0.0.1" in jupyter
    assert "--no-browser" in jupyter
    assert "--headless" in marimo
    assert "--no-token" in marimo
    assert str(tmp_path) in marimo


def test_local_app_missing_binary(tmp_path):
    with pytest.raises(LabError, match="not installed"):
        apps.ensure_local_app(
            "jupyter",
            tmp_path,
            home=tmp_path,
            opener=lambda url: True,
            which=lambda name: None,
        )


def test_local_app_reuses_a_live_server(tmp_path, monkeypatch):
    folder = tmp_path.resolve()
    state = {
        "marimo": {
            "pid": 7,
            "port": 2718,
            "root": str(folder),
            "url": "http://127.0.0.1:2718/",
        }
    }
    apps._write_state(tmp_path, state)
    monkeypatch.setattr(apps, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(apps, "_port_open", lambda port: True)
    opened: list[str] = []

    def refuse(*args, **kwargs):
        raise AssertionError("restarted")

    out = apps.ensure_local_app(
        "marimo",
        tmp_path,
        home=tmp_path,
        opener=lambda url: opened.append(url) or True,
        popen=refuse,
        which=lambda name: "/usr/bin/marimo",
    )
    assert out["started"] is False
    assert opened == ["http://127.0.0.1:2718/"]


def test_available_reports_state(monkeypatch):
    monkeypatch.setattr(apps, "editor_command", lambda path, editor: ["code"])
    monkeypatch.setattr(apps, "terminal_command", lambda path, terminal: ["kitty"])
    assert apps.available("vscode", "auto") == {"editor": True, "terminal": True}

    def no_editor(path, editor):
        raise LabError("nope")

    def no_terminal(path, terminal):
        raise LabError("nope")

    monkeypatch.setattr(apps, "editor_command", no_editor)
    monkeypatch.setattr(apps, "terminal_command", no_terminal)
    assert apps.available() == {"editor": False, "terminal": False}
