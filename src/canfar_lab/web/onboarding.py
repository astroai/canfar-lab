"""What a person needs to do next in this session, as at most three steps.

Pure function of the Lab state, so every case is unit-testable:

1. **Agents** — reinstall the ones a returning user had (automatic when the
   server starts with ``--restore``), or pick one.
2. **Sign in** — an installed agent with neither a login file nor a key.
3. **Your code** — restore the latest snapshot into an empty ``$SCRATCH/src``,
   start a project, or (once there is code) save it before the session ends.

Each step has ``status`` ``done`` | ``todo`` | ``running`` | ``optional`` and
at most one ``action`` the page renders as a single button. ``next`` names
the first step still to do; when it is ``None`` the session is ready.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: Shown first to someone with no agent yet. OpenCode can run on the free
#: OpenCode Zen model, so it is the cheapest way to a working agent.
SUGGESTED_AGENT = "opencode"


def _names(rows: list[dict[str, Any]]) -> str:
    labels = [str(r["label"]) for r in rows]
    if len(labels) <= 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def _job_kind(job: dict[str, Any] | None) -> str:
    if job and job.get("state") == "running":
        return str(job.get("kind") or "")
    return ""


def _agents_step(agents: list[dict[str, Any]], running: str) -> dict[str, Any]:
    installed = [a for a in agents if a["installed"]]
    missing = [a for a in agents if a["needs_restore"]]
    step: dict[str, Any] = {"id": "agents", "title": "Coding agents"}
    if running in ("restore", "startup"):
        return {
            **step,
            "status": "running",
            "detail": f"Reinstalling {_names(missing)}…" if missing else "Reinstalling…",
        }
    if running.startswith("agent:"):
        verb = running.split(":", 1)[1]
        doing = {"update": "Updating", "remove": "Removing", "setup": "Setting up"}
        return {**step, "status": "running", "detail": f"{doing.get(verb, 'Installing')}…"}
    if missing:
        return {
            **step,
            "status": "todo",
            "detail": f"You used {_names(missing)} last time. They need reinstalling "
            "in this session (your settings are kept).",
            "action": {"label": "Reinstall", "op": "restore_agents"},
        }
    if installed:
        return {**step, "status": "done", "detail": f"{_names(installed)} installed."}
    suggested = next((a for a in agents if a["id"] == SUGGESTED_AGENT), agents[0])
    return {
        **step,
        "status": "todo",
        "detail": "Install a coding agent to use from the Agent tab terminal. "
        f"Not sure? {suggested['label']} is a good start.",
        "action": {
            "label": f"Install {suggested['label']}",
            "op": "install_agent",
            "agent": suggested["id"],
        },
        "more": {"label": "See all agents", "focus": "agents"},
    }


def _signin_step(agents: list[dict[str, Any]]) -> dict[str, Any] | None:
    installed = [a for a in agents if a["installed"]]
    if not installed:
        return None
    step: dict[str, Any] = {"id": "signin", "title": "Sign in"}
    waiting = [a for a in installed if not a["ready"]]
    if not waiting:
        return {**step, "status": "done", "detail": f"{_names(installed)} can reach a model."}
    first = waiting[0]
    keys = [k["key"] for k in first["keys"]]
    detail = f"In the Agent tab terminal, run `{first['login']}` and follow the sign-in link"
    detail += f", or paste an API key ({' or '.join(keys)})." if keys else "."
    return {
        **step,
        "status": "todo",
        "detail": detail,
        "agent": first["id"],
        "command": first["login"],
        "action": {"label": "Add a key instead", "focus": "keys", "agent": first["id"]},
    }


def _saved_since(snapshot: dict[str, Any] | None, started_at: float | None) -> bool:
    """Was *snapshot* taken during this session (at or after *started_at*)?"""
    if snapshot is None:
        return False
    if started_at is None:
        return True
    try:
        taken = datetime.fromisoformat(str(snapshot.get("created_at"))).timestamp()
    except (TypeError, ValueError):
        return False
    return taken >= int(started_at)  # created_at has whole-second precision


def _code_step(
    code: dict[str, Any],
    snapshots: list[dict[str, Any]],
    running: str,
    *,
    started_at: float | None = None,
    restore_code: bool = True,
) -> dict[str, Any]:
    step: dict[str, Any] = {"id": "code", "title": "Your code"}
    latest = snapshots[0] if snapshots else None
    startup_restores = running == "startup" and restore_code and latest and not code["entries"]
    if running == "snapshot_restore" or startup_restores:
        return {**step, "status": "running", "detail": "Restoring your saved work…"}
    if running == "snapshot_save":
        return {**step, "status": "running", "detail": "Saving to /arc…"}
    if code["entries"]:
        n = len(code["entries"])
        where = f"{n} project{'s' if n != 1 else ''} in {code['src']}"
        saved = _saved_since(latest, started_at)
        if saved and latest:
            when = latest.get("created_at", "?")
            detail = f"{where}. Saved this session as “{latest['name']}” ({when})."
        elif latest:
            detail = (
                f"{where}. Last saved {latest.get('created_at', '?')} as “{latest['name']}”; "
                "anything changed since is lost when the session ends."
            )
        else:
            detail = f"{where}, not saved yet. $SCRATCH is wiped when the session ends."
        return {
            **step,
            "status": "done" if saved else "optional",
            "detail": detail,
            "action": {"label": "Save now", "op": "save_snapshot"},
        }
    if latest:
        repos = [str(r.get("path")) for r in latest.get("repos") or []]
        what = f" ({', '.join(repos[:4])}{'…' if len(repos) > 4 else ''})" if repos else ""
        return {
            **step,
            "status": "todo",
            "detail": f"Pick up where you left off: “{latest['name']}”, "
            f"saved {latest.get('created_at', '?')}{what}.",
            "action": {
                "label": "Restore my work",
                "op": "restore_snapshot",
                "name": latest["name"],
            },
        }
    return {
        **step,
        "status": "optional",
        "detail": "Start from a repository: run `canfar lab clone OWNER/REPO` in the terminal, "
        "or `canfar lab init NAME` for a new project. Save it here before you leave.",
        "command": "canfar lab clone OWNER/REPO",
    }


def is_returning(
    *,
    agents: list[dict[str, Any]],
    keys: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> bool:
    """Anything left by an earlier session: a snapshot, a remembered agent, a key."""
    return bool(
        snapshots
        or any(a["remembered"] or a["installed"] for a in agents)
        or any(k["present"] for k in keys)
    )


def build(
    *,
    agents: list[dict[str, Any]],
    keys: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    code: dict[str, Any],
    job: dict[str, Any] | None = None,
    returning: bool | None = None,
    started_at: float | None = None,
    restore_code: bool = True,
) -> dict[str, Any]:
    """Onboarding steps for the Lab page (see module docstring).

    *returning* is decided once, when the server starts (see
    :func:`is_returning`); otherwise a new user's first install would turn
    the greeting into "Welcome back". ``None`` infers it from the state given.
    *started_at* (epoch seconds) is when this session's Lab started: code
    counts as saved only by a snapshot taken since then. *restore_code* says
    whether the startup job brings code back.
    """
    running = _job_kind(job)
    if returning is None:
        returning = is_returning(agents=agents, keys=keys, snapshots=snapshots)
    steps = [_agents_step(agents, running)]
    signin = _signin_step(agents)
    if signin:
        steps.append(signin)
    steps.append(
        _code_step(code, snapshots, running, started_at=started_at, restore_code=restore_code)
    )
    pending = [s for s in steps if s["status"] in ("todo", "running")]
    ready = not pending
    unsaved = steps[-1]["status"] == "optional" and bool(code["entries"])
    if ready and unsaved:
        headline = "You're all set. Save your code here before you leave."
    elif ready:
        headline = "You're all set. Switch to the Agent tab to start."
    elif returning:
        headline = "Welcome back. A couple of things to get this session ready."
    else:
        headline = "Welcome to AstroAI. A couple of steps and you're coding."
    return {
        "kind": "returning" if returning else "new",
        "headline": headline,
        "ready": ready,
        "pending": len(pending),
        "next": pending[0]["id"] if pending else None,
        "steps": steps,
    }
