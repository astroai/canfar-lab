from __future__ import annotations

from pathlib import Path

from canfar_lab.sync import arc_data


def _ok(argv: list[str]) -> tuple[int, str]:
    return 0, "IDENTIFIER:/PATH local:/PATH"


def test_push_uses_canfar_data(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return _ok(argv)

    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / "one.json").write_text("{}\n", encoding="utf-8")
    out = arc_data.bridge(tmp_path, direction="push", user="ada", runner=run)
    assert out["ok"] is True
    assert out["transport"] == "arc"
    assert out["remote"] == "arc:/home/ada/.astroai/lab/sync"
    assert ["canfar", "data", "mkdir", "-p", out["remote"]] in calls
    assert any(item[2:4] == ["cp", "-R"] and "requests" in item[4] for item in calls)


def test_missing_data_command_stays_on_git(tmp_path: Path) -> None:
    out = arc_data.bridge(
        tmp_path,
        direction="push",
        user="ada",
        runner=lambda argv: (2, "No such command 'data'"),
    )
    assert out["ok"] is False
    assert out["transport"] == "off"
    assert "v1.5" in out["hint"]


def test_arc_user_env(monkeypatch) -> None:
    monkeypatch.setenv("CANFAR_LAB_ARC_USER", "ada")
    assert arc_data.arc_user() == "ada"
