from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from astroai_lab.agent.setup import SetupResult, agent_setup
from astroai_lab.agent.setup_state import (
    append_setup_log,
    dump_json,
    read_setup_state,
)
from astroai_lab.cli.main import app
from astroai_lab.core.session_resources import collect_resources

runner = CliRunner()


def test_append_setup_log_and_dump_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    append_setup_log(home, "hello")
    append_setup_log(home, "world\n")
    state = read_setup_state(home)
    assert state.log is not None
    assert "hello" in Path(state.log).read_text()
    assert dump_json({"a": 1}).startswith("{")


def test_agent_setup_records_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.agent.setup.run_bundle",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "astroai_lab.agent.setup.verify_setup",
        lambda home: [],
    )
    monkeypatch.setattr(
        "astroai_lab.core.paths.quota_used_pct",
        lambda path: 10,
    )
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_default_plugins",
        lambda **kwargs: [],
    )
    result = agent_setup(bundles=["cli"], force=True, dry_run=False, verify=True)
    assert result.ok
    assert result.exit_code == 0
    assert read_setup_state(home).ok


def test_agent_setup_verify_failure_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.agent.setup.run_bundle",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "astroai_lab.agent.setup.verify_setup",
        lambda home: ["missing mcp"],
    )
    monkeypatch.setattr(
        "astroai_lab.core.paths.quota_used_pct",
        lambda path: 10,
    )
    monkeypatch.setattr(
        "astroai_lab.agent.plugins.apply_default_plugins",
        lambda **kwargs: [],
    )
    result = agent_setup(bundles=["cli"], force=True, dry_run=False, verify=True)
    assert not result.ok
    assert result.partial
    assert result.exit_code == 2
    assert read_setup_state(home).failed is not None


