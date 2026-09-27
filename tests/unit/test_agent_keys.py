"""Model API key store: shared dotenv + dsh credentials, never printing values."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from canfar_lab import studio as studio_mod
from canfar_lab.agent import keys
from canfar_lab.cli.main import app
from canfar_lab.errors import LabError

SECRET = "sk-test-0123456789abcdef"
runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DSH_HOME", raising=False)
    for row in keys.catalog():
        monkeypatch.delenv(row["key"], raising=False)
    return home


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _creds(home: Path) -> dict:
    return yaml.safe_load((home / ".dsh" / ".credentials.yaml").read_text(encoding="utf-8"))


def _dotenv(home: Path) -> str:
    return (home / ".astroai" / "lab" / ".env").read_text(encoding="utf-8")


def test_catalog_lists_openrouter_and_dsh_routes() -> None:
    rows = {row["key"]: row for row in keys.catalog()}
    assert rows["OPENROUTER_API_KEY"]["dsh_route"] is None
    assert "OpenCode Interpreter" in rows["OPENROUTER_API_KEY"]["used_by"]
    assert rows["ANTHROPIC_API_KEY"]["dsh_route"] == "anthropic"
    assert rows["ANTHROPIC_API_KEY"]["signup_url"].startswith("https://")
    assert all(row["label"] for row in rows.values())


def test_set_dsh_key_writes_both_stores_private_and_keeps_records(home: Path) -> None:
    creds = home / ".dsh" / ".credentials.yaml"
    creds.parent.mkdir()
    creds.write_text(
        "version: 1\nrecords:\n  client-connection/browser-session:\n"
        "    kind: grant\n    payload:\n      secret: keepme\n",
        encoding="utf-8",
    )
    creds.chmod(0o600)

    result = keys.set_key(home, "ANTHROPIC_API_KEY", f"  {SECRET}\n")

    assert result == {"key": "ANTHROPIC_API_KEY", "present": True, "changed": True}
    doc = _creds(home)
    assert doc["version"] == 1
    assert doc["refs"] == {"ANTHROPIC_API_KEY": SECRET}
    assert doc["records"]["client-connection/browser-session"]["payload"]["secret"] == "keepme"
    assert f"ANTHROPIC_API_KEY={SECRET}" in _dotenv(home)
    assert _mode(creds) == 0o600
    assert _mode(home / ".astroai" / "lab" / ".env") == 0o600
    assert not creds.with_name(".credentials.yaml.lock").exists()
    assert "_astroai_dotenv" in (home / ".astroai" / "lab" / "agent-env.sh").read_text()
    assert keys.set_key(home, "ANTHROPIC_API_KEY", SECRET)["changed"] is False


def test_shared_key_stays_out_of_dsh_credentials(home: Path) -> None:
    keys.set_key(home, "OPENROUTER_API_KEY", SECRET)
    assert f"OPENROUTER_API_KEY={SECRET}" in _dotenv(home)
    assert not (home / ".dsh" / ".credentials.yaml").exists()


def test_unset_removes_from_both_and_status_never_leaks(home: Path) -> None:
    keys.set_key(home, "OPENAI_API_KEY", SECRET)
    keys.set_key(home, "OPENROUTER_API_KEY", SECRET + "x")
    row = next(r for r in keys.status(home) if r["key"] == "OPENAI_API_KEY")
    assert row["present"] is True and row["sources"] == ["studio", "assistant"]
    assert SECRET not in json.dumps(keys.status(home))

    assert keys.unset_key(home, "OPENAI_API_KEY")["changed"] is True
    assert "OPENAI_API_KEY" not in _dotenv(home)
    assert "OPENROUTER_API_KEY=" in _dotenv(home)
    assert "refs" not in _creds(home)
    assert keys.unset_key(home, "OPENAI_API_KEY")["changed"] is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NOT_A_KNOWN_KEY", SECRET),
        ("ANTHROPIC_API_KEY", "short"),
        ("ANTHROPIC_API_KEY", "sk-has space-0123"),
        ("ANTHROPIC_API_KEY", "sk-$(reboot)-0123456"),
        ("ANTHROPIC_API_KEY", "sk-'quoted'-012345"),
    ],
)
def test_set_rejects_unknown_names_and_shell_unsafe_values(
    home: Path, name: str, value: str
) -> None:
    with pytest.raises(LabError):
        keys.set_key(home, name, value)
    assert not (home / ".astroai" / "lab" / ".env").exists()


def test_live_lock_blocks_then_times_out(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    creds = home / ".dsh" / ".credentials.yaml"
    creds.parent.mkdir()
    lock = creds.with_name(".credentials.yaml.lock")
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    monkeypatch.setattr(keys, "LOCK_WAIT_SEC", 0.2)
    with pytest.raises(LabError, match="Timed out"), keys._file_lock(creds, wait=0.2):
        pass
    assert lock.exists()


def test_stale_lock_from_dead_pid_is_broken(home: Path) -> None:
    creds = home / ".dsh" / ".credentials.yaml"
    creds.parent.mkdir()
    lock = creds.with_name(".credentials.yaml.lock")
    lock.write_text("999999999\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    keys.set_key(home, "DEEPSEEK_API_KEY", SECRET)
    assert _creds(home)["refs"]["DEEPSEEK_API_KEY"] == SECRET
    assert not lock.exists()


def test_sync_copies_missing_and_newest_file_wins(home: Path) -> None:
    dotenv = home / ".astroai" / "lab" / ".env"
    dotenv.parent.mkdir(parents=True)
    dotenv.write_text(f"GEMINI_API_KEY={SECRET}\nOPENAI_API_KEY=sk-env-000000001\n")
    creds = home / ".dsh" / ".credentials.yaml"
    creds.parent.mkdir()
    creds.write_text(
        "version: 1\nrefs:\n"
        "  ANTHROPIC_API_KEY: sk-ant-00000000\n"
        "  OPENAI_API_KEY: sk-dsh-000000002\n"
    )
    old = time.time() - 60
    os.utime(dotenv, (old, old))

    changed = keys.sync_keys(home)

    assert changed == ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"]
    refs = _creds(home)["refs"]
    assert refs["GEMINI_API_KEY"] == SECRET
    assert refs["OPENAI_API_KEY"] == "sk-dsh-000000002"
    text = _dotenv(home)
    assert "ANTHROPIC_API_KEY=sk-ant-00000000" in text
    assert "OPENAI_API_KEY=sk-dsh-000000002" in text
    assert keys.sync_keys(home) == []


def test_cli_set_reads_stdin_and_list_json_hides_value(home: Path) -> None:
    result = runner.invoke(app, ["agent", "keys", "set", "GEMINI_API_KEY"], input=SECRET + "\n")
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    listed = runner.invoke(app, ["--json", "agent", "keys", "list"])
    assert listed.exit_code == 0, listed.output
    assert SECRET not in listed.output
    rows = {row["key"]: row for row in json.loads(listed.output)["keys"]}
    assert rows["GEMINI_API_KEY"]["present"] is True
    assert rows["ANTHROPIC_API_KEY"]["present"] is False


def test_acknowledge_welcome_notice_preserves_settings(home: Path) -> None:
    settings = home / ".dsh" / "settings.yaml"
    settings.parent.mkdir()
    settings.write_text("llm-pi-ai:\n  providers:\n    google:\n      apiKeyEnv: GEMINI_API_KEY\n")
    assert studio_mod.acknowledge_welcome_notice(home, "2026-08-13.1")
    doc = yaml.safe_load(settings.read_text())
    assert doc["ui-onboarding"] == {"welcomeNoticeVersion": "2026-08-13.1"}
    assert doc["llm-pi-ai"]["providers"]["google"]["apiKeyEnv"] == "GEMINI_API_KEY"
    assert studio_mod.acknowledge_welcome_notice(home, "2026-08-13.1") is None
    assert studio_mod.acknowledge_welcome_notice(home, None) is None


def test_seed_workspace_only_on_empty_registry(home: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    store = home / ".dsh" / "storages" / "workspace.json"

    assert studio_mod.seed_dsh_workspace(home, work)
    doc = json.loads(store.read_text())
    (wid,) = doc["global"]["workspaceIds"]
    record = doc["tables"]["workspaces"][wid]
    assert record["path"] == os.path.realpath(work)
    assert record["title"] == "work" and record["sessionIds"] == []
    assert doc["unit"] == {"name": "workspace", "version": 2}
    assert _mode(store) == 0o600
    assert studio_mod.seed_dsh_workspace(home, work) is None

    store.unlink()
    (home / ".dsh" / "sessions").mkdir(parents=True)
    (home / ".dsh" / "sessions" / "old.jsonl").write_text("{}\n")
    assert studio_mod.seed_dsh_workspace(home, work) is None
    assert not store.exists()


def test_studio_workdir_reads_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "src"
    work.mkdir()
    (tmp_path / "studio-cwd").write_text(f"{work}\n")
    monkeypatch.delenv("ASTROAI_STUDIO_CWD", raising=False)
    monkeypatch.setenv("ASTROAI_STUDIO_STATE", str(tmp_path))
    assert studio_mod.studio_workdir() == work.resolve()


def test_catalog_lists_only_secret_names() -> None:
    names = {row["key"] for row in keys.catalog()}
    assert "GOOSE_PROVIDER" not in names
    assert all(n.endswith(("_KEY", "_TOKEN")) for n in names)


def test_cli_set_invalid_value_is_a_clean_error(home: Path) -> None:
    result = runner.invoke(
        app, ["agent", "keys", "set", "GEMINI_API_KEY"], input="has space $(x)\n"
    )
    assert result.exit_code == 1
    assert "does not look like an API key" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    result = runner.invoke(
        app, ["--json", "agent", "keys", "set", "GEMINI_API_KEY"], input="bad value\n"
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False
