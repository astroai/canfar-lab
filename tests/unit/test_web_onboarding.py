"""canfar_lab.web.onboarding: next steps for new and returning users."""

from __future__ import annotations

from typing import Any

from canfar_lab.web import onboarding


def _agent(agent_id: str, **kw: Any) -> dict[str, Any]:
    row = {
        "id": agent_id,
        "label": agent_id.title(),
        "installed": False,
        "needs_restore": False,
        "remembered": False,
        "ready": False,
        "login": agent_id,
        "keys": [{"key": "OPENROUTER_API_KEY", "present": False}],
    }
    row.update(kw)
    return row


AGENTS = ["claude", "codex", "opencode"]
NO_CODE = {"src": "/scratch/src", "entries": [], "repos": []}
SOME_CODE = {"src": "/scratch/src", "entries": ["proj"], "repos": []}
SNAP = {"name": "friday", "created_at": "2026-11-06T17:00:00+00:00", "repos": [{"path": "proj"}]}


def _build(agents: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    return onboarding.build(
        agents=agents if agents is not None else [_agent(a) for a in AGENTS],
        keys=kw.pop("keys", []),
        snapshots=kw.pop("snapshots", []),
        code=kw.pop("code", NO_CODE),
        job=kw.pop("job", None),
        returning=kw.pop("returning", None),
    )


def _step(result: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in result["steps"] if s["id"] == step_id)


def test_new_user_gets_one_click_suggested_agent() -> None:
    result = _build()
    assert result["kind"] == "new" and result["next"] == "agents"
    agents = _step(result, "agents")
    assert agents["action"] == {
        "label": "Install Opencode",
        "op": "install_agent",
        "agent": "opencode",
    }
    assert [s["id"] for s in result["steps"]] == ["agents", "code"]  # no sign-in yet
    assert _step(result, "code")["status"] == "optional"
    assert "Welcome to AstroAI" in result["headline"]


def test_returning_user_reinstall_then_restore_code() -> None:
    agents = [_agent("claude", remembered=True, needs_restore=True), _agent("codex")]
    result = _build(agents, snapshots=[SNAP])
    assert result["kind"] == "returning" and "Welcome back" in result["headline"]
    assert _step(result, "agents")["action"]["op"] == "restore_agents"
    code = _step(result, "code")
    assert code["status"] == "todo"
    assert code["action"] == {
        "label": "Restore my work",
        "op": "restore_snapshot",
        "name": "friday",
    }
    assert result["pending"] == 2


def test_startup_job_shows_running_steps() -> None:
    agents = [_agent("claude", remembered=True, needs_restore=True)]
    job = {"state": "running", "kind": "startup"}
    result = _build(agents, snapshots=[SNAP], job=job)
    assert _step(result, "agents")["status"] == "running"
    assert _step(result, "code")["status"] == "running"
    assert "action" not in _step(result, "agents")


def test_first_install_keeps_new_user_greeting() -> None:
    """kind is fixed at server start: installing an agent is not "welcome back"."""
    agents = [_agent("opencode", installed=True, remembered=True)]
    assert _build(agents)["kind"] == "returning"  # inferred from state alone
    result = _build(agents, returning=False)
    assert result["kind"] == "new" and "Welcome to AstroAI" in result["headline"]
    assert result["next"] == "signin"


def test_sign_in_step_names_login_command() -> None:
    agents = [_agent("claude", installed=True, remembered=True)]
    signin = _step(_build(agents), "signin")
    assert signin["status"] == "todo" and signin["command"] == "claude"
    assert "`claude`" in signin["detail"] and "OPENROUTER_API_KEY" in signin["detail"]
    assert signin["action"]["focus"] == "keys"


def test_ready_when_agent_ready_and_code_saved() -> None:
    agents = [_agent("claude", installed=True, remembered=True, ready=True)]
    result = _build(agents, code=SOME_CODE, snapshots=[SNAP])
    assert result["ready"] and result["next"] is None and result["pending"] == 0
    assert _step(result, "code")["action"]["op"] == "save_snapshot"


def test_unsaved_code_nudges_but_does_not_block() -> None:
    agents = [_agent("claude", installed=True, ready=True)]
    code = _step(_build(agents, code=SOME_CODE), "code")
    assert code["status"] == "optional" and "not saved yet" in code["detail"]


def test_code_counts_as_saved_only_by_a_snapshot_from_this_session() -> None:
    ready_agent = [_agent("opencode", installed=True, remembered=True, ready=True)]
    started = 1_800_000_000.0  # 2027-01-15; SNAP is from 2026-11-06
    old = onboarding.build(
        agents=ready_agent, keys=[], snapshots=[SNAP], code=SOME_CODE, started_at=started
    )
    code = _step(old, "code")
    assert code["status"] == "optional" and code["action"]["op"] == "save_snapshot"
    assert "lost when the session ends" in code["detail"]
    assert old["ready"] and "Save your code" in old["headline"]
    fresh = {**SNAP, "created_at": "2027-01-15T08:00:00+00:00"}
    new = onboarding.build(
        agents=ready_agent, keys=[], snapshots=[fresh], code=SOME_CODE, started_at=started
    )
    assert _step(new, "code")["status"] == "done"
    assert "Switch to the Agent tab" in new["headline"]


def test_running_job_labels_match_the_action() -> None:
    agents = [_agent("opencode", installed=True)]
    for verb, word in (("install", "Installing"), ("update", "Updating"), ("remove", "Removing")):
        job = {"state": "running", "kind": f"agent:{verb}"}
        assert _step(_build(agents, job=job), "agents")["detail"] == f"{word}…"


def test_startup_without_restore_code_does_not_claim_a_restore() -> None:
    job = {"state": "running", "kind": "startup"}
    result = onboarding.build(
        agents=[_agent(a) for a in AGENTS],
        keys=[],
        snapshots=[SNAP],
        code=NO_CODE,
        job=job,
        restore_code=False,
    )
    assert _step(result, "code")["status"] == "todo"


def test_code_step_names_the_projects() -> None:
    code = {"src": "/scratch/src", "entries": ["torchfits", "zensus", "pensus"], "repos": []}
    detail = _step(_build(code=code), "code")["detail"]
    assert detail.startswith("torchfits, zensus and pensus in /scratch/src")
    many = {**code, "entries": [f"p{i}" for i in range(7)]}
    assert "p0, p1, p2, p3 and 3 more" in _step(_build(code=many), "code")["detail"]
