"""Concurrency primitives: atomic writes, path locks, shared-home safety."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from astroai_lab.agent.agent_targets import McpTarget, _read_config, _write_config
from astroai_lab.core.pathlock import path_lock
from astroai_lab.errors import LabError
from astroai_lab.utils.json_utils import write_json


def test_write_json_is_atomic_and_parseable(tmp_path: Path) -> None:
    target = tmp_path / "mcp.json"
    errors: list[Exception] = []

    def _writer(n: int) -> None:
        for i in range(25):
            try:
                write_json(target, {"mcpServers": {f"srv-{n}-{i}": {"command": "x"}}})
                data = json.loads(target.read_text(encoding="utf-8"))
                assert isinstance(data.get("mcpServers"), dict)
            except Exception as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    final = json.loads(target.read_text(encoding="utf-8"))
    assert final["mcpServers"]


def test_path_lock_excludes_second_holder(tmp_path: Path) -> None:
    lock = tmp_path / "control.lock"
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def _first() -> None:
        with path_lock(lock, timeout=1):
            order.append("first")
            entered.set()
            release.wait(timeout=5)

    t = threading.Thread(target=_first)
    t.start()
    entered.wait(timeout=5)
    with pytest.raises(LabError), path_lock(lock, timeout=0.3):
        order.append("second")
    release.set()
    t.join(timeout=5)
    assert order == ["first"]
    assert not lock.exists(), "lock must be released on exit"


def test_path_lock_breaks_stale_lock_from_dead_pid(tmp_path: Path) -> None:
    lock = tmp_path / "stale.lock"
    lock.write_text("999999 0\n", encoding="utf-8")  # pid almost certainly dead
    with path_lock(lock, timeout=1):
        pass


def test_merge_mcp_config_roundtrip_yaml_and_json(tmp_path: Path) -> None:
    json_target = McpTarget("cursor", ".cursor/mcp.json")
    yaml_target = McpTarget("hermes", ".hermes/config.yaml", key="extensions", fmt="yaml")
    jpath = tmp_path / json_target.relpath
    ypath = tmp_path / yaml_target.relpath
    _write_config(jpath, {"mcpServers": {"a": {"command": "x"}}}, json_target.fmt)
    _write_config(ypath, {"extensions": {"b": {"command": "y"}}}, yaml_target.fmt)
    assert _read_config(jpath, json_target.fmt)["mcpServers"]["a"]["command"] == "x"
    assert _read_config(ypath, yaml_target.fmt)["extensions"]["b"]["command"] == "y"
