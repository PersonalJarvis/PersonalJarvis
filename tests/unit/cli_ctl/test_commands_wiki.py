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


def test_vaults(capture_api):
    runner.invoke(app, ["wiki", "vaults"])
    assert capture_api["calls"][-1]["path"] == "/api/setup/obsidian/vaults"


def test_reindex(capture_api):
    runner.invoke(app, ["wiki", "reindex"])
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/wiki/reindex"
    assert call["query"]["dry_run"] == "false"


def test_reindex_preview(capture_api):
    runner.invoke(app, ["wiki", "reindex", "--preview"])
    assert capture_api["calls"][-1]["query"]["dry_run"] == "true"
