"""What a person needs to do next in this session, as at most three steps.

Pure function of the Lab state, so every case is unit-testable:

1. **Studio** — a model key, so the Studio tab can answer.
2. **Agents** — reinstall the ones a returning user had (automatic when the
   server starts with ``--restore``). New Terminal agents are optional.
3. **Sign in** — an installed agent with neither a login file nor a key.
3. **Your code** — restore the latest snapshot into an empty ``$SCRATCH/src``,
   start a project, or (once there is code) save it before the session ends.

Each step has ``status`` ``done`` | ``todo`` | ``running`` | ``optional`` and
at most one ``action`` the page renders as a single button. ``next`` names
the first step still to do; when it is ``None`` the session is ready.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: Suggested to someone with no Terminal agent: it runs free models with no key.
SUGGESTED_AGENT = "opencode"


def _names(rows: list[dict[str, Any]]) -> str:
    labels = [str(r["label"]) for r in rows]
    if len(labels) <= 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def _list(names: list[str], limit: int = 4) -> str:
    """``a``, ``a and b``, ``a, b and c``, ``a, b, c, d and 3 more``."""
    if len(names) > limit:
        return ", ".join(names[:limit]) + f" and {len(names) - limit} more"
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _job_kind(job: dict[str, Any] | None) -> str:
    if job and job.get("state") == "running":
        return str(job.get("kind") or "")
    return ""


def _studio_step(keys: list[dict[str, Any]]) -> dict[str, Any]:
    step: dict[str, Any] = {"id": "studio", "title": "Studio"}
    usable = [k for k in keys if k.get("studio")]
    have = [str(k["label"]) for k in usable if k["present"]]
    if have:
        return {
            **step,
            "status": "done",
            "detail": f"Studio can use your {_list(have)} key. Pick the model at the "
            "bottom of the chat box.",
        }
    labels = _list([str(k["label"]) for k in usable], limit=6)
    return {
        **step,
        "status": "todo",
        "detail": f"Studio needs an API key from one model provider: {labels}. "
        "DeepSeek is the cheapest to start with.",
        "action": {"label": "Add a key", "focus": "keys"},
    }


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
        "status": "optional",
        "detail": "Prefer Claude Code, Codex or another agent? Install it to run in the "
        f"Terminal tab with your own subscription. {suggested['label']} works without one.",
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
    detail = f"Run `{first['login']}` in the Terminal tab and follow the sign-in link"
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
    where_more = {"label": "Change where code lives", "focus": "code", "item": "where"}
    if not code.get("ephemeral", True):
        # ~/src or a custom /arc folder: nothing to save or restore.
        if code["entries"]:
            return {
                **step,
                "status": "done",
                "detail": f"{_list(code['entries'])} in {code['src']}, kept between sessions.",
                "more": where_more,
            }
        return {
            **step,
            "status": "optional",
            "detail": f"Your code lives in {code['src']} and is kept between sessions. "
            "Start from a repository: run `canfar lab clone OWNER/REPO` in the terminal, "
            "or `canfar lab init NAME` for a new project.",
            "command": "canfar lab clone OWNER/REPO",
            "more": where_more,
        }
    autosave = code.get("autosave") or {}
    auto_on = bool(autosave.get("enabled"))
    every = autosave.get("interval_min") or 30
    if code["entries"]:
        where = f"{_list(code['entries'])} in {code['src']}"
        saved = _saved_since(latest, started_at)
        if auto_on:
            last = (
                f" Last saved {_when(latest.get('created_at'))}."
                if latest
                else " The first save happens soon."
            )
            return {
                **step,
                "status": "done",
                "detail": f"{where}. Saved to /arc automatically every {every} min and when "
                f"the session ends.{last}",
                "action": {"label": "Save now", "op": "save_snapshot"},
                "more": where_more,
            }
        if saved and latest:
            when = _when(latest.get("created_at"))
            detail = f"{where}. Saved this session as “{latest['name']}” ({when})."
        elif latest:
            detail = (
                f"{where}. Last saved {_when(latest.get('created_at'))} as “{latest['name']}”; "
                "anything changed since is lost when the session ends."
            )
        else:
            detail = f"{where}, not saved yet. $SCRATCH is wiped when the session ends."
        return {
            **step,
            "status": "done" if saved else "optional",
            "detail": detail,
            "action": {"label": "Save now", "op": "save_snapshot"},
            "more": {"label": "Turn on automatic saves", "focus": "code", "item": "autosave"},
        }
    if latest:
        repos = [str(r.get("path")) for r in latest.get("repos") or []]
        what = f" ({', '.join(repos[:4])}{'…' if len(repos) > 4 else ''})" if repos else ""
        return {
            **step,
            # "Start empty" was a choice: offer the restore, do not nag about it.
            "status": "optional" if code.get("restore_at_start") == "never" else "todo",
            "detail": f"Pick up where you left off: “{latest['name']}”, "
            f"saved {_when(latest.get('created_at'))}{what}.",
            "action": {
                "label": "Restore my work",
                "op": "restore_snapshot",
                "name": latest["name"],
            },
            "more": {"label": "Pick another snapshot", "focus": "code"},
        }
    saves = " It is saved to /arc automatically." if auto_on else " Save it here before you leave."
    return {
        **step,
        "status": "optional",
        "detail": f"Code lives in {code['src'] or '$SCRATCH/src'} (fast; wiped when the "
        "session ends). Start from a repository: run `canfar lab clone OWNER/REPO` in the "
        f"terminal, or `canfar lab init NAME` for a new project.{saves}",
        "command": "canfar lab clone OWNER/REPO",
        "more": where_more,
    }


def _when(raw: object, now: float | None = None) -> str:
    """'today at 14:05 UTC', 'yesterday at 09:30 UTC', 'on 6 Nov' — not raw ISO."""
    import time
    from datetime import datetime, timezone

    try:
        at = datetime.fromisoformat(str(raw)).astimezone(timezone.utc)
    except ValueError:
        return str(raw or "?")
    today = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).date()
    days = (today - at.date()).days
    clock = at.strftime("%H:%M UTC")
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"yesterday at {clock}"
    return f"on {at.day} {at.strftime('%b %Y' if at.year != today.year else '%b')}"


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
    steps = [_studio_step(keys), _agents_step(agents, running)]
    signin = _signin_step(agents)
    if signin:
        steps.append(signin)
    steps.append(
        _code_step(code, snapshots, running, started_at=started_at, restore_code=restore_code)
    )
    if code.get("restart_needed"):
        live = code.get("live_src") or "the old folder"
        restart: dict[str, Any] = {
            "id": "restart",
            "title": "Open your new code folder",
            "status": "todo",
            "detail": f"Studio, Terminal, JupyterLab and VS Code still use {live}.",
        }
        if code.get("restart_supported"):
            restart["action"] = {"label": "Restart tools now", "op": "restart_tools"}
        else:
            restart["detail"] += " Start a new session to switch."
        steps.append(restart)
    pending = [s for s in steps if s["status"] in ("todo", "running")]
    ready = not pending
    unsaved = (
        steps[-1]["status"] == "optional" and bool(code["entries"]) and code.get("ephemeral", True)
    )
    if ready and unsaved:
        headline = "You're all set. Save your code here before you leave."
    elif ready:
        headline = "You're all set. Switch to the Studio tab to start."
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
