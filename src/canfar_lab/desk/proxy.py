"""Loopback cockpit proxy: dsh + Desk dock + action API (stdlib only).

Same discipline as the image's studio proxy: stdlib-only, HTTP relay with
WebSocket splicing, HTML injection. Security for this local one:

* binds ``127.0.0.1`` only;
* ``/__desk/*`` requires the per-run bearer token (embedded in the served
  page) and rejects foreign ``Origin`` headers — local CSRF / DNS-rebinding
  guards; no side effects on GET;
* everything else is relayed to dsh, and HTML responses get the dock injected.
"""

from __future__ import annotations

import contextlib
import hmac
import http.client
import json
import select
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from canfar_lab.desk.agents_page import load_agents_html
from canfar_lab.errors import LabError

DOCK_ASSET = Path(__file__).resolve().parent.parent / "data" / "desk" / "dock.html"
DOCK_SCRIPT_ID = "astroai-dock-config"

_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

DESK_BUTTONS: list[dict[str, Any]] = [
    {
        "id": "sync",
        "label": "Sync to CANFAR",
        "action": "sync_to_canfar",
        "kind": "primary",
        "title": "Commit and push, then ring the session through canfar data",
    },
    {
        "id": "launch-studio",
        "label": "Launch Studio",
        "action": "launch_studio",
        "title": "Open a running AstroAI Studio session, or create one",
    },
]


def load_dock_html(path: Path | None = None) -> str:
    return (path or DOCK_ASSET).read_text(encoding="utf-8")


def inject_dock(html: str, *, config: dict[str, Any], dock_html: str) -> str:
    payload = json.dumps(config).replace("</", "<\\/")
    snippet = (
        f'<script id="{DOCK_SCRIPT_ID}" type="application/json">{payload}</script>\n{dock_html}\n'
    )
    index = html.lower().rfind("</body>")
    if index >= 0:
        return html[:index] + snippet + html[index:]
    return html + snippet


def build_dock_config(
    *,
    token: str,
    mode: str = "desk",
    state_url: str = "/__desk/state",
    action_url: str = "/__desk/action",
    version: str | None = None,
    buttons: list[dict[str, Any]] | None = None,
    enabled: bool = False,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "token": token,
        "state_url": state_url,
        "action_url": action_url,
        "version": version,
        "enabled": enabled,
        "buttons": DESK_BUTTONS if buttons is None else buttons,
    }


class DeskProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        backend_host: str,
        backend_port: int,
        token: str,
        state_fn: Callable[[], dict[str, Any]],
        action_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
        dock_config: dict[str, Any],
        dock_html: str,
        say=None,  # noqa: ANN001 — Callable[[str], None]
    ) -> None:
        super().__init__(address, _DeskHandler)
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.token = token
        self.state_fn = state_fn
        self.action_fn = action_fn
        self.dock_config = dock_config
        self.dock_html = dock_html
        self.say = say or (lambda message: None)
        port = self.server_address[1]
        self.allowed_origins = frozenset(
            {
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
                f"http://[::1]:{port}",
            }
        )


