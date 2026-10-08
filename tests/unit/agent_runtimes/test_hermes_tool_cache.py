"""Existing native conversations adopt tool discovery without losing history."""

import json
import sqlite3

import pytest

from jarvis.agent_runtimes.tool_snapshot import refresh_tool_search_cache


@pytest.mark.parametrize("hashed", [False, True])
def test_only_the_selected_eager_cache_is_invalidated(tmp_path, hashed):
    snapshot = json.dumps({"tools": [
        {"function": {"name": "mcp__jarvis__society_ask_user"}},
        {"function": {"name": "tool_search"}},
    ]}) if hashed else json.dumps(["mcp__jarvis__society_ask_user", "tool_search"])
    pin = "snapshot-hash" if hashed else snapshot
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, tool_names TEXT, system_prompt TEXT);
            CREATE TABLE system_prompts (hash TEXT PRIMARY KEY, prompt TEXT);
            CREATE TABLE messages (session_id TEXT, content TEXT);
        """)
        db.executemany("INSERT INTO sessions VALUES (?, ?, ?)", [
            ("selected", pin, "Keep my identity"), ("other", pin, "Other identity"),
        ])
        db.execute("INSERT INTO system_prompts VALUES (?, ?)", ("snapshot-hash", snapshot))
        db.execute("INSERT INTO messages VALUES (?, ?)", ("selected", "Completed action"))
    assert refresh_tool_search_cache(tmp_path, "selected")
    assert not refresh_tool_search_cache(tmp_path, "selected")
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT * FROM sessions ORDER BY id").fetchall() == [
            ("other", pin, "Other identity"), ("selected", None, "Keep my identity"),
        ]
        assert db.execute("SELECT * FROM messages").fetchall() == [("selected", "Completed action")]
        assert db.execute("SELECT prompt FROM system_prompts").fetchone() == (snapshot,)
        compact = json.dumps(["terminal", "tool_search", "tool_call", "tool_describe"])
        db.execute("UPDATE sessions SET tool_names = ? WHERE id = 'selected'", (compact,))
    assert not refresh_tool_search_cache(tmp_path, "selected")


def test_missing_or_unfamiliar_native_store_is_retained(tmp_path, caplog):
    assert not refresh_tool_search_cache(tmp_path, "missing")
    assert not (tmp_path / "state.db").exists()
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("CREATE TABLE future_native_layout (value TEXT)")
    assert not refresh_tool_search_cache(tmp_path, "unknown")
    assert "retaining it" in caplog.text
