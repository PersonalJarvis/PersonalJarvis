"""The reasoning trace of a turn survives the turn (jarvis/state/turn_trace.py).

A trace is the event list the UI replays into "Thought for 4s" + steps. The
collector keeps the bus's trace-relevant events in a ring, hands a turn its
slice, and redacts what it stores; ``ChatStore`` keeps it next to the reply
and migrates an older ``chats.db`` that has no column for it.
"""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionExecuted,
    ActionProposed,
    BrainTurnCompleted,
    BrainTurnStarted,
    HotkeyPressed,
    MessageSent,
)
from jarvis.state.chat_store import ChatStore
from jarvis.state.turn_trace import (
    TRACE_EVENT_KINDS,
    TurnTraceCollector,
    trace_from_events,
    trace_payload_for,
)


async def test_collector_keeps_only_trace_events_in_order() -> None:
    bus = EventBus()
    collector = TurnTraceCollector(bus)
    t0 = 1_700_000_000_000
    await bus.publish(HotkeyPressed(timestamp_ns=(t0 + 1) * 1_000_000))
    await bus.publish(
        ActionProposed(
            timestamp_ns=(t0 + 2) * 1_000_000,
            tool_name="wiki-recall",
            args={"query": "Urlaub"},
            rationale="Ich schaue im Wiki nach.",
        )
    )
    await bus.publish(
        ActionExecuted(
            timestamp_ns=(t0 + 5) * 1_000_000,
            tool_name="wiki-recall",
            success=True,
            duration_ms=3,
            output_preview="2 pages",
        )
    )
    await bus.publish(MessageSent(timestamp_ns=(t0 + 6) * 1_000_000, role="assistant", text="x"))

    trace = collector.snapshot(t0)
    assert trace is not None
    assert [e["name"] for e in trace["events"]] == ["ActionProposed", "ActionExecuted"]
    assert trace["events"][0]["payload"]["args"] == {"query": "Urlaub"}
    assert trace["events"][0]["payload"]["rationale"] == "Ich schaue im Wiki nach."
    assert trace["events"][1]["payload"]["output_preview"] == "2 pages"
    assert trace["started_ms"] == t0
    assert trace["ended_ms"] == t0 + 5


async def test_snapshot_is_none_for_a_quiet_window() -> None:
    collector = TurnTraceCollector(EventBus())
    assert collector.snapshot(0) is None


def test_slice_respects_the_window() -> None:
    collector = TurnTraceCollector()
    for ms in (10, 20, 30):
        collector.record(
            "BrainTurnStarted",
            BrainTurnStarted(timestamp_ns=ms * 1_000_000, provider="p", model="m"),
        )
    assert [e["ts_ms"] for e in collector.slice(15, 25)] == [20]
    assert [e["ts_ms"] for e in collector.slice(20)] == [20, 30]


def test_payload_is_redacted_and_capped() -> None:
    secret = "sk-ant-api03-" + "A" * 40
    payload = trace_payload_for(
        "ActionProposed",
        ActionProposed(
            tool_name="run_shell",
            args={
                "command": f"export KEY={secret}",
                "long": "x" * 500,
                **{f"k{i}": i for i in range(20)},
            },
            rationale="r" * 1000,
        ),
    )
    assert secret not in payload["args"]["command"]
    assert len(payload["args"]["long"]) < 200
    assert len(payload["args"]) <= 8
    assert len(payload["rationale"]) < 300
    # Numbers stay numbers, so the UI can format durations.
    done = trace_payload_for(
        "ActionExecuted", ActionExecuted(tool_name="x", success=False, duration_ms=42)
    )
    assert done["duration_ms"] == 42 and done["success"] is False


