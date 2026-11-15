"""canfar_lab.session.secrets: owner-only agent logins, keys and settings."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from canfar_lab.session import secrets


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DSH_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return home


def _mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_lock_down_strips_group_and_other(home: Path) -> None:
    lab = home / ".canfar" / "lab"
    lab.mkdir(parents=True)
    (lab / ".env").write_text("K=v\n", encoding="utf-8")
    claude = home / ".claude"
    claude.mkdir()
    creds = claude / ".credentials.json"
    creds.write_text("{}", encoding="utf-8")
    config = home / ".config"
    (config / "opencode").mkdir(parents=True)
    for path, mode in ((lab, 0o775), (lab / ".env", 0o644), (claude, 0o755), (creds, 0o644)):
        os.chmod(path, mode)
    os.chmod(config, 0o755)
    os.chmod(config / "opencode", 0o755)

    assert set(secrets.exposed(home)) >= {str(lab / ".env"), str(claude), str(creds)}
    result = secrets.lock_down(home)
    assert result["ok"] and result["changed"]
    assert _mode(lab) == 0o700 and _mode(lab / ".env") == 0o600
    assert _mode(claude) == 0o700 and _mode(creds) == 0o600
    assert _mode(config / "opencode") == 0o700
    assert _mode(config) == 0o755  # shared parent left alone
    assert secrets.exposed(home) == []
    assert secrets.lock_down(home)["changed"] == []


def test_never_adds_permissions(home: Path) -> None:
    claude = home / ".claude"
    claude.mkdir()
    os.chmod(claude, 0o500)
    secrets.lock_down(home)
    assert _mode(claude) == 0o500
    os.chmod(claude, 0o700)


def test_dry_run_changes_nothing(home: Path) -> None:
    (home / ".codex").mkdir()
    os.chmod(home / ".codex", 0o755)
    result = secrets.lock_down(home, dry_run=True)
    assert result["changed"] and _mode(home / ".codex") == 0o755