def test_agent_setup_json_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.cli.agent_cmd.agent_setup_mod.agent_setup",
        lambda **k: SetupResult(
            ok=True,
            partial=False,
            mode="install",
            actions=("bundle:cli",),
            errors=(),
            warnings=(),
            stamp="now",
        ),
    )
    result = runner.invoke(app, ["--json", "agent", "setup", "cli"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True


def test_agent_install_json_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("astroai_lab.agent.install.refuse_if_home_owned", lambda *a, **k: None)
    monkeypatch.setattr(
        "astroai_lab.agent.install.classify_binary",
        lambda binary, home=None: {
            "binary": binary,
            "path": None,
            "source": "missing",
            "managed": False,
            "home_install": False,
            "home_path": None,
        },
    )
    result = runner.invoke(app, ["--json", "--dry-run", "agent", "install", "kilo"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["tool"] == "kilo"


def test_agent_plugins_and_list_json() -> None:
    result = runner.invoke(app, ["--json", "agent", "plugins", "list"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    result = runner.invoke(app, ["--json", "agent", "list"])
    # Incomplete setup exits 1 with ok:false; body is still valid JSON.
    assert result.exit_code in (0, 1)
    data = json.loads(result.stdout)
    assert "agents" in data


def test_agent_plugins_install_json_dry_run() -> None:
    result = runner.invoke(app, ["--json", "--dry-run", "agent", "plugins", "install", "ponytail"])
    assert result.exit_code in (0, 1, 2)
    if result.stdout.strip().startswith("{") or result.stdout.strip().startswith("["):
        pass
    else:
        assert "ponytail" in (result.stdout + result.stderr).lower() or result.exit_code == 0


def test_resources_cgroup_and_gpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.core.session_resources._cgroup_mem_pct",
        lambda: 42.0,
    )
    monkeypatch.setattr(
        "astroai_lab.core.session_resources._gpu_stats",
        lambda: [
            {
                "index": 0,
                "name": "TestGPU",
                "util_pct": 10.0,
                "mem_used_mib": 1.0,
                "mem_total_mib": 8.0,
            }
        ],
    )
    snap = collect_resources()
    assert snap.cgroup_mem_pct == 42.0
    assert snap.gpu[0]["name"] == "TestGPU"


def test_agent_sync_applies_bundles_and_stamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astroai_lab.agent.setup import agent_sync

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    seen: list[str] = []
    monkeypatch.setattr(
        "astroai_lab.agent.setup.default_bundle_names",
        lambda root: ["cli"],
    )
    monkeypatch.setattr(
        "astroai_lab.agent.setup.run_bundle",
        lambda name, *a, **k: seen.append(name),
    )
    monkeypatch.setattr(
        "astroai_lab.agent.setup.ensure_agent_dirs",
        lambda *a, **k: None,
    )
    agent_sync(dry_run=False)
    assert seen == ["cli"]
    assert read_setup_state(home).ok

    dry_home = tmp_path / "dry"
    dry_home.mkdir()
    monkeypatch.setenv("HOME", str(dry_home))
    agent_sync(dry_run=True)
    assert read_setup_state(dry_home).ok is False


def test_install_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.agent import install as inst

    # kilo/goose/cline/opencode/codex + generic npm/uv agents migrated out of
    # TOOLS into the YAML registry; TOOLS keeps quirky installers + utilities
    # (hermes/openclaw/cursor/…). See agent/registry.
    assert "qoder" in inst.list_tools()
    assert "cursor" in inst.list_tools()
    assert "agent" not in inst.list_tools()
    assert "freebuff" not in inst.list_tools()
    assert "swival" not in inst.list_tools()
    assert inst.tool_binary("cursor") == "agent"
    assert "kilo" not in inst.list_tools()
    assert inst.tool_binary("qoder") == "qodercli"
    rows = inst.list_tools_status()
    assert any(r["name"] == "qoder" for r in rows)

    # Timeout path for curl|bash without network
    monkeypatch.setattr(
        "astroai_lab.agent.install.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(
            __import__("subprocess").TimeoutExpired(cmd="curl", timeout=1)
        ),
    )
    with pytest.raises(Exception, match="timed out"):
        inst._curl_pipe_bash("https://example.invalid/install")


def test_curl_pipe_bash_streams_installer_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from astroai_lab.agent import install as inst

    class _FakeStdin:
        def write(self, data: bytes) -> None:
            pass

        def close(self) -> None:
            pass

    class _FakeStdout:
        def readline(self) -> bytes:
            if not getattr(self, "_done", False):
                self._done = True
                return b"Installing deps...\n"
            return b""

    class _FakeProc:
        returncode = 0
        stdin = _FakeStdin()
        stdout = _FakeStdout()

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(inst, "_require", lambda cmd: None)
    monkeypatch.setattr(inst, "curl_installer_environ", lambda env=None: {})
    monkeypatch.setattr(
        inst.subprocess,
        "run",
        lambda *a, **k: type("_R", (), {"stdout": b"#!/bin/bash\necho ok\n"})(),
    )
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *a, **k: _FakeProc())

    inst._curl_pipe_bash("https://example.test/install.sh", stream=True)
    err = capsys.readouterr().err
    assert "Downloading installer" in err
    assert "Running installer" in err
    assert "Installing deps" in err


def test_cgroup_and_gpu_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    from astroai_lab.core import session_resources as sr

    class FakePath:
        def __init__(self, p: object) -> None:
            self.p = str(p)

        def is_file(self) -> bool:
            return self.p in {
                "/sys/fs/cgroup/memory.current",
                "/sys/fs/cgroup/memory.max",
            }

        def read_text(self, encoding: str = "utf-8") -> str:
            return {
                "/sys/fs/cgroup/memory.current": "25\n",
                "/sys/fs/cgroup/memory.max": "100\n",
            }[self.p]

    monkeypatch.setattr(sr, "Path", FakePath)
    assert sr._cgroup_mem_pct() == 25.0

    monkeypatch.setattr(sr.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    class R:
        returncode = 0
        stdout = "0, Fake GPU, 12, 100, 8000\n"

    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: R())
    gpus = sr._gpu_stats()
    assert gpus[0]["name"] == "Fake GPU"
    assert gpus[0]["util_pct"] == 12.0


def test_status_json_has_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = runner.invoke(app, ["--json", "status"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "resources" in data
    assert "home" in data["resources"]


def test_agent_setup_quota_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.core.paths.quota_used_pct",
        lambda path: 99,
    )
    from astroai_lab.errors import LabError

    with pytest.raises(LabError, match="Home quota"):
        agent_setup(bundles=["cli"], force=False, dry_run=False)


def test_agent_project_json_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "astroai_lab.cli.agent_cmd.agent_setup_mod.agent_setup",
        lambda **k: (_ for _ in ()).throw(
            __import__("astroai_lab.errors", fromlist=["LabError"]).LabError("nope")
        ),
    )
    result = runner.invoke(app, ["--json", "agent", "setup", "--project", "--path", str(tmp_path)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