def test_trace_from_recorded_voice_rows_matches_collector_shape() -> None:
    rows = [
        {"kind": "TranscriptFinal", "ts_ms": 100, "payload": {}},
        {
            "kind": "ActionProposed",
            "ts_ms": 120,
            "payload": {"tool_name": "search_web", "args": {"query": "Wetter"}},
        },
        {
            "kind": "ActionExecuted",
            "ts_ms": 900,
            "payload": {"tool_name": "search_web", "success": True},
        },
        {"kind": "ActionExecuted", "ts_ms": 5000, "payload": {"tool_name": "later"}},
    ]
    trace = trace_from_events(rows, since_ms=100, until_ms=1000)
    assert trace is not None
    assert [e["name"] for e in trace["events"]] == ["ActionProposed", "ActionExecuted"]
    assert trace["events"][0]["payload"]["args"] == {"query": "Wetter"}
    assert trace_from_events([], since_ms=0) is None


def test_every_trace_kind_has_a_payload_whitelist() -> None:
    from jarvis.state.turn_trace import _PAYLOAD_KEYS

    assert TRACE_EVENT_KINDS == frozenset(_PAYLOAD_KEYS)


async def test_chat_store_round_trips_the_trace(tmp_path) -> None:
    path = str(tmp_path / "chats.db")
    trace = {
        "started_ms": 1,
        "ended_ms": 9,
        "events": [{"name": "BrainTurnCompleted", "ts_ms": 9, "payload": {"model": "m"}}],
    }
    s1 = ChatStore(bus=EventBus(), db_path=path)
    await s1.add_message(thread_id="t", role="user", text="hi")
    await s1.add_message(thread_id="t", role="assistant", text="hello", trace=trace)

    s2 = ChatStore(bus=EventBus(), db_path=path)
    msgs = s2.get_thread("t")["messages"]
    assert msgs[0]["trace"] is None
    assert msgs[1]["trace"] == trace


async def test_chat_store_migrates_a_db_without_the_trace_column(tmp_path) -> None:
    """An existing chats.db from before traces opens and stores traces."""
    path = tmp_path / "chats.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chat_threads (
            thread_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'text',
            created_at_ns INTEGER NOT NULL, updated_at_ns INTEGER NOT NULL);
        CREATE TABLE chat_messages (
            message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, role TEXT NOT NULL,
            text TEXT NOT NULL, timestamp_ns INTEGER NOT NULL);
        INSERT INTO chat_threads VALUES ('old', 'Old', 'text', 1, 1);
        INSERT INTO chat_messages VALUES ('m1', 'old', 'assistant', 'legacy', 1);
        """
    )
    conn.commit()
    conn.close()

    store = ChatStore(bus=EventBus(), db_path=str(path))
    legacy = store.get_thread("old")["messages"]
    assert legacy[0]["trace"] is None
    await store.add_message(thread_id="old", role="assistant", text="new", trace={"events": []})
    # An empty trace is stored as nothing — there is no trace to show.
    assert store.get_thread("old")["messages"][-1]["trace"] is None
    await store.add_message(
        thread_id="old",
        role="assistant",
        text="traced",
        trace={"events": [{"name": "BrainTurnStarted", "ts_ms": 1, "payload": {}}]},
    )
    assert (
        store.get_thread("old")["messages"][-1]["trace"]["events"][0]["name"] == "BrainTurnStarted"
    )


async def test_chat_store_drops_an_unserialisable_trace_but_keeps_the_message() -> None:
    store = ChatStore(bus=EventBus())
    bad = {"events": [{"name": "x", "ts_ms": 1, "payload": {"u": uuid4()}}]}
    msg = await store.add_message(thread_id="t", role="assistant", text="ok", trace=bad)
    assert msg.text == "ok"
    stored = store.get_thread("t")["messages"][-1]["trace"]
    # ``default=str`` makes the UUID a string rather than losing the trace.
    assert stored is not None and isinstance(stored["events"][0]["payload"]["u"], str)


async def test_completed_turn_is_in_the_collector_too() -> None:
    bus = EventBus()
    collector = TurnTraceCollector(bus)
    await bus.publish(
        BrainTurnCompleted(provider="anthropic", model="claude", timestamp_ns=5_000_000)
    )
    trace = collector.snapshot(0)
    assert trace is not None
    assert trace["events"][0]["payload"] == {
        "provider": "anthropic",
        "model": "claude",
        "finish_reason": "",
        "tokens_in": 0,
        "tokens_out": 0,
    }
