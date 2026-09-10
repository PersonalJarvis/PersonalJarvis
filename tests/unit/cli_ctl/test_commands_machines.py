from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_machine_list(capture_api):
    result = runner.invoke(app, ["machines", "list"])
    assert result.exit_code == 0
    assert capture_api["calls"][-1]["path"] == "/api/machines"


def test_move_requires_confirmation(capture_api):
    result = runner.invoke(app, ["machines", "move", "writer", "--host", "node"])
    assert result.exit_code == 1
    assert not capture_api["calls"]


def test_move_queues_the_selected_host(capture_api):
    result = runner.invoke(app, ["machines", "move", "writer", "--host", "node", "--yes"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["path"] == "/api/machines/agents/writer/move"
