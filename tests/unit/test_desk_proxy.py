from __future__ import annotations

import gzip
import http.client
import http.server
import json
import socket
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from canfar_lab.desk import proxy
from canfar_lab.errors import LabError

TOKEN = "tok-test-123"

HTML_PAGE = b"<html><body><h1>dsh</h1></body></html>"


class _Backend(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    accept_encoding: str | None = None

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send(self, status: int, ctype: str, body: bytes, *, encoding: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        _Backend.accept_encoding = self.headers.get("Accept-Encoding")
        path = urlsplit(self.path).path
        if path == "/page":
            self._send(200, "text/html; charset=utf-8", HTML_PAGE)
        elif path == "/json":
            self._send(200, "application/json", b'{"hello": 1}')
        elif path == "/gz":
            self._send(
                200,
                "text/html; charset=utf-8",
                gzip.compress(HTML_PAGE),
                encoding="gzip",
            )
        else:
            self._send(404, "text/plain", b"nope")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self._send(200, "application/json", json.dumps({"echo": body.decode()}).encode())


@pytest.fixture
def backend():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def cockpit(backend):
    calls: list[tuple[str, dict]] = []
    state = {"ok": True, "root": "/tmp/demo", "mode": "desk"}

    def state_fn():
        return dict(state)

    def action_fn(name, payload):
        calls.append((name, payload))
        return {"ok": True, "action": name, "got": payload}

    server, _thread = proxy.start(
        port=0,
        backend_port=backend,
        token=TOKEN,
        state_fn=state_fn,
        action_fn=action_fn,
        dock_config=proxy.build_dock_config(token=TOKEN, version="1.2.3", enabled=True),
        dock_html="<script>window.__dock_loaded = true;</script>",
    )
    yield SimpleNamespace(port=server.server_address[1], server=server, calls=calls, state=state)
    server.shutdown()
    server.server_close()


def _request(port: int, path: str, *, method: str = "GET", headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    out = (resp.status, dict(resp.getheaders()), data)
    conn.close()
    return out


def _auth(origin: str | None = None) -> dict[str, str]:
    headers = {"X-Desk-Token": TOKEN}
    if origin:
        headers["Origin"] = origin
    return headers


# ------------------------------------------------------------------ injection


def test_html_request_does_not_ask_for_compression(cockpit):
    _request(cockpit.port, "/page", headers={"Accept-Encoding": "gzip"})
    assert _Backend.accept_encoding in (None, "identity")


def test_html_gets_dock_injected(cockpit):
    status, headers, body = _request(cockpit.port, "/page")
    assert status == 200
    text = body.decode("utf-8")
    assert 'id="astroai-dock-config"' in text
    assert TOKEN in text
    assert "__dock_loaded" in text
    assert text.index("astroai-dock-config") < text.index("</body>")
    assert headers["Content-Length"] == str(len(body))


def test_json_is_not_touched(cockpit):
    status, headers, body = _request(cockpit.port, "/json")
    assert status == 200
    assert body == b'{"hello": 1}'
    assert "astroai-dock-config" not in body.decode("utf-8")
    assert "application/json" in headers["Content-Type"]


def test_gzipped_html_is_relayed_untouched(cockpit):
    status, headers, body = _request(cockpit.port, "/gz")
    assert status == 200
    assert headers.get("Content-Encoding") == "gzip"
    assert b"astroai-dock-config" not in body
    assert gzip.decompress(body) == HTML_PAGE


def test_post_body_relayed(cockpit):
    status, _headers, body = _request(
        cockpit.port,
        "/echo",
        method="POST",
        headers={"Content-Type": "text/plain"},
        body=b"hello",
    )
    assert status == 200
    assert json.loads(body) == {"echo": "hello"}


def test_backend_down_relays_502():
    # A port that is listening now but closed by the time the proxy relays.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    server, _thread = proxy.start(
        port=0,
        backend_port=dead_port,
        token=TOKEN,
        state_fn=dict,
        action_fn=lambda name, payload: {},
        dock_config={},
        dock_html="",
    )
    try:
        status, _headers, body = _request(server.server_address[1], "/")
        assert status == 502
        assert "unreachable" in json.loads(body)["error"]
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------- security


def test_state_requires_token(cockpit):
    status, _headers, _body = _request(cockpit.port, "/__desk/state")
    assert status == 403

    status, _headers, _body = _request(
        cockpit.port, "/__desk/state", headers={"X-Desk-Token": "wrong"}
    )
    assert status == 403


def test_state_with_token(cockpit):
    status, _headers, body = _request(cockpit.port, "/__desk/state", headers=_auth())
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["root"] == "/tmp/demo"


def test_state_rejects_foreign_origin(cockpit):
    status, _headers, _body = _request(
        cockpit.port, "/__desk/state", headers=_auth("http://evil.example")
    )
    assert status == 403


def test_state_accepts_loopback_origin(cockpit):
    origin = f"http://127.0.0.1:{cockpit.port}"
    status, _headers, _body = _request(cockpit.port, "/__desk/state", headers=_auth(origin))
    assert status == 200


def test_action_dispatch(cockpit):
    body = json.dumps({"message": "hi"})
    status, _headers, data = _request(
        cockpit.port,
        "/__desk/action/sync_to_canfar",
        method="POST",
        headers={**_auth(), "Content-Type": "application/json"},
        body=body,
    )
    assert status == 200
    assert json.loads(data) == {
        "ok": True,
        "action": "sync_to_canfar",
        "got": {"message": "hi"},
    }
    assert cockpit.calls == [("sync_to_canfar", {"message": "hi"})]


def test_action_laberror_maps_to_400(cockpit):
    def bad(name, payload):
        raise LabError("bad action", hint="use a real one")

    cockpit.server.action_fn = bad
    status, _headers, data = _request(
        cockpit.port, "/__desk/action/nope", method="POST", headers=_auth()
    )
    assert status == 400
    payload = json.loads(data)
    assert payload["ok"] is False
    assert payload["error"].startswith("bad action")
    assert payload["hint"] == "use a real one"


def test_action_unexpected_error_maps_to_500(cockpit):
    def boom(name, payload):
        raise RuntimeError("kaboom")

    cockpit.server.action_fn = boom
    status, _headers, data = _request(
        cockpit.port, "/__desk/action/x", method="POST", headers=_auth()
    )
    assert status == 500
    assert json.loads(data)["error"] == "kaboom"


def test_unknown_desk_path_404(cockpit):
    status, _headers, _body = _request(cockpit.port, "/__desk/nope", headers=_auth())
    assert status == 404


def test_action_requires_post(cockpit):
    status, _headers, _body = _request(
        cockpit.port, "/__desk/action/sync_to_canfar", headers=_auth()
    )
    assert status == 404


# ------------------------------------------------------------------ websocket


class _WsEcho(threading.Thread):
    """One-shot raw server: answers the upgrade, then echoes bytes."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.head = b""

    def run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            self.head = head
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
            )
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                conn.sendall(data)


def test_websocket_is_spliced():
    ws = _WsEcho()
    ws.start()
    # The proxy relays to its configured backend, so point a dedicated proxy
    # at the echo server.
    server, _thread = proxy.start(
        port=0,
        backend_port=ws.port,
        token=TOKEN,
        state_fn=dict,
        action_fn=lambda name, payload: {},
        dock_config={},
        dock_html="",
    )
    try:
        client = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10)
        with client:
            client.sendall(
                b"GET /ws HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                assert chunk, "proxy closed during handshake"
                head += chunk
            assert b"101" in head
            client.sendall(b"ping")
            assert client.recv(4) == b"ping"
    finally:
        server.shutdown()
        server.server_close()
    assert b"Upgrade: websocket" in ws.head


# ----------------------------------------------------------------- assets/api


def test_dock_asset_exists_and_carries_the_dock():
    html = proxy.load_dock_html()
    assert "astroai-dock" in html
    assert proxy.DOCK_SCRIPT_ID in html
    assert "__astroaiDockLoaded" in html


def test_inject_dock_escapes_config_and_places_at_body_end():
    html = "<html><body>hi</body></html>"
    out = proxy.inject_dock(
        html,
        config={"token": "t", "evil": "</script><script>alert(1)</script>"},
        dock_html="<script>dock()</script>",
    )
    assert "</script><script>alert(1)</script>" not in out
    assert "<\\/script>" in out
    assert out.endswith("</body></html>")
    assert out.index("astroai-dock-config") < out.index("</body>")


def test_inject_dock_appends_without_body():
    out = proxy.inject_dock("<div>page</div>", config={}, dock_html="<b>dock</b>")
    assert out.startswith("<div>page</div>")
    assert "astroai-dock-config" in out
    assert out.endswith("<b>dock</b>\n")


def test_desk_buttons_shape():
    ids = [button["id"] for button in proxy.DESK_BUTTONS]
    assert ids == ["sync", "launch-studio"]
    labels = {button["id"]: button["label"] for button in proxy.DESK_BUTTONS}
    assert labels["sync"] == "Sync to CANFAR"
    assert labels["launch-studio"] == "Launch Studio"
    dock = proxy.load_dock_html()
    assert "AstroAI Desk" in dock
    assert 'id="astroai-dock-handle"' in dock
    assert ">Desk</button>" in dock


def test_html_injects_dock_when_disabled(backend):
    """Desk off still injects the handle so the user can open the bar live."""
    config = proxy.build_dock_config(token=TOKEN)
    assert config["enabled"] is False
    server, _thread = proxy.start(
        port=0,
        backend_port=backend,
        token=TOKEN,
        state_fn=dict,
        action_fn=lambda name, payload: {"ok": True},
        dock_config=config,
        dock_html="<b>dock</b>",
    )
    try:
        status, _headers, body = _request(server.server_address[1], "/page")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    text = body.decode("utf-8")
    assert "astroai-dock-config" in text
    assert '"enabled": false' in text
    assert "<b>dock</b>" in text


def test_agents_page_is_local_html(cockpit):
    status, _headers, body = _request(cockpit.port, "/__desk/agents")
    assert status == 200
    assert b"AstroAI Agents" in body
    assert TOKEN.encode() not in body


def test_build_dock_config_overrides_buttons():
    config = proxy.build_dock_config(token="t", buttons=[{"id": "x"}])
    assert config["token"] == "t"
    assert config["state_url"] == "/__desk/state"
    assert config["action_url"] == "/__desk/action"
    assert config["buttons"] == [{"id": "x"}]


def test_wait_for_port(backend):
    assert proxy.wait_for_port("127.0.0.1", backend, timeout=2.0) is True


def test_wait_for_port_times_out():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead = sock.getsockname()[1]
    sock.close()
    assert proxy.wait_for_port("127.0.0.1", dead, timeout=0.3, interval=0.05) is False
