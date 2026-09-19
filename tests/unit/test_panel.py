"""Tests for astroai_lab.panel (headless review-bench runner)."""

from __future__ import annotations

from pathlib import Path

import pytest

from astroai_lab import panel as panel_mod
from astroai_lab.errors import LabError


@pytest.fixture(autouse=True)
def _clear_dsh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENCODE_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep key discovery off the real ~/.local/share/opencode/auth.json."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_slugify() -> None:
    assert panel_mod.slugify("My Smoke_test 01") == "my-smoke-test-01"
    assert panel_mod.slugify("!!!") == "review"


def test_panel_id_collision_appends_time(tmp_path: Path) -> None:
    pid = panel_mod.panel_id_for(tmp_path, "smoke")
    assert pid.endswith("-smoke")
    (tmp_path / "panel" / pid).mkdir(parents=True)
    (tmp_path / "panel" / pid / "00-brief.md").write_text("brief", encoding="utf-8")
    again = panel_mod.panel_id_for(tmp_path, "smoke")
    assert again != pid and again.startswith(pid)


def test_build_task_carries_protocol_and_claims(tmp_path: Path) -> None:
    task = panel_mod.build_task(tmp_path, "C1: covers 90%", "2026-09-11-smoke")
    assert "C1: covers 90%" in task
    assert "panel/2026-09-11-smoke/" in task
    assert "SKILL.md" in task and "panel-round1.js" in task
    assert "established | suggestive | speculative" in task
    assert "ask_<role>" in task


def test_dsh_cmd_attaches_repo_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.studio.dsh_binary", lambda: "/opt/astroai/bin/dsh")
    patch = tmp_path / ".dsh" / "cordis.patch.yml"
    patch.parent.mkdir(parents=True)
    patch.write_text("[]\n", encoding="utf-8")
    cmd = panel_mod.dsh_cmd(patch=patch, task="do it")
    assert cmd[:3] == ["/opt/astroai/bin/dsh", "--profile", "headless"]
    assert "--patch" in cmd and str(patch) in cmd and cmd[-1] == "do it"
    bare = panel_mod.dsh_cmd(patch=tmp_path / ".dsh" / "missing.yml", task="do it")
    assert "--patch" not in bare


def test_dsh_cmd_rejects_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("astroai_lab.studio.dsh_binary", lambda: None)
    with pytest.raises(LabError, match="No `dsh` executable"):
        panel_mod.dsh_cmd(patch=None, task="do it")


def test_run_panel_dry_run_resolves_without_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_home: Path
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    result = panel_mod.run_panel(tmp_path, "C1: x covers y", "smoke", dry_run=True)
    assert result["panel_id"].endswith("-smoke")
    assert result["route"] == "google"
    assert "C1: x covers y" in result["task"]
    assert result["report_dir"].endswith(result["panel_id"])
    assert not (tmp_path / ".dsh").exists()  # dry-run writes nothing


def test_run_panel_rejects_missing_repo(tmp_path: Path) -> None:
    with pytest.raises(LabError):
        panel_mod.run_panel(tmp_path / "absent", "C1: x", dry_run=True)


def test_run_panel_fallback_on_opencode_go_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_home: Path
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "zen")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d-key")
    monkeypatch.setattr("astroai_lab.studio.dsh_binary", lambda: "/opt/astroai/bin/dsh")
    calls: list[list[str]] = []

    def fake_run(cmd, *, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append(list(cmd))
        if len(calls) == 1:
            raise LabError("MissingSessionID / x-opencode-session required (Console Go 400)")
        return None

    monkeypatch.setattr("astroai_lab.utils.subprocess.run", fake_run)
    result = panel_mod.run_panel(tmp_path, "C1: x", "smoke", dry_run=False)
    assert result["route"] == "deepseek-official"
    assert result["fallback_note"] and "falling back" in result["fallback_note"]
    assert len(calls) == 2
    assert calls[0][0] == "/opt/astroai/bin/dsh"


def test_scaffold_matches_template(tmp_path: Path) -> None:
    from astroai_lab.agent.review_bench import vendored_review_bench_root

    wrote = panel_mod.scaffold_repo_dsh(tmp_path)
    assert len(wrote) == 2
    template = vendored_review_bench_root() / "project-template" / ".dsh"
    for name in ("cordis.patch.yml", "README.md"):
        assert (tmp_path / ".dsh" / name).read_bytes() == (template / name).read_bytes()
    assert panel_mod.scaffold_repo_dsh(tmp_path) == []  # no clobber


def test_panel_status_extracts_verdicts(tmp_path: Path) -> None:
    panel_dir = tmp_path / "panel" / "2026-09-11-smoke"
    panel_dir.mkdir(parents=True)
    (panel_dir / "02-report.md").write_text(
        "# Report\n\n| C1 | established — coverage 0.91 |\nProse here.\n", encoding="utf-8"
    )
    table = panel_mod.panel_status(panel_dir)
    assert "established" in table
    with pytest.raises(LabError):
        panel_mod.panel_status(tmp_path / "panel" / "missing")
