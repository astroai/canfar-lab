"""canfar_lab.web.server: auth, routes, jobs and SSE over a real loopback server."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from canfar_lab.web import server as server_mod

TOKEN = "t0ken-for-tests-0123456789"


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[server_mod.LabServer]:
    scratch = tmp_path / "scratch"
    (scratch / "src").mkdir(parents=True)
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.setenv("CANFAR_LAB_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    srv = server_mod.make_server(port=0, token=TOKEN, capture=False)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _req(
    srv: server_mod.LabServer,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    token: str | None = TOKEN,
    write_header: bool = True,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | str, http.client.HTTPResponse]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if write_header:
        hdrs["X-Canfar-Lab"] = "1"
    data = None
    if body is not None:
        data = json.dumps(body)
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read().decode()
    try:
        return resp.status, json.loads(raw), resp
    except ValueError:
        return resp.status, raw, resp


def test_health_is_open_everything_else_needs_token(lab: server_mod.LabServer) -> None:
    assert _req(lab, "GET", "/api/health", token=None)[0] == 200
    assert _req(lab, "GET", "/api/state", token=None)[0] == 401
    assert _req(lab, "GET", "/api/state", token="wrong")[0] == 401
    assert _req(lab, "GET", "/", token=None)[0] == 401


def test_writes_need_custom_header(lab: server_mod.LabServer) -> None:
    status, body, _ = _req(lab, "POST", "/api/agents/restore", write_header=False)
    assert status == 403 and "X-Canfar-Lab" in body["error"]


def test_token_login_sets_cookie(lab: server_mod.LabServer) -> None:
    status, _, resp = _req(lab, "GET", f"/?token={TOKEN}", token=None)
    assert status == 303
    cookie = resp.getheader("Set-Cookie") or ""
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    status, page, _ = _req(lab, "GET", "/", token=None, headers={"Cookie": cookie.split(";")[0]})
    assert status == 200 and "<title>AstroAI Lab</title>" in page
    assert _req(lab, "GET", "/?token=nope", token=None)[0] == 401


def test_page_honours_forwarded_prefix(lab: server_mod.LabServer) -> None:
    _, page, resp = _req(lab, "GET", "/", headers={"X-Forwarded-Prefix": "/lab"})
    assert '<base href="/lab/">' in page
    assert "frame-ancestors" in (resp.getheader("Content-Security-Policy") or "")


def test_state_has_onboarding_and_six_agents(lab: server_mod.LabServer) -> None:
    status, state, _ = _req(lab, "GET", "/api/state")
    assert status == 200
    assert [a["id"] for a in state["agents"]] == ["claude", "codex", "agy", "opencode", "pi", "omp"]
    assert state["onboarding"]["steps"][0]["id"] == "agents"
    status, summary, _ = _req(lab, "GET", "/api/onboarding")
    assert "steps" not in summary and "pending" in summary


def test_unknown_routes_and_methods(lab: server_mod.LabServer) -> None:
    assert _req(lab, "GET", "/api/nope")[0] == 404
    assert _req(lab, "DELETE", "/api/state")[0] == 405
    status, body, _ = _req(lab, "POST", "/api/agents/cursor/install")
    assert status == 400 and "not a Lab agent" in body["error"]
    assert _req(lab, "POST", "/api/agents/claude/explode")[0] == 400


def test_key_set_and_clear_never_echo_value(
    lab: server_mod.LabServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secret = "sk-ant-test-0123456789"
    status, body, _ = _req(lab, "POST", "/api/keys/ANTHROPIC_API_KEY", {"value": secret})
    assert status == 200 and body["present"] and secret not in json.dumps(body)
    _, state, _ = _req(lab, "GET", "/api/state")
    assert secret not in json.dumps(state)
    assert {k["key"]: k["present"] for k in state["keys"]}["ANTHROPIC_API_KEY"]
    assert _req(lab, "DELETE", "/api/keys/ANTHROPIC_API_KEY")[1]["present"] is False


def test_job_busy_and_sse(lab: server_mod.LabServer, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()

    def _restore() -> dict[str, Any]:
        print("reinstalling claude", flush=True)
        gate.wait(10)
        return {"ok": True, "agents": ["claude"], "results": []}

    from canfar_lab.session import agents

    monkeypatch.setattr(agents, "restore", _restore)
    status, body, _ = _req(lab, "POST", "/api/agents/restore")
    assert status == 202
    job_id = body["job"]["id"]
    status, body, _ = _req(lab, "POST", "/api/agents/restore")
    assert status == 409 and "Busy" in body["error"]
    lab.runner.log(lab.runner.get(job_id), "line one")
    gate.set()

    conn = http.client.HTTPConnection("127.0.0.1", lab.server_address[1], timeout=10)
    conn.request("GET", f"/api/jobs/{job_id}/events", headers={"Authorization": f"Bearer {TOKEN}"})
    resp = conn.getresponse()
    assert resp.getheader("Content-Type") == "text/event-stream"
    stream = resp.read().decode()
    assert "event: line" in stream and '"line one"' in stream
    assert "event: done" in stream and '"state": "ok"' in stream

    _, body, _ = _req(lab, "GET", f"/api/jobs/{job_id}")
    assert body["job"]["result"]["agents"] == ["claude"]


def test_snapshot_round_trip_over_http(lab: server_mod.LabServer, tmp_path: Path) -> None:
    import shutil

    if not (shutil.which("tar") and shutil.which("zstd")):
        pytest.skip("needs tar and zstd")
    src = tmp_path / "scratch" / "src"
    (src / "proj").mkdir()
    (src / "proj" / "a.py").write_text("x = 1\n", encoding="utf-8")
    status, body, _ = _req(lab, "POST", "/api/snapshots", {"name": "web1", "include_dsh": False})
    assert status == 202
    assert lab.runner.wait(lab.runner.get(body["job"]["id"]), timeout=30)
    _, state, _ = _req(lab, "GET", "/api/state")
    assert [s["name"] for s in state["snapshots"]] == ["web1"]
    assert state["onboarding"]["steps"][-1]["status"] == "done"

    shutil.rmtree(src / "proj")
    _, state, _ = _req(lab, "GET", "/api/state")
    assert state["onboarding"]["steps"][-1]["action"]["op"] == "restore_snapshot"
    status, body, _ = _req(lab, "POST", "/api/snapshots/web1/restore", {})
    assert lab.runner.wait(lab.runner.get(body["job"]["id"]), timeout=30)
    assert (src / "proj" / "a.py").is_file()
    assert _req(lab, "POST", "/api/snapshots", {"name": "../x"})[0] == 400
    assert _req(lab, "DELETE", "/api/snapshots/web1")[1]["ok"]


def test_startup_job_restores_agents_and_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from canfar_lab.session import agents, snapshots

    monkeypatch.setattr(agents, "pending_restore", lambda home=None: ["claude"])
    monkeypatch.setattr(agents, "restore", lambda home=None: {"ok": True, "results": []})
    monkeypatch.setattr(snapshots, "list_snapshots", lambda root=None: [{"name": "last"}])
    restored: list[str] = []
    monkeypatch.setattr(
        snapshots, "restore", lambda name, **_: restored.append(name) or {"ok": True}
    )
    monkeypatch.setattr(server_mod, "code_state", lambda: {"src": "/s", "entries": [], "repos": []})
    out = server_mod.startup_job(restore_code=True)(None)  # type: ignore[arg-type]
    assert out["ok"] and restored == ["last"] and "agents" in out


def test_refuses_non_loopback_bind() -> None:
    from canfar_lab.errors import LabError

    with pytest.raises(LabError, match="loopback"):
        server_mod.make_server(host="0.0.0.0", port=0, token=TOKEN)


def test_token_file_is_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANFAR_LAB_WEB_TOKEN", TOKEN)
    path = tmp_path / "run" / "lab-web.token"
    assert server_mod.resolve_token(path) == TOKEN
    assert path.read_text().strip() == TOKEN
    assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.delenv("CANFAR_LAB_WEB_TOKEN")
    assert len(server_mod.resolve_token(None)) > 30


def test_forwarded_prefix_cannot_point_base_at_another_host(lab: server_mod.LabServer) -> None:
    _, page, _ = _req(lab, "GET", "/", headers={"X-Forwarded-Prefix": "//evil.example"})
    assert "evil.example" not in page and "<base" not in page
    _, page, _ = _req(lab, "GET", "/", headers={"X-Forwarded-Prefix": "/session/contrib/a1/lab"})
    assert '<base href="/session/contrib/a1/lab/">' in page


def test_login_token_is_redacted_in_the_log(
    lab: server_mod.LabServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []
    monkeypatch.setattr(
        server_mod.os, "write", lambda _fd, data: lines.append(data.decode()) or len(data)
    )
    handler = server_mod.LabHandler.__new__(server_mod.LabHandler)
    handler.client_address = ("127.0.0.1", 1)
    handler.server = lab
    handler.log_message('"%s" %s', f"GET /?token={TOKEN} HTTP/1.1", "303")
    assert TOKEN not in lines[0] and "token=<redacted>" in lines[0]


@pytest.mark.parametrize(
    "body",
    [{"include_dsh": "false"}, {"name": 5}, {"op": "cluster_start"}, {"overwrite": 1}],
)
def test_bad_snapshot_bodies_are_400(lab: server_mod.LabServer, body: dict[str, Any]) -> None:
    status, reply, _ = _req(lab, "POST", "/api/snapshots", body)
    if "op" in body:  # unknown keys are ignored, never passed through
        assert status in (202, 400) and lab.runner.latest().kind != "cluster"  # type: ignore[union-attr]
    else:
        assert status == 400, reply


def test_negative_content_length_is_rejected(lab: server_mod.LabServer) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", lab.server_address[1], timeout=5)
    conn.putrequest("POST", "/api/snapshots")
    for k, v in {
        "Authorization": f"Bearer {TOKEN}",
        "X-Canfar-Lab": "1",
        "Content-Length": "-1",
    }.items():
        conn.putheader(k, v)
    conn.endheaders()
    assert conn.getresponse().status == 400
