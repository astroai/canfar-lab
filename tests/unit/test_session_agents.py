"""canfar_lab.session.agents: the six Lab agents over the registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from canfar_lab.agent import keys as keys_mod
from canfar_lab.agent import registry_lifecycle
from canfar_lab.agent.setup_state import remember_agent, remembered_agents
from canfar_lab.errors import LabError
from canfar_lab.session import agents

LAB_IDS = ["claude", "codex", "agy", "opencode", "pi", "omp"]
SECRET = "sk-test-0123456789abcdef"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DSH_HOME", raising=False)
    for row in keys_mod.catalog():
        monkeypatch.delenv(row["key"], raising=False)
    return home


@pytest.fixture
def fake_probe(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Agents in the returned set report an installed binary."""
    from canfar_lab.agent import registry_probe

    installed: set[str] = set()

    def _status(agent: dict[str, Any], home: Path | None = None, **_: Any) -> dict[str, Any]:
        ok = agent["id"] in installed
        return {
            "id": agent["id"],
            "name": agent["name"],
            "summary": agent.get("summary", ""),
            "binary": agent["binary"],
            "binary_ok": ok,
            "binary_path": f"/scratch/.local/bin/{agent['binary']}" if ok else None,
            "managed": ok,
            "home_install": False,
            "config": "",
            "config_ok": True,
            "version": None,
        }

    monkeypatch.setattr(registry_probe, "registry_agent_status", _status)
    return installed


def test_spec_lists_six_agents_in_order() -> None:
    specs = agents.lab_agents()
    assert [a.id for a in specs] == LAB_IDS
    assert all(a.label and a.keys for a in specs)


def test_spec_rejects_unknown_registry_id(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / agents.SPEC_FILE).write_text("agents:\n  - id: nope\n", encoding="utf-8")
    with pytest.raises(LabError, match="not in the agent registry"):
        agents.lab_agents(tmp_path)


def test_status_reports_keys_by_presence_only(home: Path, fake_probe: set[str]) -> None:
    fake_probe.add("codex")
    keys_mod.set_key(home, "OPENAI_API_KEY", SECRET)
    snap = agents.status(home)
    assert SECRET not in repr(snap)
    rows = {r["id"]: r for r in snap["agents"]}
    assert list(rows) == LAB_IDS
    assert rows["codex"]["installed"] and not rows["claude"]["installed"]
    assert {"key": "OPENAI_API_KEY", "present": True} in rows["codex"]["keys"]
    by_key = {k["key"]: k for k in snap["keys"]}
    assert by_key["OPENAI_API_KEY"]["present"]
    assert not by_key["ANTHROPIC_API_KEY"]["present"]
    assert "Claude Code" in by_key["ANTHROPIC_API_KEY"]["used_by"]


def test_install_sets_up_and_remembers(
    home: Path, monkeypatch: pytest.MonkeyPatch, fake_probe: set[str]
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        registry_lifecycle,
        "install_registry_agent",
        lambda agent_id, dry_run=False: calls.append(("install", agent_id)) or agent_id,
    )
    monkeypatch.setattr(
        registry_lifecycle,
        "setup_registry_agent",
        lambda agent_id, **_: (
            calls.append(("setup", agent_id)) or {"actions": ["mcp"], "errors": []}
        ),
    )
    result = agents.install("pi")
    assert result["ok"] and calls == [("install", "pi"), ("setup", "pi")]
    assert remembered_agents(home) == ["pi"]


def test_install_refuses_non_lab_agent(home: Path) -> None:
    with pytest.raises(LabError, match="not a Lab agent"):
        agents.install("cursor")


def test_remove_forgets(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remember_agent(home, "omp", installed=True)
    monkeypatch.setattr(
        registry_lifecycle,
        "remove_registry_agent",
        lambda agent_id, **_: [{"target": "bin", "status": "removed", "detail": "x"}],
    )
    assert agents.remove("omp")["ok"]
    assert remembered_agents(home) == []


def test_restore_reinstalls_only_missing_remembered(
    home: Path, monkeypatch: pytest.MonkeyPatch, fake_probe: set[str]
) -> None:
    for agent_id in ("claude", "codex", "cursor"):  # cursor: remembered, not a Lab agent
        remember_agent(home, agent_id, installed=True)
    fake_probe.add("codex")
    installed: list[str] = []

    def _install(agent_id: str, dry_run: bool = False) -> str:
        if agent_id == "claude":
            installed.append(agent_id)
            return agent_id
        raise AssertionError(agent_id)

    monkeypatch.setattr(registry_lifecycle, "install_registry_agent", _install)
    monkeypatch.setattr(
        registry_lifecycle,
        "setup_registry_agent",
        lambda *a, **k: pytest.fail("restore must not re-run setup"),
    )
    result = agents.restore(home)
    assert result["ok"] and result["agents"] == ["claude"] and installed == ["claude"]


def test_restore_keeps_going_after_a_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch, fake_probe: set[str]
) -> None:
    remember_agent(home, "claude", installed=True)
    remember_agent(home, "pi", installed=True)

    def _install(agent_id: str, dry_run: bool = False) -> str:
        if agent_id == "claude":
            raise LabError("network down")
        return agent_id

    monkeypatch.setattr(registry_lifecycle, "install_registry_agent", _install)
    result = agents.restore(home)
    assert not result["ok"]
    assert [r["ok"] for r in result["results"]] == [False, True]
    assert "network down" in result["results"][0]["errors"][0]


