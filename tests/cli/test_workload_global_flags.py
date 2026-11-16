"""`canfar lab --dry-run/--yes/--json` reach the cluster and jobs commands."""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from canfar_lab.cli.main import app

runner = CliRunner()

_STOPPED = {
    "stopped_cluster": True,
    "manager_found": True,
    "destroyed_manager": True,
    "destroyed_autoscaler_workers": [],
    "cleared_state": True,
    "error": None,
    "detail": None,
}


def test_dry_run_cluster_stop_destroys_nothing() -> None:
    with patch("canfar_workload.cli.cluster_stop_payload") as stop:
        result = runner.invoke(app, ["--dry-run", "cluster", "stop"])
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    stop.assert_not_called()


def test_cluster_stop_needs_yes_without_a_terminal() -> None:
    with patch("canfar_workload.cli.cluster_stop_payload") as stop:
        result = runner.invoke(app, ["cluster", "stop"])
    assert result.exit_code == 1
    stop.assert_not_called()


def test_global_yes_and_json_reach_cluster_stop() -> None:
    with patch("canfar_workload.cli.cluster_stop_payload", return_value=_STOPPED) as stop:
        result = runner.invoke(app, ["--yes", "--json", "cluster", "stop"])
    assert result.exit_code == 0, result.output
    stop.assert_called_once()
    assert json.loads(result.stdout)["destroyed_manager"] is True


def test_dry_run_jobs_cancel_and_submit_do_nothing() -> None:
    with (
        patch("canfar_workload.cli.job_cancel_payload") as cancel,
        patch("canfar_workload.cli.job_submit_payload") as submit,
    ):
        assert runner.invoke(app, ["--dry-run", "jobs", "cancel", "r1"]).exit_code == 0
        assert runner.invoke(app, ["--dry-run", "jobs", "submit", "--cmd", "true"]).exit_code == 0
    cancel.assert_not_called()
    submit.assert_not_called()


def test_cluster_status_reports_an_unreachable_manager() -> None:
    with patch(
        "canfar_workload.cli.cluster_status_payload", side_effect=ConnectionError("no route")
    ):
        result = runner.invoke(app, ["cluster", "status"])
    assert result.exit_code == 1
    assert "no route" in result.output
