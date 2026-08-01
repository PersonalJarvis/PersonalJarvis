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


def test_ingest_sends_guarded_write_request(capture_api):
    result = runner.invoke(
        app,
        [
            "wiki",
            "ingest",
            "The user will travel to San Francisco tomorrow.",
            "--source",
            "test:cli",
        ],
    )
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/wiki/ingest"
    assert call["body"] == {
        "text": "The user will travel to San Francisco tomorrow.",
        "source": "test:cli",
    }


def test_ingest_dry_run_sends_nothing(capture_api):
    result = runner.invoke(
        app,
        ["wiki", "ingest", "A durable statement for the Wiki.", "--dry-run"],
    )
    assert result.exit_code == 0
    assert capture_api["calls"] == []
    assert '"path": "/api/wiki/ingest"' in result.output


def test_reindex(capture_api):
    runner.invoke(app, ["wiki", "reindex"])
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/wiki/reindex"
    assert call["query"]["dry_run"] == "false"


def test_reindex_preview(capture_api):
    runner.invoke(app, ["wiki", "reindex", "--preview"])
    assert capture_api["calls"][-1]["query"]["dry_run"] == "true"


def test_backfill_requires_confirmation_for_model_write(capture_api):
    result = runner.invoke(app, ["wiki", "backfill"], input="n\n")
    assert result.exit_code != 0
    assert capture_api["calls"] == []


def test_backfill_sends_bounded_request_with_yes(capture_api):
    result = runner.invoke(
        app,
        ["wiki", "backfill", "--days", "3", "--max-sessions", "7", "--yes"],
    )
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/wiki/backfill"
    assert call["body"] == {
        "days": 3,
        "max_sessions": 7,
        "dry_run": False,
    }


def test_backfill_preview_is_non_destructive(capture_api):
    result = runner.invoke(app, ["wiki", "backfill", "--preview"])
    assert result.exit_code == 0
    assert capture_api["calls"][-1]["body"]["dry_run"] is True
