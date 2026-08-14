"""Tests for curated Agentic IDE terminal commands."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_rename_terminal_sends_the_new_call_sign(capture_api) -> None:
    result = runner.invoke(app, ["ide", "rename-terminal", "API pane", "Frontend"])

    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "PATCH"
    # The capture fixture exposes the decoded URL path; the client still
    # percent-encodes the space on the wire.
    assert call["path"] == "/api/agentic-ide/terminals/API pane"
    assert call["body"] == {"name": "Frontend"}


def test_tidy_sends_the_row_count(capture_api) -> None:
    """Straightening is a layout change, so it needs no confirmation."""
    result = runner.invoke(app, ["ide", "tidy", "--rows", "3"])

    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/agentic-ide/terminals/tidy"
    assert call["body"] == {"rows": 3}


def test_tidy_defaults_to_two_rows(capture_api) -> None:
    """The shape a workspace opens in, and the one the report asked for."""
    assert runner.invoke(app, ["ide", "tidy"]).exit_code == 0
    assert capture_api["calls"][-1]["body"] == {"rows": 2}


def test_close_terminals_requires_confirmation(capture_api) -> None:
    result = runner.invoke(app, ["ide", "close-terminals", "Mika", "Nova"])

    assert result.exit_code == 1
    assert capture_api["calls"] == []


def test_close_terminals_sends_one_dangerous_batch(capture_api) -> None:
    result = runner.invoke(
        app,
        ["ide", "close-terminals", "Mika", "Nova", "--yes"],
    )

    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/agentic-ide/terminals/close-batch"
    assert call["body"] == {"names": ["Mika", "Nova"]}


def test_close_terminals_exits_nonzero_when_any_terminal_remains_open(capture_api) -> None:
    capture_api["routes"][("POST", "/api/agentic-ide/terminals/close-batch")] = (
        200,
        {
            "ok": False,
            "closed": ["Mika"],
            "failed": [{"name": "Nova", "detail": "Still stopping."}],
        },
    )

    result = runner.invoke(
        app,
        ["ide", "close-terminals", "Mika", "Nova", "--yes"],
    )

    assert result.exit_code == 1
    assert '"failed"' in result.stdout