def test_keys_limited_to_lab_agents(home: Path) -> None:
    assert agents.set_key("ANTHROPIC_API_KEY", SECRET, home)["present"]
    assert agents.unset_key("ANTHROPIC_API_KEY", home)["present"] is False
    with pytest.raises(LabError, match="not a key any Lab agent reads"):
        agents.set_key("FACTORY_API_KEY", SECRET, home)


def test_signed_in_from_login_files(
    home: Path, monkeypatch: pytest.MonkeyPatch, fake_probe: set[str]
) -> None:
    fake_probe.update({"claude", "opencode", "omp"})
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    (home / ".claude").mkdir()
    (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    rows = {r["id"]: r for r in agents.status(home)["agents"]}
    assert rows["claude"]["signed_in"] is True and rows["claude"]["ready"]
    assert rows["opencode"]["signed_in"] is False and not rows["opencode"]["ready"]
    (home / "data" / "opencode").mkdir(parents=True)
    (home / "data" / "opencode" / "auth.json").write_text("{}", encoding="utf-8")
    assert {r["id"]: r for r in agents.status(home)["agents"]}["opencode"]["ready"]
    # omp keeps its login where the Lab cannot see it: trust the install.
    assert rows["omp"]["signed_in"] is None and rows["omp"]["ready"]


def test_home_copies_found_and_removed_without_touching_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    scratch = tmp_path / "scratch"
    (scratch / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setenv("SCRATCH", str(scratch))
    claude = agents.get_lab_agent("claude")
    # A self-update's binary and versions, plus the login that must stay.
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "claude").write_bytes(b"x" * 100)
    versions = home / ".local" / "share" / "claude" / "versions"
    versions.mkdir(parents=True)
    (versions / "2.1.0").write_bytes(b"y" * 1000)
    (home / ".claude").mkdir()
    (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    # A link into the Lab's own scratch copy is not a home copy.
    (scratch / ".local" / "bin" / "opencode").write_bytes(b"z")
    (home / ".local" / "bin" / "opencode").symlink_to(scratch / ".local" / "bin" / "opencode")

    rows = agents.home_copies(claude, home)
    assert [r["path"] for r in rows] == ["~/.local/bin/claude", "~/.local/share/claude/versions"]
    assert sum(r["bytes"] for r in rows) == 1100
    assert agents.home_copies(agents.get_lab_agent("opencode"), home) == []

    result = agents.remove_home_copies("claude", home)
    assert result["ok"] and result["freed"] == 1100
    assert not (home / ".local" / "bin" / "claude").exists() and not versions.exists()
    assert (home / ".claude" / ".credentials.json").is_file()
    assert (home / ".local" / "bin" / "opencode").is_symlink()


def test_home_installs_are_programs_only() -> None:
    """Never list a config, login or history path as a removable copy."""
    for agent in agents.lab_agents():
        for raw in agent.home_installs:
            assert raw.startswith("~/"), raw
            assert not any(part in raw for part in (".json", ".toml", "auth", "credentials")), raw
            assert raw not in ("~/.claude", "~/.codex", "~/.pi", "~/.config", "~/.gemini"), raw


def test_rehome_removes_home_copies_then_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (tmp_path / "scratch").mkdir()
    monkeypatch.setenv("SCRATCH", str(tmp_path / "scratch"))
    (home / ".opencode" / "bin").mkdir(parents=True)
    (home / ".opencode" / "bin" / "opencode").write_bytes(b"x" * 10)
    order: list[str] = []
    monkeypatch.setattr(
        agents,
        "install",
        lambda agent_id: (
            order.append("install")
            or {"ok": not (home / ".opencode" / "bin").exists(), "agent": agent_id}
        ),
    )
    result = agents.rehome("opencode", home)
    assert result["ok"] and order == ["install"]
    assert result["removed"] == ["~/.opencode/bin"] and result["freed"] == 10
