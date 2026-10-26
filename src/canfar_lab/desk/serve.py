"""Run local dsh behind the Desk dock until that process exits."""

from __future__ import annotations

import contextlib
import secrets
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from canfar_lab import ui
from canfar_lab.desk import actions as actions_mod
from canfar_lab.desk import proxy as proxy_mod


def serve_with_dock(
    *,
    cmd: list[str],
    cwd: Path,
    env: Mapping[str, str],
    public_port: int,
    backend_port: int,
    root: Path,
    settings: Any,
    dsh_bin: str | None,
    dsh_spec: str | None,
    release: Any,
    opener: Callable[[str], object] | None = None,
) -> int:
    """Serve ``cmd`` on ``backend_port`` and the dock on ``public_port``.

    The browser URL is the public port. ``opener`` receives that URL once the
    proxy is listening.
    """
    proc = subprocess.Popen(list(cmd), cwd=cwd, env=dict(env))
    if not proxy_mod.wait_for_port("127.0.0.1", backend_port, timeout=120):
        ui.print_warn("dsh has not answered yet; the page will load once it does")

    token = secrets.token_urlsafe(24)
    actions = actions_mod.DeskActions(
        root=root,
        settings=settings,
        dsh_bin=dsh_bin,
        dsh_spec=dsh_spec,
        release=release,
        say=lambda message: None,
    )
    version = getattr(release, "version", None)
    server, _thread = proxy_mod.start(
        port=public_port,
        backend_port=backend_port,
        token=token,
        state_fn=actions.state,
        action_fn=actions.dispatch,
        dock_config=proxy_mod.build_dock_config(
            token=token,
            version=version,
            enabled=bool(getattr(settings, "desk", False)),
        ),
        say=lambda message: None,
    )
    if opener is not None:
        opener(f"http://127.0.0.1:{public_port}/")
    try:
        while proc.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        proc.terminate()
    finally:
        server.shutdown()
        if proc.poll() is None:
            proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    code = proc.returncode
    if code in (None, 0) or (isinstance(code, int) and code < 0):
        return 0
    return code
