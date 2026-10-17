"""Desk action API handlers (behind the loopback token).

The dock talks to these through ``POST /__desk/action/<name>``. Every handler
returns a JSON-able dict; failures use ``{"ok": false, "error": …}`` for
expected states (no session, no auth) and raise :class:`LabError` for bad
input.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time as time_mod
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from canfar_lab.config.settings import get_settings
from canfar_lab.desk import apps
from canfar_lab.desk import state as state_mod
from canfar_lab.errors import LabError
from canfar_lab.session_urls import contrib_url
from canfar_lab.sync import bus as bus_mod
from canfar_lab.sync.catalog import load_catalog
from canfar_lab.sync.engine import SyncEngine

#: Tools that open *in the session* from the desk dock.
TOOL_SUFFIXES = {"marimo": "/marimo/", "jupyter": "/jupyter/lab", "hub": "/hub/"}

STATE_CACHE_SECONDS = 5.0


class DeskActions:
    def __init__(
        self,
        *,
        root: Path,
        home: Path | None = None,
        settings=None,  # noqa: ANN001 — LabSettings
        opener: Callable[[str], bool] = webbrowser.open,
        ops_factory=None,  # noqa: ANN001 — callable → CanfarOps
        engine_factory=None,  # noqa: ANN001 — callable(SyncEngine)
        popen: Callable[..., object] = subprocess.Popen,
        dsh_bin: str | None = None,
        dsh_spec: str | None = None,
        release=None,  # noqa: ANN001 — dsh_release.Release
        say=None,  # noqa: ANN001
    ) -> None:
        self.root = Path(root)
        self.home = Path(home) if home else Path.home()
        self.settings = settings or get_settings()
        self._opener = opener
        self._ops_factory = ops_factory or state_mod.canfar_ops
        self._engine_factory = engine_factory
        self._popen = popen
        self.dsh_bin = dsh_bin
        self.dsh_spec = dsh_spec
        self.release = release
        self._say = say or (lambda message: None)
        self._state_cache: tuple[float, dict[str, Any]] | None = None

    # ------------------------------------------------------------------ bits

    def _mailbox(self) -> Path:
        return bus_mod.mailbox_dir(
            home=self.home, override=getattr(self.settings, "sync_mailbox", None)
        )

    def _engine(self, side: str = "desk") -> SyncEngine:
        if self._engine_factory is not None:
            return self._engine_factory(side)
        return SyncEngine(
            self.root,
            load_catalog(self.root),
            side=side,
            exclude=list(getattr(self.settings, "sync_exclude", []) or []),
            say=self._say,
        )

    def _session(self) -> dict[str, Any] | None:
        return state_mod.studio_session(
            self._ops_factory(), name=str(getattr(self.settings, "session_name", "astroai-studio"))
        )

    def state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        moment = time_mod.monotonic()
        if self._state_cache is not None and moment - self._state_cache[0] < STATE_CACHE_SECONDS:
            return self._state_cache[1]
        try:
            data = state_mod.collect(
                root=self.root,
                mailbox=self._mailbox(),
                settings=self.settings,
                dsh_bin=self.dsh_bin,
                dsh_spec=self.dsh_spec,
                release=self.release,
            )
        except Exception as exc:  # noqa: BLE001 — the dock must always render
            data = {"ok": False, "error": str(exc), "root": str(self.root)}
        self._state_cache = (moment, data)
        return data

    # -------------------------------------------------------------- dispatch

    def dispatch(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        handlers = {
            "state": self.state,
            "sync_to_canfar": self.sync_to_canfar,
            "open_tool": self.open_tool,
            "open_editor": self.open_editor,
            "open_terminal": self.open_terminal,
            "cluster": self.cluster,
            "login": self.login,
        }
        handler = handlers.get(str(action))
        if handler is None:
            raise LabError(f"unknown desk action: {action}")
        self._state_cache = None  # any action invalidates the chip cache
        return handler(payload or {})

    # --------------------------------------------------------------- actions

    def sync_to_canfar(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Publish (commit+push) then drop a request for the session watcher."""
        engine = self._engine("desk")
        report = engine.publish(
            repos=payload.get("repos"),
            mode=str(getattr(self.settings, "sync_publish_mode", "commit")),
            message=payload.get("message"),
            dry_run=bool(payload.get("dry_run")),
        )
        result: dict[str, Any] = {"ok": True, "report": report.to_dict()}
        if payload.get("dry_run"):
            return result

        mailbox = self._mailbox()
        bus_mod.ensure(mailbox)
        ready = bus_mod.read_ready(mailbox=mailbox)
        repos = [
            {"slug": rec.slug, "head": rec.head} for rec in report.repos if rec.status != "missing"
        ]
        request = bus_mod.request_payload(side=bus_mod.DESK, root=self.root, repos=repos)
        path = bus_mod.enqueue("requests", request, mailbox=mailbox)
        shared = (
            bool(getattr(self.settings, "sync_mailbox", None))
            or bool(os.environ.get(bus_mod.MAILBOX_ENV, "").strip())
            or bool(os.environ.get("SKAHA_SESSIONID") or os.environ.get("skaha_sessionid"))  # noqa: SIM112
        )
        result.update(
            {
                "request": path.name,
                "mailbox": str(mailbox),
                "session_ready": bool(ready),
                "shared_mailbox": bool(shared),
            }
        )
        if not ready:
            result["hint"] = (
                "Session watcher not seen yet — the request keeps for 30 min and is "
                "picked up at session boot."
                if shared
                else (
                    "Mailbox is laptop-local: the session cannot read it. The push is on "
                    "GitHub, so `canfar lab sync` inside the session still pulls it; "
                    "configure sync_mailbox (SSHFS of arc:/home/$USER) for the doorbell."
                )
            )
        return result

    def open_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool = str(payload.get("tool") or "")
        suffix = TOOL_SUFFIXES.get(tool)
        if suffix is None:
            raise LabError(f"unknown tool: {tool}")
        session = self._session()
        if not session or not session.get("id"):
            return {
                "ok": False,
                "error": "No running Studio session",
                "hint": "Press `Canfar sync` to create it, then retry.",
            }
        base = str(session.get("url") or contrib_url(str(session["id"])))
        url = f"{base}{suffix}"
        opened = bool(self._opener(url))
        return {"ok": True, "tool": tool, "url": url, "opened": opened, "session": session}

    def open_editor(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = Path(str(payload.get("path") or self.root)).expanduser()
        if not target.is_dir():
            return {"ok": False, "error": f"not a directory: {target}"}
        description = apps.open_editor(target, str(getattr(self.settings, "apps_editor", "vscode")))
        return {"ok": True, "path": str(target), "opened": description}

    def open_terminal(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = Path(str(payload.get("path") or self.root)).expanduser()
        if not target.is_dir():
            return {"ok": False, "error": f"not a directory: {target}"}
        description = apps.open_terminal(
            target, str(getattr(self.settings, "apps_terminal", "auto"))
        )
        return {"ok": True, "path": str(target), "opened": description}

    def login(self, payload: dict[str, Any]) -> dict[str, Any]:
        description = apps.open_terminal(
            self.root, str(getattr(self.settings, "apps_terminal", "auto"))
        )
        return {
            "ok": True,
            "command": "canfar login",
            "terminal": description,
            "hint": "Finish the device login in the terminal, then press Canfar sync again.",
        }

    def cluster(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "status")
        if mode == "status":
            try:
                row = self._ops_factory().find_manager()
            except Exception as exc:  # noqa: BLE001 — no client / no auth
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "mode": mode, "manager": row}
        if mode not in ("start", "stop"):
            raise LabError(f"unknown cluster mode: {mode}")
        log = self.home / ".astroai" / "lab" / "desk-cluster.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "canfar_lab.cli.main", "cluster", mode]
        with log.open("ab") as handle:
            self._popen(cmd, stdout=handle, stderr=handle, start_new_session=True)
        return {
            "ok": True,
            "mode": mode,
            "log": str(log),
            "hint": "watch with: canfar lab cluster status",
        }


__all__ = ["TOOL_SUFFIXES", "DeskActions"]
