"""CLI unit tests (no live Ray cluster)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from astroai_workload.cli import app
from astroai_workload.models import RunStatus

runner = CliRunner()


class _FakeExecutor:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.kwargs = kwargs

    def submit(self, spec):
        return spec.run_id

    def status(self, run_id):
        return RunStatus.SUCCEEDED

    def logs(self, run_id):
        return f"logs:{run_id}\n"

    def cancel(self, run_id):
        return None

    def wait(self, run_id, **kwargs):
        return RunStatus.SUCCEEDED, f"logs:{run_id}\n"

    def submit_and_wait(self, spec, **kwargs):
        return RunStatus.SUCCEEDED, f"logs:{spec.run_id}\n"

    def list_jobs(self):
        return [
            {
                "submission_id": "job-1",
                "status": "succeeded",
                "entrypoint": "python job.py",
            }
        ]


def test_cli_status_and_list(monkeypatch) -> None:
    monkeypatch.setattr("astroai_workload.cli.RayExecutor", _FakeExecutor)
    result = runner.invoke(app, ["status", "job-1"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "succeeded"

    listed = runner.invoke(app, ["list"])
    assert listed.exit_code == 0
    assert "job-1" in listed.stdout


def test_cli_submit_cmd(monkeypatch) -> None:
    monkeypatch.setattr("astroai_workload.cli.RayExecutor", _FakeExecutor)
    result = runner.invoke(app, ["submit", "--cmd", "python -c 'print(1)'", "--run-id", "x1"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "x1"


def test_cli_run_script(monkeypatch, tmp_path: Path) -> None:
    script = tmp_path / "job.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    def _fake_run_script(path, **kwargs):
        assert Path(path) == script
        assert kwargs["cpus"] == 2
        assert kwargs["memory"] == "2GiB"
        assert kwargs["run_id"] == "run-1"
        assert kwargs["args"] == ["--epochs", "1"]
        assert kwargs["inputs"] == ["vos://in.fits"]
        assert kwargs["expected_outputs"] == ["/arc/projects/g/out"]
        return RunStatus.SUCCEEDED, "ok\n"

    monkeypatch.setattr("astroai_workload.cli.run_script", _fake_run_script)
    result = runner.invoke(
        app,
        [
            "run",
            str(script),
            "--cpus",
            "2",
            "--memory",
            "2GiB",
            "--run-id",
            "run-1",
            "--input",
            "vos://in.fits",
            "--output",
            "/arc/projects/g/out",
            "--epochs",
            "1",
        ],
    )
    assert result.exit_code == 0
    assert "ok" in result.stdout


def test_cli_submit_stores_input_uris(monkeypatch) -> None:
    seen: dict[str, list[str]] = {}

    class _Capture(_FakeExecutor):
        def submit(self, spec):
            seen["inputs"] = [p.uri for p in spec.inputs]
            seen["outputs"] = [p.uri for p in spec.expected_outputs]
            return spec.run_id

    monkeypatch.setattr("astroai_workload.cli.RayExecutor", _Capture)
    result = runner.invoke(
        app,
        [
            "submit",
            "--cmd",
            "python train.py",
            "--run-id",
            "mosaic-1",
            "--input",
            "vos://in.fits",
            "--output",
            "/arc/projects/g/out",
        ],
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "mosaic-1"
    assert seen["inputs"] == ["vos://in.fits"]
    assert seen["outputs"] == ["/arc/projects/g/out"]


def test_cli_help_names_cluster_and_run() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "cluster start" in out
    assert "astroai run" in out


def test_cli_help_has_no_legacy_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "--autoscaling" not in out
    assert "--workers" not in out
    assert "scale" not in out
    assert "ensure" not in out


def test_cluster_help_lists_autoscaling_surface() -> None:
    result = runner.invoke(app, ["cluster", "--help"])
    assert result.exit_code == 0
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "start" in out
    assert "status" in out
    assert "stop" in out


def test_cluster_start_forwards_autoscaling_options(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def _fake_payload(**kwargs):
        captured.update(kwargs)
        return {
            "manager_url": "https://m",
            "jobs_address": "https://m/dashboard",
            "cluster_phase": "Running",
            "joined_workers": 0,
            "autoscaling": True,
        }

    monkeypatch.setattr("astroai_workload.cli.cluster_start_payload", _fake_payload)
    result = runner.invoke(
        app,
        ["cluster", "start", "--json", "--min-workers", "1", "--max-workers", "4"],
    )
    assert result.exit_code == 0, result.output
    assert '"autoscaling": true' in result.output
    assert captured["min_workers"] == 1
    assert captured["max_workers"] == 4


def test_cli_run_missing_script_is_not_invalid_value(tmp_path: Path) -> None:
    missing = tmp_path / "nope.py"
    result = runner.invoke(app, ["run", str(missing)])
    assert result.exit_code == 1
    combined = result.stdout + result.stderr
    assert "Script not found" in combined
    assert "Invalid value" not in combined


def test_cli_submit_wait_prints_status(monkeypatch) -> None:
    monkeypatch.setattr("astroai_workload.cli.RayExecutor", _FakeExecutor)
    result = runner.invoke(app, ["submit", "--cmd", "python train.py", "--run-id", "w1", "--wait"])
    assert result.exit_code == 0
    assert "w1" in result.stdout
    assert "succeeded" in result.stderr
    assert "logs:w1" in result.stdout


def test_cli_logs_cancel_wait(monkeypatch) -> None:
    monkeypatch.setattr("astroai_workload.cli.RayExecutor", _FakeExecutor)
    logs = runner.invoke(app, ["logs", "job-1"])
    assert logs.exit_code == 0
    assert logs.stdout == "logs:job-1\n"
    cancel = runner.invoke(app, ["cancel", "job-1"])
    assert cancel.exit_code == 0
    assert "cancel requested: job-1" in cancel.stdout
    waited = runner.invoke(app, ["wait", "job-1"])
    assert waited.exit_code == 0
    assert "succeeded" in waited.stderr


def test_hello_example_runs_locally() -> None:
    script = Path(__file__).resolve().parents[2] / "examples" / "workload" / "hello" / "job.py"
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "hello from ray" in proc.stdout
