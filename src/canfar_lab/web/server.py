"""``canfar lab web``: the Lab tab of the dsh image (stdlib HTTP, one static page).

Routes (all JSON unless noted)::

    GET    /                         the page (HTML)
    GET    /api/health               liveness, no auth
    GET    /api/state                agents, keys, code, snapshots, job, onboarding
    GET    /api/onboarding           just the onboarding summary (tab badge)
    GET    /api/cluster              Ray status + recent jobs (slow; polled separately)
    POST   /api/agents/restore       job: reinstall remembered agents
    POST   /api/agents/<id>/<verb>   job: install | update | remove | setup
    POST   /api/keys/<NAME>          {"value": "..."}; empty value clears
    DELETE /api/keys/<NAME>
    POST   /api/snapshots            job: save  {"name"?, "include_dsh"?, "include_envs"?}
    POST   /api/snapshots/<n>/restore  job: restore {"overwrite"?}
    DELETE /api/snapshots/<n>
    POST   /api/cluster/start|stop   job
    GET    /api/jobs                 recent jobs;  GET /api/jobs/<id>
    GET    /api/jobs/<id>/events     SSE: ``line`` events, then one ``done``

Auth: binds loopback, refuses non-loopback peers, and requires the
per-process bearer token (``Authorization: Bearer …``, injected by the image
proxy) or the ``lab_token`` cookie set by ``GET /?token=…`` (laptop use).
Writes also need the ``X-Canfar-Lab: 1`` header, which a cross-site form or
fetch cannot send without a CORS preflight this server never approves.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from canfar_lab.errors import LabError
from canfar_lab.web import onboarding
from canfar_lab.web.jobs import Busy, Job, JobRunner

PAGE = Path(__file__).resolve().parent.parent / "data" / "web" / "index.html"
DEFAULT_PORT = 4792
COOKIE = "lab_token"
WRITE_HEADER = "X-Canfar-Lab"
MAX_BODY = 64 * 1024
SSE_HEARTBEAT_SEC = 15.0
AGENT_VERBS = ("install", "update", "remove", "setup", "clean-home", "rehome")
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def session_info() -> dict[str, Any]:
    """Session id, $SCRATCH and its free space, for the page header."""
    import shutil

    from canfar_lab.session import snapshots

    scratch = snapshots.scratch_dir()
    free = None
    if scratch is not None:
        with contextlib.suppress(OSError):
            free = shutil.disk_usage(scratch).free
    return {
        "id": (os.environ.get("skaha_sessionid") or "").strip() or None,  # noqa: SIM112
        "scratch": str(scratch) if scratch else None,
        "scratch_free": free,
        "home": str(Path.home()),
    }


def code_state(*, with_repos: bool = True) -> dict[str, Any]:
    """What is in ``$SCRATCH/src``. *with_repos* adds git state per project (slower)."""
    from canfar_lab.session import snapshots

    scratch = snapshots.scratch_dir()
    src = (scratch / "src") if scratch else None
    entries: list[str] = []
    if src is not None and src.is_dir():
        entries = sorted(p.name for p in src.iterdir() if not p.name.startswith("."))
    repos = snapshots.repo_summary(src) if with_repos and src is not None and src.is_dir() else []
    by_path = {r["path"]: r for r in repos}
    projects = [
        {"name": name, "git": name in by_path, **(by_path.get(name) or {})} for name in entries
    ]
    return {
        "src": str(src) if src else None,
        "entries": entries,
        "repos": repos,
        "projects": projects,
    }


def snapshot_rows() -> list[dict[str, Any]]:
    from canfar_lab.session import snapshots

    keep = ("name", "created_at", "src_bytes", "dsh_bytes", "repos", "dsh_state", "path")
    return [{k: row.get(k) for k in keep} for row in snapshots.list_snapshots()]


def lab_state(
    runner: JobRunner,
    *,
    returning: bool | None = None,
    started_at: float | None = None,
    restore_code: bool = True,
    with_repos: bool = True,
) -> dict[str, Any]:
    from canfar_lab.session import agents
    from canfar_lab.session import snapshots as snap_mod
    from canfar_lab.version import display_version

    agent_state = agents.status()
    snaps = snapshot_rows()
    code = code_state(with_repos=with_repos)
    latest = runner.latest()
    job = latest.to_dict(tail=40) if latest else None
    return {
        "ok": True,
        "version": display_version(),
        "user": os.environ.get("USER", ""),
        "session": session_info(),
        "snapshot_root": str(snap_mod.snapshot_root()),
        "agents": agent_state["agents"],
        "keys": agent_state["keys"],
        "code": code,
        "snapshots": snaps,
        "job": job,
        "onboarding": onboarding.build(
            agents=agent_state["agents"],
            keys=agent_state["keys"],
            snapshots=snaps,
            code=code,
            job=job,
            returning=returning,
            started_at=started_at,
            restore_code=restore_code,
        ),
    }


def session_was_used() -> bool | None:
    """Did an earlier session leave anything behind? Decided once, at start."""
    from canfar_lab.session import agents

    try:
        state = agents.status()
        return onboarding.is_returning(
            agents=state["agents"], keys=state["keys"], snapshots=snapshot_rows()
        )
    except Exception:  # noqa: BLE001 — fall back to inferring per request
        return None


def cluster_state() -> dict[str, Any]:
    from canfar_lab.session import cluster

    status = cluster.status()
    status.pop("raw", None)
    jobs = cluster.jobs(status.get("address"), limit=10) if status.get("running") else {"jobs": []}
    return {**status, "jobs": jobs.get("jobs") or []}


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def startup_job(*, restore_code: bool) -> Any:
    """Session start: reinstall remembered agents; optionally bring code back."""

    def _run(job: Job) -> dict[str, Any]:
        from canfar_lab.session import agents, snapshots

        out: dict[str, Any] = {"ok": True}
        pending = agents.pending_restore()
        if pending:
            print(f"Reinstalling {', '.join(pending)} …", flush=True)
            out["agents"] = agents.restore()
            out["ok"] = bool(out["agents"]["ok"])
        if restore_code:
            code = code_state()
            snaps = snapshots.list_snapshots()
            if snaps and not code["entries"]:
                name = snaps[0]["name"]
                print(f"Restoring your work from “{name}” …", flush=True)
                out["snapshot"] = snapshots.restore(name)
        if len(out) == 1:
            print("Nothing to restore.", flush=True)
        return out

    return _run


def _flag(body: dict[str, Any], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise LabError(f"{key!r} must be true or false.")
    return value


def start_action(runner: JobRunner, op: str, **kw: Any) -> Job:
    from canfar_lab.session import agents, cluster, snapshots

    if op == "restore_agents":
        return runner.start("restore", "Reinstall agents", lambda _j: agents.restore())
    if op == "agent":
        agent_id, verb = kw["agent"], kw["verb"]
        spec = agents.get_lab_agent(agent_id)
        fn = {
            "install": agents.install,
            "update": agents.update,
            "remove": agents.remove,
            "setup": agents.setup,
            "clean-home": agents.remove_home_copies,
            "rehome": agents.rehome,
        }[verb]
        label = {
            "install": f"Install {spec.label}",
            "update": f"Update {spec.label}",
            "remove": f"Remove {spec.label}",
            "setup": f"Set up {spec.label} for CANFAR",
            "clean-home": f"Remove {spec.label} copies from your home",
            "rehome": f"Move {spec.label} to $SCRATCH",
        }[verb]
        return runner.start(f"agent:{verb}", label, lambda _j: fn(agent_id))
    if op == "save_snapshot":
        name = kw.get("name") or None
        if name is not None:
            snapshots._check_name(name)
        include_dsh = _flag(kw, "include_dsh", True)
        include_envs = _flag(kw, "include_envs")
        overwrite = _flag(kw, "overwrite")
        return runner.start(
            "snapshot_save",
            f"Save {name or 'snapshot'}",
            lambda _j: snapshots.save(
                name, include_dsh=include_dsh, include_envs=include_envs, overwrite=overwrite
            ),
        )
    if op == "restore_snapshot":
        name = snapshots._check_name(kw["name"])
        overwrite = _flag(kw, "overwrite")
        replace_dsh = _flag(kw, "replace_dsh")
        return runner.start(
            "snapshot_restore",
            f"Restore {name}",
            lambda _j: snapshots.restore(name, overwrite=overwrite, replace_dsh=replace_dsh),
        )
    if op == "cluster_start":
        return runner.start("cluster", "Start cluster", lambda _j: cluster.start())
    if op == "cluster_stop":
        return runner.start("cluster", "Stop cluster", lambda _j: cluster.stop())
    raise LabError(f"Unknown action {op!r}")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class LabServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], *, token: str, runner: JobRunner) -> None:
        family = socket.AF_INET6 if ":" in addr[0] else socket.AF_INET
        self.address_family = family
        super().__init__(addr, LabHandler)
        self.token = token
        self.runner = runner
        #: New or returning user, fixed at start (see onboarding.build).
        self.returning: bool | None = None
        #: When this Lab started; code counts as saved only by a later snapshot.
        self.started_at = time.time()
        #: Whether the startup job restores code (``--restore-code``).
        self.restore_code = False

    def state(self, *, with_repos: bool = True) -> dict[str, Any]:
        return lab_state(
            self.runner,
            returning=self.returning,
            started_at=self.started_at,
            restore_code=self.restore_code,
            with_repos=with_repos,
        )


_ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    ("GET", re.compile(r"/"), "page"),
    ("GET", re.compile(r"/api/health"), "health"),
    ("GET", re.compile(r"/api/state"), "state"),
    ("GET", re.compile(r"/api/onboarding"), "onboarding"),
    ("GET", re.compile(r"/api/cluster"), "cluster"),
    ("GET", re.compile(r"/api/jobs"), "jobs"),
    ("GET", re.compile(r"/api/jobs/(?P<job>\d+)"), "job"),
    ("GET", re.compile(r"/api/jobs/(?P<job>\d+)/events"), "events"),
    ("POST", re.compile(r"/api/agents/restore"), "agents_restore"),
    ("POST", re.compile(r"/api/agents/(?P<agent>[a-z0-9-]+)/(?P<verb>[a-z-]+)"), "agent"),
    ("POST", re.compile(r"/api/keys/(?P<key>[A-Z][A-Z0-9_]+)"), "key_set"),
    ("DELETE", re.compile(r"/api/keys/(?P<key>[A-Z][A-Z0-9_]+)"), "key_unset"),
    ("POST", re.compile(r"/api/snapshots"), "snapshot_save"),
    ("POST", re.compile(r"/api/snapshots/(?P<name>[^/]+)/restore"), "snapshot_restore"),
    ("DELETE", re.compile(r"/api/snapshots/(?P<name>[^/]+)"), "snapshot_delete"),
    ("POST", re.compile(r"/api/cluster/(?P<verb>start|stop)"), "cluster_verb"),
]


class LabHandler(BaseHTTPRequestHandler):
    server_version = "canfar-lab-web"
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------

    @property
    def lab(self) -> LabServer:
        return cast(LabServer, self.server)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Never log the laptop login token (GET /?token=…).
        text = re.sub(r"([?&]token=)[^&\s\"]+", r"\1<redacted>", format % args)
        line = f"{self.address_string()} - {text}\n"
        with contextlib.suppress(OSError):
            os.write(self.lab.runner.log_fd, line.encode("utf-8", "replace"))

    def _send(
        self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str, hint: str | None = None) -> None:
        self._json(status, {"ok": False, "error": message, "hint": hint})

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise LabError("Bad Content-Length.") from exc
        if length < 0 or length > MAX_BODY:
            raise LabError("Request body too large.")
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError as exc:
            raise LabError("Request body is not JSON.") from exc
        if not isinstance(data, dict):
            raise LabError("Request body must be a JSON object.")
        return data

    # -- auth ------------------------------------------------------------

    def _token_ok(self) -> bool:
        token = self.lab.token
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), token):
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except Exception:  # noqa: BLE001 — malformed cookie header
            return False
        morsel = cookie.get(COOKIE)
        return morsel is not None and hmac.compare_digest(morsel.value, token)

    def _authorize(self, write: bool) -> bool:
        if self.client_address[0] not in LOOPBACK:
            self._error(HTTPStatus.FORBIDDEN, "Loopback only.")
            return False
        if not self._token_ok():
            self._error(HTTPStatus.UNAUTHORIZED, "Missing or wrong Lab token.")
            return False
        if write and self.headers.get(WRITE_HEADER) != "1":
            self._error(HTTPStatus.FORBIDDEN, f"Writes need the {WRITE_HEADER} header.")
            return False
        return True

    # -- dispatch --------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        query = parse_qs(url.query)
        if method == "GET" and path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if method == "GET" and path == "/" and "token" in query:
            self._token_login(query["token"][0])
            return
        for verb, pattern, name in _ROUTES:
            match = pattern.fullmatch(path)
            if not match:
                continue
            if verb != method:
                continue
            if not self._authorize(write=method != "GET"):
                return
            try:
                getattr(self, f"_r_{name}")(**match.groupdict(), query=query)
            except Busy as exc:
                self._error(HTTPStatus.CONFLICT, exc.message, exc.hint)
            except LabError as exc:
                self._error(HTTPStatus.BAD_REQUEST, exc.message, exc.hint)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # noqa: BLE001
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")
            return
        if any(p.fullmatch(path) for _v, p, _n in _ROUTES):
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed.")
        else:
            self._error(HTTPStatus.NOT_FOUND, "Not found.")

    def _token_login(self, token: str) -> None:
        if self.client_address[0] not in LOOPBACK or not hmac.compare_digest(token, self.lab.token):
            self._error(HTTPStatus.UNAUTHORIZED, "Wrong Lab token.")
            return
        cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"
        self._send(
            HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "./", "Set-Cookie": cookie}
        )

    def _job_reply(self, job: Job) -> None:
        self._json(HTTPStatus.ACCEPTED, {"ok": True, "job": job.to_dict()})

    # -- routes ----------------------------------------------------------

    def _r_page(self, *, query: dict[str, list[str]]) -> None:
        html = PAGE.read_text(encoding="utf-8")
        prefix = (self.headers.get("X-Forwarded-Prefix") or "").strip().rstrip("/")
        # One or more "/segment"s; never "//host", which would make a
        # protocol-relative <base> and send every API call elsewhere.
        if prefix and re.fullmatch(r"(/[A-Za-z0-9_.-]+)+", prefix):
            html = html.replace("<head>", f'<head>\n<base href="{prefix}/">', 1)
        csp = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'"
        )
        self._send(
            HTTPStatus.OK,
            html.encode("utf-8"),
            "text/html; charset=utf-8",
            {"Content-Security-Policy": csp},
        )

    def _r_health(self, *, query: dict[str, list[str]]) -> None:
        self._json(HTTPStatus.OK, {"ok": True})

    def _r_state(self, *, query: dict[str, list[str]]) -> None:
        self._json(HTTPStatus.OK, self.lab.state())

    def _r_onboarding(self, *, query: dict[str, list[str]]) -> None:
        state = self.lab.state(with_repos=False)["onboarding"]
        self._json(HTTPStatus.OK, {"ok": True, **{k: v for k, v in state.items() if k != "steps"}})

    def _r_cluster(self, *, query: dict[str, list[str]]) -> None:
        self._json(HTTPStatus.OK, cluster_state())

    def _r_jobs(self, *, query: dict[str, list[str]]) -> None:
        self._json(
            HTTPStatus.OK, {"ok": True, "jobs": [j.to_dict() for j in self.lab.runner.recent()]}
        )

    def _r_job(self, *, job: str, query: dict[str, list[str]]) -> None:
        found = self.lab.runner.get(int(job))
        if found is None:
            self._error(HTTPStatus.NOT_FOUND, "No such job.")
            return
        self._json(HTTPStatus.OK, {"ok": True, "job": found.to_dict(tail=200)})

    def _r_events(self, *, job: str, query: dict[str, list[str]]) -> None:
        runner = self.lab.runner
        found = runner.get(int(job))
        if found is None:
            self._error(HTTPStatus.NOT_FOUND, "No such job.")
            return
        start = self.headers.get("Last-Event-ID") or (query.get("from") or ["0"])[0]
        index = int(start) + 1 if self.headers.get("Last-Event-ID") else int(start or 0)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last_beat = time.monotonic()
        while True:
            rows, done = runner.lines_since(found, index, timeout=1.0)
            chunks = []
            for i, line in rows:
                chunks.append(f"id: {i}\nevent: line\ndata: {json.dumps(line)}\n\n")
                index = i + 1
            if done:
                chunks.append(f"event: done\ndata: {json.dumps(found.to_dict())}\n\n")
            if not chunks and time.monotonic() - last_beat > SSE_HEARTBEAT_SEC:
                chunks.append(": keepalive\n\n")
            if chunks:
                self.wfile.write("".join(chunks).encode("utf-8"))
                self.wfile.flush()
                last_beat = time.monotonic()
            if done:
                return

    def _r_agents_restore(self, *, query: dict[str, list[str]]) -> None:
        self._job_reply(start_action(self.lab.runner, "restore_agents"))

    def _r_agent(self, *, agent: str, verb: str, query: dict[str, list[str]]) -> None:
        if verb not in AGENT_VERBS:
            raise LabError(f"Unknown agent action {verb!r}.")
        self._job_reply(start_action(self.lab.runner, "agent", agent=agent, verb=verb))

    def _r_key_set(self, *, key: str, query: dict[str, list[str]]) -> None:
        from canfar_lab.session import agents

        value = str(self._body().get("value") or "").strip()
        result = agents.set_key(key, value) if value else agents.unset_key(key)
        self._json(HTTPStatus.OK, result)

    def _r_key_unset(self, *, key: str, query: dict[str, list[str]]) -> None:
        from canfar_lab.session import agents

        self._json(HTTPStatus.OK, agents.unset_key(key))

    def _r_snapshot_save(self, *, query: dict[str, list[str]]) -> None:
        body = self._body()
        fields = {
            k: body[k] for k in ("name", "include_dsh", "include_envs", "overwrite") if k in body
        }
        self._job_reply(start_action(self.lab.runner, "save_snapshot", **fields))

    def _r_snapshot_restore(self, *, name: str, query: dict[str, list[str]]) -> None:
        body = self._body()
        self._job_reply(
            start_action(
                self.lab.runner,
                "restore_snapshot",
                name=name,
                overwrite=body.get("overwrite"),
                replace_dsh=body.get("replace_dsh"),
            )
        )

    def _r_snapshot_delete(self, *, name: str, query: dict[str, list[str]]) -> None:
        from canfar_lab.session import snapshots

        if self.lab.runner.current() is not None:
            raise Busy("Busy: wait for the running job to finish.")
        self._json(HTTPStatus.OK, snapshots.delete(name))

    def _r_cluster_verb(self, *, verb: str, query: dict[str, list[str]]) -> None:
        self._job_reply(start_action(self.lab.runner, f"cluster_{verb}"))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def resolve_token(token_file: Path | None) -> str:
    """``$CANFAR_LAB_WEB_TOKEN`` or a fresh random token; written to *token_file* (0600)."""
    token = os.environ.get("CANFAR_LAB_WEB_TOKEN", "").strip() or secrets.token_urlsafe(32)
    if token_file is not None:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
        os.chmod(token_file, 0o600)
    return token


def default_token_file() -> Path:
    from canfar_lab.shell.session_env import resolve_session_env

    return resolve_session_env(ensure=False).canfar_lab_runtime_root / "lab-web.token"


def make_server(
    *, host: str = "127.0.0.1", port: int = DEFAULT_PORT, token: str, capture: bool = True
) -> LabServer:
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise LabError("canfar lab web only listens on loopback (127.0.0.1 or ::1).")
    runner = JobRunner(capture=capture)
    return LabServer(
        ("127.0.0.1" if host == "localhost" else host, port), token=token, runner=runner
    )


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    token_file: Path | None = None,
    restore: bool = False,
    restore_code: bool = False,
) -> None:
    # Installers must never wait on a prompt nobody can answer.
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    os.environ.setdefault("NO_COLOR", "1")
    os.environ.setdefault("CI", "1")
    token = resolve_token(token_file)
    server = make_server(host=host, port=port, token=token)
    url = f"http://{'[' + host + ']' if ':' in host else host}:{server.server_address[1]}/"
    print(f"canfar lab web on {url}", file=sys.stderr, flush=True)
    if token_file is not None:
        print(f"  token file: {token_file}", file=sys.stderr, flush=True)
    if sys.stderr.isatty():  # never put the token in container logs
        print(f"  open: {url}?token={token}", file=sys.stderr, flush=True)
    server.returning = session_was_used()
    server.restore_code = restore_code
    if restore or restore_code:
        server.runner.start(
            "startup", "Get this session ready", startup_job(restore_code=restore_code)
        )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
