"""Tests for the ultrawiki commands — the readable knowledge base from a terminal.

The curated group exists so an agent or a terminal user can ask "what does it
know about X" without a browser, and so the vault export is scriptable. The
export is the one destructive command here (it deletes its own stale notes),
so it must be gated rather than fire on sight.
"""

from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_ask_sends_question_and_evidence_limit(capture_api):
    result = runner.invoke(
        app,
        ["ultrawiki", "ask", "What changed?", "--evidence", "4", "--area", "work"],
    )
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/ultrawiki/ask"
    assert call["body"] == {"question": "What changed?", "k": 4, "area": "work"}


def test_topics_lists_the_entity_pages(capture_api):
    runner.invoke(app, ["ultrawiki", "topics"])
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/ultrawiki/explore/entities"
    assert call["method"] == "GET"


def test_topics_passes_the_search_filter(capture_api):
    runner.invoke(app, ["ultrawiki", "topics", "--search", "bora"])
    assert capture_api["calls"][-1]["query"]["q"] == "bora"


def test_topic_reads_one_entity_page(capture_api):
    runner.invoke(app, ["ultrawiki", "topic", "bora bora"])
    assert capture_api["calls"][-1]["path"] == "/api/ultrawiki/explore/entities/bora bora"


def test_moments_can_be_scoped_to_a_topic(capture_api):
    runner.invoke(app, ["ultrawiki", "moments", "--topic", "tahiti"])
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/ultrawiki/explore/moments"
    assert call["query"]["entity"] == "tahiti"


def test_moments_can_be_scoped_to_a_month(capture_api):
    runner.invoke(app, ["ultrawiki", "moments", "--month", "2026-07"])
    assert capture_api["calls"][-1]["query"]["month"] == "2026-07"


def test_graph_passes_the_mention_floor(capture_api):
    runner.invoke(app, ["ultrawiki", "graph", "--min-mentions", "5"])
    call = capture_api["calls"][-1]
    assert call["path"] == "/api/ultrawiki/explore/graph"
    # Query values arrive as strings — this is an HTTP query string, not JSON.
    assert str(call["query"]["min_mentions"]) == "5"


def test_vault_reports_the_status(capture_api):
    runner.invoke(app, ["ultrawiki", "vault"])
    assert capture_api["calls"][-1]["path"] == "/api/ultrawiki/vault/status"


def test_export_is_a_guarded_write(capture_api):
    result = runner.invoke(app, ["ultrawiki", "export", "--yes"])
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/ultrawiki/vault/export"


def test_export_without_yes_does_not_fire(capture_api):
    before = len(capture_api["calls"])
    runner.invoke(app, ["ultrawiki", "export"], input="n\n")
    # It rewrites a directory and deletes its own stale notes — a bare
    # invocation must ask first, exactly like every other destructive command.
    assert len(capture_api["calls"]) == before


def test_register_is_a_guarded_write(capture_api):
    result = runner.invoke(app, ["ultrawiki", "register", "--yes"])
    assert result.exit_code == 0
    call = capture_api["calls"][-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/ultrawiki/vault/register"
