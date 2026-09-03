"""Unit tests for dashboard URL resolution + local reverse proxy."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from astroai_workload.dashboard import (
    DashboardProxy,
    dashboard_iframe_html,
    jobs_url_from_connect,
    persist_connect_url,
    read_persisted_connect_url,
    resolve_dashboard_url,
)


class _Upstream:
    """Tiny upstream that echoes path + sets a frame-busting header."""

    def __init__(self) -> None:
        self.hits: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                upstream.hits.append(self.path)
                body = f"upstream:{self.path}".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # noqa: ANN001
                return

        upstream = self
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Upstream:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN001
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def test_jobs_url_from_connect() -> None:
    assert jobs_url_from_connect("https://canfar.net/session/contrib/abc") == (
        "https://canfar.net/session/contrib/abc/dashboard"
    )
    assert jobs_url_from_connect("https://canfar.net/session/contrib/abc/") == (
        "https://canfar.net/session/contrib/abc/dashboard"
    )


def test_persist_and_read_connect_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RAY_CLUSTER_ID", "c1")
    persist_connect_url("c1", "https://canfar.net/session/contrib/abc")
    assert read_persisted_connect_url() == "https://canfar.net/session/contrib/abc/"


def test_resolve_dashboard_url_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASTROAI_RAY_JOBS_ADDRESS", raising=False)
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("ASTROAI_RAY_JOBS_ADDRESS", "http://127.0.0.1:8265")
    assert resolve_dashboard_url() == "http://127.0.0.1:8265"


def test_resolve_dashboard_url_from_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ASTROAI_RAY_JOBS_ADDRESS", raising=False)
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("astroai_workload.dashboard._live_manager_connect", lambda: (None, False))
    persist_connect_url("c9", "https://canfar.net/session/contrib/xyz")
    assert resolve_dashboard_url() == "https://canfar.net/session/contrib/xyz/dashboard"


def test_resolve_dashboard_url_live_beats_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ASTROAI_RAY_JOBS_ADDRESS", raising=False)
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    persist_connect_url("old", "https://canfar.net/session/contrib/dead")
    monkeypatch.setattr(
        "astroai_workload.dashboard._live_manager_connect",
        lambda: ("https://canfar.net/session/contrib/live", True),
    )
    assert resolve_dashboard_url() == "https://canfar.net/session/contrib/live/dashboard"


def test_live_manager_connect_persists_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_workload.dashboard import _live_manager_connect

    monkeypatch.setenv("HOME", str(tmp_path))

    class _Ops:
        def auth_status(self):
            return type("A", (), {"authenticated": True})()

        def find_manager(self):
            return {
                "id": "sess-1",
                "connectURL": "https://canfar.net/session/contrib/live",
                "status": "Running",
            }

    monkeypatch.setattr(
        "astroai_workload.canfar_ops.CanfarOps",
        lambda: _Ops(),
    )
    url, visible = _live_manager_connect()
    assert visible is True
    assert url == "https://canfar.net/session/contrib/live"
    assert read_persisted_connect_url() == "https://canfar.net/session/contrib/live/"


def test_resolve_dashboard_url_pending_manager_not_stale_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ASTROAI_RAY_JOBS_ADDRESS", raising=False)
    monkeypatch.delenv("RAY_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    persist_connect_url("old", "https://canfar.net/session/contrib/dead")
    monkeypatch.setattr("astroai_workload.dashboard._live_manager_connect", lambda: (None, True))
    assert resolve_dashboard_url() is None


def test_iframe_html() -> None:
    html = dashboard_iframe_html("https://x/dashboard", height=600)
    assert 'src="https://x/dashboard"' in html
    assert 'height="600"' in html
    assert "iframe" in html


def test_proxy_forwards_and_strips_frame_headers() -> None:
    with _Upstream() as upstream:
        proxy = DashboardProxy(upstream.url, port=0)
        proxy.start()
        try:
            resp = httpx.get(f"{proxy.url}jobs", timeout=10)
            assert resp.status_code == 200
            assert resp.text == "upstream:/jobs"
            assert "X-Frame-Options" not in resp.headers
            assert "Content-Security-Policy" not in resp.headers
            assert resp.headers["Content-Type"] == "text/plain"
            assert upstream.hits == ["/jobs"]
        finally:
            proxy.stop()


def test_proxy_502_when_upstream_down() -> None:
    proxy = DashboardProxy("http://127.0.0.1:1", port=0)
    proxy.start()
    try:
        resp = httpx.get(f"{proxy.url}foo", timeout=10)
        assert resp.status_code == 502
    finally:
        proxy.stop()
