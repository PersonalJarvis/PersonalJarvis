"""Tests for the wiki commands."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_recall(capture_api):
    runner.invoke(app, ["wiki", "recall", "melbourne"])
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/wiki/search" and call["query"]["q"] == "melbourne"


def test_page(capture_api):
    runner.invoke(app, ["wiki", "page", "people/jane"])
    assert capture_api["calls"][-1]["path"] == "/api/wiki/page/people/jane"


def test_tree(capture_api):
    runner.invoke(app, ["wiki", "tree"])
    assert capture_api["calls"][-1]["path"] == "/api/wiki/tree"