class _DeskHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: DeskProxyServer  # ty: ignore[invalid-mutable-override]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib signature
        self.server.say("proxy: " + (format % args))

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    # ------------------------------------------------------------------ core

    def _handle(self) -> None:
        path = self.path
        with contextlib.suppress(ValueError):
            path = urlsplit(self.path).path
        if path.startswith("/__desk/"):
            if path == "/__desk/agents" and self.command == "GET":
                self._agents_page()
                return
            self._desk(path)
            return
        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            self._upgrade()
            return
        self._relay()

    def _agents_page(self) -> None:
        body = load_agents_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _desk(self, path: str) -> None:
        if not self._authorized():
            return
        if path == "/__desk/state" and self.command in ("GET", "HEAD"):
            self._json(self.server.state_fn())
            return
        if path.startswith("/__desk/action/") and self.command == "POST":
            name = path.rsplit("/", 1)[-1]
            payload = self._read_json()
            try:
                result = self.server.action_fn(name, payload)
            except LabError as exc:
                self._json({"ok": False, "error": str(exc), "hint": exc.hint}, status=400)
                return
            except Exception as exc:  # noqa: BLE001 — surface to the dock
                self._json({"ok": False, "error": str(exc)}, status=500)
                return
            self._json(result if isinstance(result, dict) else {"ok": True, "result": result})
            return
        self._json({"ok": False, "error": "not found"}, status=404)

    def _authorized(self) -> bool:
        token = str(self.headers.get("X-Desk-Token") or "")
        if not token or not hmac.compare_digest(token, str(self.server.token)):
            self._json({"ok": False, "error": "forbidden"}, status=403)
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin not in self.server.allowed_origins:
            self._json({"ok": False, "error": "forbidden origin"}, status=403)
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1_000_000))
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _json(self, obj: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _relay(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(min(length, 50_000_000)) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in _HOP_HEADERS
            and key.lower() not in ("content-length", "accept-encoding")
        }
        if body is not None:
            headers["Content-Length"] = str(len(body))
        conn = http.client.HTTPConnection(
            self.server.backend_host, self.server.backend_port, timeout=60
        )
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            self._json({"ok": False, "error": f"dsh backend unreachable: {exc}"}, status=502)
            return

        ctype = (resp.getheader("Content-Type") or "").lower()
        encoding = (resp.getheader("Content-Encoding") or "").lower()
        injectable = (
            "text/html" in ctype and encoding in ("", "identity") and self.command != "HEAD"
        )
        # Always inject: ``enabled`` only sets the initial Hide/Show state in
        # dock.html. The Studio handle turns the bar on without a restart.
        if injectable:
            charset = "utf-8"
            if "charset=" in ctype:
                charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
            try:
                text = data.decode(charset, errors="replace")
            except LookupError:
                text = data.decode("utf-8", errors="replace")
            text = inject_dock(
                text, config=self.server.dock_config, dock_html=self.server.dock_html
            )
            data = text.encode("utf-8", errors="replace")

        self.send_response(resp.status)
        for key, value in resp.getheaders():
            low = key.lower()
            if low in _HOP_HEADERS or low == "content-length":
                continue
            if low == "content-encoding" and injectable:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        conn.close()
        self.close_connection = True

    # -------------------------------------------------------------- websocket

    def _upgrade(self) -> None:
        try:
            backend = socket.create_connection(
                (self.server.backend_host, self.server.backend_port), timeout=15
            )
        except OSError as exc:
            self.server.say(f"websocket backend unreachable: {exc}")
            self.close_connection = True
            return
        lines = [f"{self.command} {self.path} {self.request_version}"]
        lines.extend(f"{key}: {value}" for key, value in self.headers.items())
        head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", errors="replace")
        try:
            backend.sendall(head)
            self.connection.settimeout(None)
            backend.settimeout(None)
            _splice(self.connection, backend)
        except (OSError, ValueError):
            pass
        finally:
            backend.close()
        self.close_connection = True


def _splice(client: socket.socket, backend: socket.socket) -> None:
    """Blind bidirectional byte relay for upgraded connections."""
    peers = [client, backend]
    while True:
        readable, _, _ = select.select(peers, [], [], 120)
        if not readable:
            return
        for source in readable:
            target = backend if source is client else client
            data = source.recv(65536)
            if not data:
                return
            target.sendall(data)


def start(
    *,
    port: int,
    backend_port: int,
    token: str,
    state_fn: Callable[[], dict[str, Any]],
    action_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
    dock_config: dict[str, Any],
    backend_host: str = "127.0.0.1",
    dock_html: str | None = None,
    say=None,  # noqa: ANN001 — Callable[[str], None]
) -> tuple[DeskProxyServer, threading.Thread]:
    server = DeskProxyServer(
        ("127.0.0.1", port),
        backend_host=backend_host,
        backend_port=backend_port,
        token=token,
        state_fn=state_fn,
        action_fn=action_fn,
        dock_config=dock_config,
        dock_html=dock_html if dock_html is not None else load_dock_html(),
        say=say,
    )
    thread = threading.Thread(target=server.serve_forever, name="desk-proxy", daemon=True)
    thread.start()
    return server, thread


def wait_for_port(
    host: str,
    port: int,
    *,
    timeout: float = 90.0,
    interval: float = 0.5,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(interval)
    return False


__all__ = [
    "DESK_BUTTONS",
    "DOCK_ASSET",
    "DOCK_SCRIPT_ID",
    "DeskProxyServer",
    "build_dock_config",
    "inject_dock",
    "load_dock_html",
    "start",
    "wait_for_port",
]
