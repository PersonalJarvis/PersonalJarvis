"""Tests for sessions.db -> board aggregation.

The board previously read only flight-recorder JSONL, which is empty on most
installs, so every tile showed 0. The durable rich source is ``sessions.db``
(voice turns with user/Jarvis text + per-turn tool calls). These tests pin the
new ``BoardAggregator`` sessions source: word counts, usage categories, session
count, and conversation time, all per day, all derived only from the durable
store. Raw text is counted but never persisted (privacy invariant).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jarvis.board.aggregator import BoardAggregator
from jarvis.board.store import BoardStore


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _make_sessions_db(path: Path) -> tuple[str, str]:
    """Create a minimal sessions.db with two active days. Returns (dayA, dayB)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE voice_sessions (
            id TEXT PRIMARY KEY,
            started_ms INTEGER NOT NULL,
            ended_ms INTEGER,
            hangup_reason TEXT,
            turn_count INTEGER DEFAULT 0
        );
        CREATE TABLE voice_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            started_ms INTEGER NOT NULL,
            ended_ms INTEGER,
            user_text TEXT,
            jarvis_text TEXT,
            tool_calls_json TEXT
        );
        """
    )
    day_a = datetime.now().astimezone().replace(hour=11, minute=0, second=0, microsecond=0) - timedelta(days=2)
    day_b = day_a + timedelta(days=1)

    # Day A: two turns. user words 3 + 2 = 5, jarvis words 1 + 4 = 5.
    conn.executemany(
        "INSERT INTO voice_turns (session_id, started_ms, ended_ms, user_text, jarvis_text, tool_calls_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("s1", _ms(day_a), _ms(day_a + timedelta(seconds=10)),
             "hello there friend", "hi", '["spawn_openclaw", "wiki-recall"]'),
            ("s1", _ms(day_a + timedelta(minutes=1)), _ms(day_a + timedelta(minutes=1, seconds=5)),
             "open chrome", "opening the browser now", '["open_app", "click"]'),
            # Day B: one turn, user 2 words, jarvis 3 words, a contact tool.
            ("s2", _ms(day_b), _ms(day_b + timedelta(seconds=8)),
             "call mum", "calling her right away", '["call-contact"]'),
        ],
    )
    conn.executemany(
        "INSERT INTO voice_sessions (id, started_ms, ended_ms, turn_count) VALUES (?, ?, ?, ?)",
        [
            ("s1", _ms(day_a), _ms(day_a + timedelta(minutes=10)), 2),   # 600s
            ("s2", _ms(day_b), None, 1),                                  # still open
        ],
    )
    conn.commit()
    conn.close()
    return day_a.strftime("%Y-%m-%d"), day_b.strftime("%Y-%m-%d")


def test_sessions_word_counts_per_day(tmp_path: Path) -> None:
    sessions_db = tmp_path / "data" / "sessions.db"
    day_a, day_b = _make_sessions_db(sessions_db)
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",          # intentionally empty
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=sessions_db,
    )
    agg.run()

    row_a = agg.db.execute(
        "SELECT user_words_count, jarvis_words_count, session_count "
        "FROM daily_stats WHERE date = ?",
        (day_a,),
    ).fetchone()
    assert row_a is not None
    assert row_a["user_words_count"] == 5      # 3 + 2
    assert row_a["jarvis_words_count"] == 5    # 1 + 4
    assert row_a["session_count"] == 1

    row_b = agg.db.execute(
        "SELECT user_words_count, jarvis_words_count, session_count "
        "FROM daily_stats WHERE date = ?",
        (day_b,),
    ).fetchone()
    assert row_b["user_words_count"] == 2          # "call mum"
    assert row_b["jarvis_words_count"] == 4         # "calling her right away"
    assert row_b["session_count"] == 1


def test_sessions_category_counts(tmp_path: Path) -> None:
    sessions_db = tmp_path / "data" / "sessions.db"
    day_a, day_b = _make_sessions_db(sessions_db)
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=sessions_db,
    )
    agg.run()

    import json
    cats_a = json.loads(
        agg.db.execute(
            "SELECT category_counts FROM daily_stats WHERE date = ?", (day_a,)
        ).fetchone()["category_counts"]
    )
    # Day A tools: spawn_openclaw (agents; legacy pre-rename tool name),
    # wiki-recall(knowledge), open_app(browser), click(browser)
    assert cats_a.get("agents") == 1
    assert cats_a.get("knowledge") == 1
    assert cats_a.get("browser") == 2

    cats_b = json.loads(
        agg.db.execute(
            "SELECT category_counts FROM daily_stats WHERE date = ?", (day_b,)
        ).fetchone()["category_counts"]
    )
    assert cats_b.get("community") == 1


def test_sessions_conversation_seconds(tmp_path: Path) -> None:
    sessions_db = tmp_path / "data" / "sessions.db"
    day_a, _day_b = _make_sessions_db(sessions_db)
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=sessions_db,
    )
    agg.run()
    secs = agg.db.execute(
        "SELECT conversation_seconds_estimate FROM daily_stats WHERE date = ?", (day_a,)
    ).fetchone()["conversation_seconds_estimate"]
    assert secs == pytest.approx(600.0)


def test_store_summary_exposes_words_and_categories(tmp_path: Path) -> None:
    sessions_db = tmp_path / "data" / "sessions.db"
    _make_sessions_db(sessions_db)
    db_path = tmp_path / "board" / "personal.db"
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=db_path,
        sessions_db_path=sessions_db,
    )
    agg.run()
    agg.close()

    store = BoardStore(db_path=db_path)
    summary = store.summary(window_days=365)
    assert summary["totals"]["user_words"] == 7      # 5 + 2
    assert summary["totals"]["jarvis_words"] == 9     # 5 + 4
    assert summary["totals"]["session_count"] == 2

    categories = store.categories()
    by_key = {c["category"]: c["count"] for c in categories["categories"]}
    # all six keys present (zeros included), order stable
    assert [c["category"] for c in categories["categories"]] == [
        "agents", "browser", "mail", "community", "knowledge", "system",
    ]
    assert by_key["agents"] == 1
    assert by_key["browser"] == 2
    assert by_key["knowledge"] == 1
    assert by_key["community"] == 1
    assert categories["total"] == 5


def test_heatmap_carries_word_counts(tmp_path: Path) -> None:
    sessions_db = tmp_path / "data" / "sessions.db"
    day_a, _day_b = _make_sessions_db(sessions_db)
    db_path = tmp_path / "board" / "personal.db"
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=db_path,
        sessions_db_path=sessions_db,
    )
    agg.run()
    agg.close()

    store = BoardStore(db_path=db_path)
    heatmap = store.heatmap(days=365)
    cell = next(c for c in heatmap["cells"] if c["date"] == day_a)
    assert cell["user_words"] == 5
    assert cell["jarvis_words"] == 5
    # Empty days still expose the keys (zero), so the chart never breaks.
    assert all("user_words" in c and "jarvis_words" in c for c in heatmap["cells"])


def test_summary_reports_longest_streak(tmp_path: Path) -> None:
    sessions_db = tmp_path / "data" / "sessions.db"
    _make_sessions_db(sessions_db)  # two consecutive active days
    db_path = tmp_path / "board" / "personal.db"
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=db_path,
        sessions_db_path=sessions_db,
    )
    agg.run()
    agg.close()

    store = BoardStore(db_path=db_path)
    summary = store.summary(window_days=365)
    # day_a and day_b are consecutive -> longest run is 2.
    assert summary["longest_streak"] == 2


def _append_turn(path: Path, started_ms: int, user_text: str, jarvis_text: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO voice_turns (session_id, started_ms, ended_ms, user_text, "
        "jarvis_text, tool_calls_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", started_ms, started_ms + 5000, user_text, jarvis_text, "[]"),
    )
    conn.commit()
    conn.close()


def test_run_if_stale_picks_up_new_words(tmp_path: Path) -> None:
    sessions_db = tmp_path / "data" / "sessions.db"
    day_a, _ = _make_sessions_db(sessions_db)
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=sessions_db,
    )
    agg.run()
    before = agg.db.execute(
        "SELECT user_words_count FROM daily_stats WHERE date = ?", (day_a,)
    ).fetchone()["user_words_count"]
    assert before == 5

    # The user speaks four more words on day_a.
    day_a_dt = datetime.fromisoformat(day_a + "T13:00:00").astimezone()
    _append_turn(sessions_db, _ms(day_a_dt), "one two three four", "ok")

    # A fresh cache (huge TTL) must NOT re-run -> numbers stay frozen.
    assert agg.run_if_stale(ttl_s=10_000) is False
    frozen = agg.db.execute(
        "SELECT user_words_count FROM daily_stats WHERE date = ?", (day_a,)
    ).fetchone()["user_words_count"]
    assert frozen == 5

    # A stale cache (ttl 0) re-aggregates -> the new words appear.
    assert agg.run_if_stale(ttl_s=0) is True
    after = agg.db.execute(
        "SELECT user_words_count FROM daily_stats WHERE date = ?", (day_a,)
    ).fetchone()["user_words_count"]
    assert after == 9


def test_missing_sessions_db_is_safe(tmp_path: Path) -> None:
    agg = BoardAggregator(
        jsonl_dir=tmp_path / "flight_recorder",
        db_path=tmp_path / "board" / "personal.db",
        sessions_db_path=tmp_path / "data" / "does_not_exist.db",
    )
    agg.run()  # must not raise
    # Columns still exist even with no data.
    cols = {r["name"] for r in agg.db.execute("PRAGMA table_info(daily_stats)")}
    assert {"user_words_count", "jarvis_words_count", "session_count", "category_counts"} <= cols
