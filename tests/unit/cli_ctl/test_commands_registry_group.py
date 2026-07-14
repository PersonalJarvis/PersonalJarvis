"""Tests for the curated `jarvis commands` group (Command Registry browser)."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app
from jarvis.cli_ctl.reserved import RESERVED_CONTROL_NAMES

runner = CliRunner()


def test_list(capture_api):
    runner.invoke(app, ["commands", "list"])
    assert capture_api["calls"][-1]["path"] == "/api/commands"
    assert capture_api["calls"][-1]["method"] == "GET"


def test_show(capture_api):
    runner.invoke(app, ["commands", "show", "brain-switch"])
    assert capture_api["calls"][-1]["path"] == "/api/commands/brain-switch"


def test_commands_is_a_reserved_control_name():
    """`jarvis commands ...` must route to the control CLI, not the launcher."""
    assert "commands" in RESERVED_CONTROL_NAMES
