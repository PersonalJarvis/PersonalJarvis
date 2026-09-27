"""Exact durable terminal lookups for Mars cancellation/result reconciliation."""

from __future__ import annotations

import pytest

from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.store import AgentChatStore


class NoHistoryScanStore(AgentChatStore):
    """The exact lookup must not load a chat's private history into the caller."""

    def list_events(self, session_id, *, after_seq=0):
        raise AssertionError("turn_terminal must not load complete session history")


@pytest.fixture
def store():
    store = NoHistoryScanStore()
    for session in ("society:one", "society:two"):
        store.create_session(
            provider="openai",
            model="",
            effort="",
            cwd=".",
            session_id=session,
            surface="society",
        )
    try:
        yield store
    finally:
        store.close()


def _terminal(store, session, turn, status):
    return store.append_event(
        session, make_event("turn_finished", {"turn_id": turn, "status": status})
    )


def test_terminal_lookup_is_exactly_session_and_turn_scoped(store):
    wanted = _terminal(store, "society:one", "shared-turn", "done")
    _terminal(store, "society:two", "shared-turn", "cancelled")
    _terminal(store, "society:one", "newer-turn", "error")
    actual = store.turn_terminal("society:one", "shared-turn")
    assert actual["payload"] == {"turn_id": "shared-turn", "status": "done"}
    assert actual["seq"] == wanted["seq"]
    assert store.turn_terminal("society:two", "shared-turn")["payload"]["status"] == "cancelled"
    assert store.turn_terminal("society:missing", "shared-turn") is None
    assert store.turn_terminal("society:one", "missing-turn") is None


def test_latest_durable_terminal_wins_without_reading_other_event_kinds(store):
    _terminal(store, "society:one", "target", "done")
    latest = _terminal(store, "society:one", "target", "cancelled")
    store.append_event(
        "society:one",
        make_event(
            "assistant_text",
            {"turn_id": "target", "text": "Not a terminal receipt."},
        ),
    )
    record = store.turn_terminal("society:one", "target")
    assert record["seq"] == latest["seq"]
    assert record["kind"] == "turn_finished"
    assert record["payload"]["status"] == "cancelled"


def test_turn_reference_is_a_bound_sql_value(store):
    _terminal(store, "society:one", "actual-turn", "done")
    assert store.turn_terminal("society:one", "' OR 1=1 --") is None
    assert store.turn_terminal("society:one' OR 1=1 --", "actual-turn") is None


def test_terminal_lookup_survives_connection_and_process_style_reopen(tmp_path):
    path = tmp_path / "chat.db"
    original = NoHistoryScanStore(path)
    original.create_session(
        provider="openai",
        model="",
        effort="",
        cwd=".",
        session_id="society:one",
        surface="society",
    )
    saved = _terminal(original, "society:one", "durable-turn", "done")
    original.close()
    reopened = NoHistoryScanStore(path)
    try:
        result = reopened.turn_terminal("society:one", "durable-turn")
        assert result["seq"] == saved["seq"]
        assert result["payload"] == saved["payload"]
    finally:
        reopened.close()
