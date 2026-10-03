"""Board insights: one picture built from the stores the app already keeps.

Every source is a real (temporary) store written the way its owner writes it;
the tests check the counts, the local-day bucketing, the streak across
sources and that a missing store degrades to ``available: false``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.board.aggregator import BoardAggregator
from jarvis.board.insights import (
    SERIES_DAYS,
    BoardInsights,
    InsightSources,
    current_streak,
    longest_streak,
)
from jarvis.board.store import BoardStore
from jarvis.dictation.stats import DictationStats
from jarvis.ui.web.board_routes import board_router


def _noon(days_ago: int) -> datetime:
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    return now - timedelta(days=days_ago)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _write_dictation(path: Path) -> None:
    stats = DictationStats(path)
    stats.record(created_at=_noon(0).isoformat(), word_count=120, duration_s=60.0)
    stats.record(created_at=_noon(1).isoformat(), word_count=80, duration_s=40.0)


def _write_agent_chat(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE agent_chat_sessions (
            session_id TEXT PRIMARY KEY, provider TEXT, surface TEXT
        );
        CREATE TABLE agent_chat_events (
            session_id TEXT, seq INTEGER, ts_ms INTEGER, kind TEXT, payload TEXT
        );
        """
    )
    conn.execute("INSERT INTO agent_chat_sessions VALUES ('s1', 'claude-api', 'jarvis')")
    conn.execute("INSERT INTO agent_chat_sessions VALUES ('s2', 'gemini', 'society')")
    rows = [
        ("s1", 1, _ms(_noon(2)), "user_message", "{}"),
        ("s1", 2, _ms(_noon(2)), "assistant_text", "{}"),
        ("s2", 1, _ms(_noon(2)), "user_message", "{}"),
    ]
    conn.executemany("INSERT INTO agent_chat_events VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _write_cli_index(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE cli_turns (agent TEXT, session_id TEXT, ts_ms INTEGER, "
        "tokens_in INTEGER, tokens_out INTEGER)"
    )
    rows = [
        ("claude-cli", "a", _ms(_noon(3)), 10, 5),
        ("claude-cli", "a", _ms(_noon(3)) + 60_000, 10, 5),
        ("claude-cli", "b", _ms(_noon(40)), 1, 1),
        ("codex-cli", "c", _ms(_noon(3)), 100, 50),
    ]
    conn.executemany("INSERT INTO cli_turns VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _sources(tmp_path: Path) -> InsightSources:
    dictation = tmp_path / "dictation_stats.json"
    chat = tmp_path / "agent_chat.db"
    cli = tmp_path / "cli_usage_index.db"
    _write_dictation(dictation)
    _write_agent_chat(chat)
    _write_cli_index(cli)
    board = tmp_path / "board" / "personal.db"
    BoardAggregator(jsonl_dir=tmp_path / "fr", db_path=board).run()
    return InsightSources(
        dictation_stats=dictation, board_db=board, agent_chat_db=chat, cli_index_db=cli,
    )


def test_build_counts_every_source(tmp_path: Path) -> None:
    picture = BoardInsights(_sources(tmp_path)).build()

    assert picture["dictation"] == {
        "available": True, "words": 200, "dictations": 2, "seconds": 100.0,
    }
    assert picture["chats"]["messages"] == 2
    assert picture["chats"]["sessions"] == 2
    assert picture["chats"]["providers"][0]["provider"] == "claude-api"
    assert picture["chats"]["providers"][0]["jarvis_sessions"] == 1

    agents = {item["agent"]: item for item in picture["agents"]["items"]}
    assert agents["claude-cli"]["sessions"] == 2
    assert agents["claude-cli"]["sessions_30d"] == 1
    assert agents["claude-cli"]["turns"] == 3
    assert agents["claude-cli"]["tokens"] == 32
    assert picture["agents"]["sessions"] == 3
    assert picture["agents"]["turns"] == 4

    assert picture["reference"] == {"typing_wpm": 40, "novel_words": 90_000}
    assert picture["trend"]["words_30d"] == 200


def test_daily_series_and_streak_span_all_sources(tmp_path: Path) -> None:
    picture = BoardInsights(_sources(tmp_path)).build()

    days = picture["days"]
    assert len(days) == SERIES_DAYS
    by_date = {d["date"]: d for d in days}
    assert by_date[_noon(0).date().isoformat()]["dictation_words"] == 120
    assert by_date[_noon(2).date().isoformat()]["chat_messages"] == 2
    assert by_date[_noon(3).date().isoformat()]["agent_sessions"] == 2
    # Dictation today + yesterday, chat two days ago, agents three days ago:
    # four consecutive days, each from a different store.
    assert picture["streak"]["current_days"] == 4
    assert picture["records"]["best_agent_day"]["value"] == 2

    punch = picture["punch_card"]
    assert len(punch) == 7 and all(len(row) == 24 for row in punch)
    assert sum(map(sum, punch)) == 2 + 4  # chat messages + agent turns


def test_missing_stores_read_as_unavailable(tmp_path: Path) -> None:
    picture = BoardInsights(
        InsightSources(
            dictation_stats=tmp_path / "missing.json",
            board_db=tmp_path / "missing.db",
            sessions_db=None,
            agent_chat_db=tmp_path / "missing_chat.db",
            cli_index_db=None,
        )
    ).build()
    assert picture["dictation"]["available"] is False
    assert picture["voice"]["available"] is False
    assert picture["chats"]["available"] is False
    assert picture["agents"]["available"] is False
    assert picture["streak"] == {
        "current_days": 0, "longest_days": 0, "active_days": 0, "first_day": None,
    }
    assert picture["records"] == {"best_words_day": None, "best_agent_day": None}


def test_streak_grace_for_a_quiet_today() -> None:
    today = _noon(0).date()
    days = {(today - timedelta(days=n)).isoformat() for n in (1, 2, 3, 7, 8)}
    assert current_streak(days, today) == 3
    assert longest_streak(days) == 3


def test_route_returns_the_picture_with_categories(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    app = FastAPI()
    app.include_router(board_router)
    app.state.board_store = BoardStore(db_path=sources.board_db)
    app.state.board_insights = BoardInsights(sources)
    with TestClient(app) as client:
        resp = client.get("/api/board/insights")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dictation"]["words"] == 200
    assert [c["category"] for c in body["categories"]["categories"]][:1] == ["agents"]


def test_route_is_503_without_insights() -> None:
    app = FastAPI()
    app.include_router(board_router)
    with TestClient(app) as client:
        assert client.get("/api/board/insights").status_code == 503
