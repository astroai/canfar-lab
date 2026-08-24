"""Unit tests for the `help` command aggregator and pager logic."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import typer
from typer.main import get_command

import astroai_lab.cli.help_cmd as help_cmd
from astroai_lab.cli.main import app


def _expected_paths() -> list[tuple[str, ...]]:
    """Independently enumerate every registered command path from the click group.

    This mirrors help_cmd._command_paths deliberately — it is a test oracle that
    must be kept in sync if the walker ever gains filtering (e.g. hidden aliases).
    """
    root = get_command(app)
    paths: list[tuple[str, ...]] = [()]

    def walk(cmd: object, prefix: tuple[str, ...]) -> None:
        commands = getattr(cmd, "commands", None)
        if not commands:
            return
        for name, sub in commands.items():
            path = prefix + (name,)
            paths.append(path)
            walk(sub, path)

    walk(root, ())
    return paths


def test_command_paths_include_nested() -> None:
    paths = help_cmd._command_paths(app)
    assert () in paths
    assert ("agent",) in paths
    assert ("agent", "list") in paths
    assert ("env", "export") in paths


def test_top_level_commands_sorted() -> None:
    tops = help_cmd.top_level_commands(app)
    assert "agent" in tops
    assert "env" in tops
    assert "cluster" in tops
    assert "jobs" in tops
    assert "run" in tops
    assert "dashboard" not in tops
    assert "mcp" not in tops
    assert "autoscaler" not in tops
    assert tops == sorted(tops)


@patch("astroai_lab.cli.help_cmd.typer.echo")
@patch("astroai_lab.cli.help_cmd.subprocess.run")
def test_emit_pages_via_less_on_tty(mock_run, mock_echo) -> None:
    text = "\n".join(f"line {i}" for i in range(60))
    with (
        patch("astroai_lab.cli.help_cmd.sys.stdout.isatty", return_value=True),
        patch("astroai_lab.cli.help_cmd.shutil.which", return_value="/usr/bin/less"),
    ):
        help_cmd._emit(text)
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == ["less", "-R"]
    assert mock_run.call_args.kwargs["input"] == text
    mock_echo.assert_not_called()


@patch("astroai_lab.cli.help_cmd.typer.echo")
@patch("astroai_lab.cli.help_cmd.subprocess.run")
def test_emit_plain_when_not_tty(mock_run, mock_echo) -> None:
    text = "\n".join(f"line {i}" for i in range(60))
    with patch("astroai_lab.cli.help_cmd.sys.stdout.isatty", return_value=False):
        help_cmd._emit(text)
    mock_run.assert_not_called()
    mock_echo.assert_called_once_with(text)


@patch("astroai_lab.cli.help_cmd.typer.echo")
@patch("astroai_lab.cli.help_cmd.subprocess.run")
def test_emit_plain_when_short(mock_run, mock_echo) -> None:
    with (
        patch("astroai_lab.cli.help_cmd.sys.stdout.isatty", return_value=True),
        patch("astroai_lab.cli.help_cmd.shutil.which", return_value="/usr/bin/less"),
    ):
        help_cmd._emit("short\nhelp")
    mock_run.assert_not_called()
    mock_echo.assert_called_once()


@pytest.mark.parametrize("bad_path", ["nope", "agent nope", ""])
def test_print_one_help_unknown_raises(bad_path: str) -> None:
    # typer.Exit subclasses click's Exit (a RuntimeError), not SystemExit.
    with pytest.raises(typer.Exit) as exc:
        help_cmd.print_one_help(app, bad_path)
    assert exc.value.exit_code == 1


def test_unknown_path_error_json_emits_structured_error() -> None:
    """The JSON contract stays machine-readable on failure: {\"error\": ...}."""
    captured: list[dict] = []

    def _capture(data: dict) -> None:
        captured.append(data)

    with (
        patch("astroai_lab.cli.help_cmd.ui.print_json", side_effect=_capture),
        pytest.raises(typer.Exit) as exc,
    ):
        help_cmd._unknown_path_error(app, "nope", json_output=True)
    assert exc.value.exit_code == 1
    assert len(captured) == 1
    assert "error" in captured[0]
    assert "nope" in captured[0]["error"]


def test_completer_offers_top_level_and_nested() -> None:
    complete = help_cmd.command_path_completer(app)
    offered = complete(None, "")
    assert "agent" in offered
    assert "agent list" in offered
    assert "env export" in offered
    assert "help" in offered


def test_completer_excludes_hidden_alias() -> None:
    complete = help_cmd.command_path_completer(app)
    offered = complete(None, "")
    assert "guide" not in offered


def test_completer_filters_by_incomplete_prefix() -> None:
    complete = help_cmd.command_path_completer(app)
    agent_offered = complete(None, "agent")
    assert "agent" in agent_offered
    assert "agent list" in agent_offered
    # Completions are prefix matches of registered paths only.
    assert all(p.startswith("agent") for p in agent_offered)
    assert "env export" not in agent_offered
    assert all(p.startswith("env") for p in complete(None, "env"))


def test_help_command_option_has_shell_complete_wired() -> None:
    help_cmd_click = get_command(app).commands["help"]
    command_param = next(p for p in help_cmd_click.params if p.name == "command")
    assert getattr(command_param, "_custom_shell_complete", None) is not None
    # Exercise typer's compat invocation path end-to-end: click routes through
    # _custom_shell_complete and wraps plain strings into CompletionItem(.value).
    results = command_param.shell_complete(ctx=None, incomplete="agent")
    values = {getattr(r, "value", str(r)) for r in results}
    assert "agent" in values
    assert "agent list" in values


def test_command_inventory_covers_all_visible_paths() -> None:
    inventory = help_cmd.command_inventory(app)
    paths = {entry["path"] for entry in inventory}
    assert "init" in paths
    assert "status" in paths
    assert "agent list" in paths
    assert "env export" in paths
    assert "save" in paths
    assert "resume" in paths
    assert "cluster start" in paths
    assert "cluster status" in paths
    assert "cluster dashboard" in paths
    assert "jobs list" in paths
    assert "run" in paths
    # Hidden aliases are excluded from the machine inventory.
    assert "cluster ensure" not in paths
    assert "cluster scale" not in paths
    assert "cluster check" not in paths
    assert "dashboard" not in paths
    assert "mcp" not in paths
    assert "autoscaler" not in paths
    assert "guide" not in paths
    assert "saves" not in paths
    assert len(inventory) == len(help_cmd._visible_command_paths(app))


def test_command_inventory_entry_shape() -> None:
    status = next(e for e in help_cmd.command_inventory(app) if e["path"] == "status")
    assert status["hidden"] is False
    assert isinstance(status["help"], str)
    assert isinstance(status["options"], list)
    assert all("opts" in o and "name" in o for o in status["options"])


def test_command_help_json_nested() -> None:
    entry = help_cmd.command_help_json(app, "agent list")
    assert entry is not None
    assert entry["path"] == "agent list"
    assert "options" in entry


def test_command_help_json_unknown_returns_none() -> None:
    assert help_cmd.command_help_json(app, "nope") is None
    assert help_cmd.command_help_json(app, "agent nope") is None
    assert help_cmd.command_help_json(app, "") is None


def _capture_aggregate() -> str:
    """Run print_all_help with _emit patched and return the aggregate text."""
    captured: list[str] = []

    def _capture(text: str) -> None:
        captured.append(text)

    with patch("astroai_lab.cli.help_cmd._emit", side_effect=_capture):
        help_cmd.print_all_help(app)

    assert len(captured) == 1
    return captured[0]


def test_aggregate_includes_every_registered_command() -> None:
    """Every registered command's help must appear in the aggregate output."""
    aggregate = _capture_aggregate()

    # Expected set is derived independently of the code under test, so a
    # regression in _command_paths (e.g. dropping a nested group) is caught.
    expected = _expected_paths()
    assert set(help_cmd._command_paths(app)) == set(expected)
    assert expected, "no commands registered?"

    for path in expected:
        label = " ".join(path) or "(root)"
        rendered = help_cmd._render(app, path)
        assert rendered, f"help render produced no output for: {label}"
        # Every command's rendered --help must be present verbatim.
        assert rendered in aggregate, f"missing help for: {label}"


def test_aggregate_output_order_matches_registration_order() -> None:
    """The aggregate dump must list commands in registration order.

    `print_all_help` walks the click command tree, which preserves typer's
    registration order (click Groups keep a dict keyed in insertion order).
    The rendered help for each path must therefore appear strictly after the
    previous path's, in the same order as `_expected_paths()` enumerates them.
    """
    aggregate = _capture_aggregate()

    # Walk the aggregate with a moving cursor so each chunk must be found
    # strictly after the previous one — this also guards against substring
    # collisions (a later chunk accidentally matching inside an earlier one).
    cursor = 0
    for path in _expected_paths():
        label = " ".join(path) or "(root)"
        rendered = help_cmd._render(app, path)
        assert rendered, f"help render produced no output for: {label}"
        pos = aggregate.find(rendered, cursor)
        assert pos != -1, (
            f"help for {label!r} missing or out of order in aggregate output "
            f"(expected after offset {cursor})"
        )
        cursor = pos + len(rendered)
