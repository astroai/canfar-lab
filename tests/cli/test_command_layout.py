"""Command layout: `studio` subcommands share the REPO slot; old names still work."""

from __future__ import annotations

from typer.testing import CliRunner

from canfar_lab.cli.main import app

runner = CliRunner()


def test_studio_review_reaches_the_review_group() -> None:
    result = runner.invoke(app, ["studio", "review", "--help"])
    assert result.exit_code == 0, result.output
    assert "doctor" in result.output
    assert "routers" in result.output


def test_studio_status_is_a_subcommand_not_a_repo() -> None:
    result = runner.invoke(app, ["studio", "status", "--help"])
    assert result.exit_code == 0, result.output
    assert "Show which Studio tabs are running" in result.output


def test_old_names_still_work() -> None:
    for argv in (["panel", "--help"], ["workspace", "--help"], ["desk", "--help"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, (argv, result.output)


def test_config_workspace_is_registered() -> None:
    result = runner.invoke(app, ["config", "workspace", "--help"])
    assert result.exit_code == 0, result.output
    assert "show" in result.output


def test_studio_accepts_options_after_the_repo() -> None:
    result = runner.invoke(app, ["--json", "studio", ".", "--skills"])
    assert result.exit_code == 0, result.output
    assert "skills" in result.output
