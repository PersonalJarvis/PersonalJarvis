"""Tests fuer NDJSON-Stream-Consumer (Claude + Codex).

Deckt:
- Roundtrip jedes Pydantic-Models gegen seine NDJSON-Repraesentation.
- Edge-Case: leere Zeile, Whitespace-only, ungueltiges JSON, JSON-Array statt
  Object, unbekannter Top-Level-`type`, unbekannter `system.subtype`.
- `read_ndjson_stream`: liest mehrere Events, ueberspringt Garbage, schreibt
  Tee-Datei korrekt, beendet bei EOF.

Style: pytest-asyncio (Project-Default in pyproject.toml).
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

from jarvis.missions.workers.stream_consumer import (
    ClaudeApiRetry,
    ClaudeAssistantMessage,
    ClaudeResult,
    ClaudeStreamDelta,
    ClaudeSystemInit,
    ClaudeUserMessage,
    CodexError,
    CodexItem,
    CodexThreadStarted,
    CodexTurnCompleted,
    CodexTurnFailed,
    CodexTurnStarted,
    parse_claude_stream_json,
    parse_codex_stream_json,
    read_ndjson_stream,
)


# ---------------------------------------------------------------------------
# Claude-Parser-Tests
# ---------------------------------------------------------------------------


class TestParseClaudeStreamJson:
    def test_system_init(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-abc",
                "model": "claude-sonnet-4-6",
                "tools": ["Read", "Edit", "Bash"],
                "cwd": "/tmp/work",
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeSystemInit)
        assert ev.session_id == "sess-abc"
        assert ev.model == "claude-sonnet-4-6"
        assert ev.tools == ["Read", "Edit", "Bash"]

    def test_system_api_retry(self) -> None:
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "retry_delay_ms": 5000,
                "attempt": 2,
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeApiRetry)
        assert ev.retry_delay_ms == 5000
        assert ev.attempt == 2

    def test_assistant_message(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
                "session_id": "sess-1",
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeAssistantMessage)
        assert ev.message["role"] == "assistant"

    def test_user_message_with_tool_result(self) -> None:
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
                },
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeUserMessage)
        assert ev.message["content"][0]["type"] == "tool_result"

    def test_stream_event_token_delta(self) -> None:
        line = json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_delta", "delta": {"text": "He"}},
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeStreamDelta)
        assert ev.event["type"] == "content_block_delta"

    def test_result_success(self) -> None:
        line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "cost_usd": 0.0123,
                "num_turns": 4,
                "session_id": "sess-1",
                "duration_ms": 12345,
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeResult)
        assert ev.is_error is False
        assert ev.cost_usd == 0.0123
        assert ev.num_turns == 4
        assert ev.subtype == "success"

    def test_result_max_turns_error(self) -> None:
        line = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "cost_usd": 0.05,
                "num_turns": 20,
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeResult)
        assert ev.is_error is True
        assert ev.subtype == "error_max_turns"

    # --- Edge cases ---

    def test_empty_line_returns_none(self) -> None:
        assert parse_claude_stream_json("") is None
        assert parse_claude_stream_json("\n") is None
        assert parse_claude_stream_json("   ") is None

    def test_invalid_json_returns_none(self) -> None:
        assert parse_claude_stream_json("not json {") is None
        assert parse_claude_stream_json("{broken") is None

    def test_json_array_returns_none(self) -> None:
        # Top-level Array statt Object — wir akzeptieren nur Objects.
        assert parse_claude_stream_json("[]") is None
        assert parse_claude_stream_json("[1,2,3]") is None

    def test_unknown_top_type_returns_none(self) -> None:
        line = json.dumps({"type": "this_is_not_real", "foo": 1})
        assert parse_claude_stream_json(line) is None

    def test_unknown_system_subtype_returns_none(self) -> None:
        line = json.dumps({"type": "system", "subtype": "future_thing", "data": 1})
        assert parse_claude_stream_json(line) is None

    def test_extra_fields_are_ignored(self) -> None:
        # CLI fuegt neue Felder hinzu — wir crashen NICHT.
        line = json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "x",
                "tools": [],
                "future_field_we_dont_know_yet": {"deep": True},
            }
        )
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeSystemInit)
        assert ev.session_id == "x"

    def test_partial_line_with_no_newline(self) -> None:
        # Partial-Line (kein Newline am Ende) wird trotzdem geparst.
        line = '{"type":"assistant","message":{"role":"assistant"}}'
        ev = parse_claude_stream_json(line)
        assert isinstance(ev, ClaudeAssistantMessage)


# ---------------------------------------------------------------------------
# Codex-Parser-Tests
# ---------------------------------------------------------------------------


class TestParseCodexStreamJson:
    def test_thread_started(self) -> None:
        line = json.dumps({"type": "thread.started", "thread_id": "th-1"})
        ev = parse_codex_stream_json(line)
        assert isinstance(ev, CodexThreadStarted)
        assert ev.thread_id == "th-1"

    def test_turn_started(self) -> None:
        ev = parse_codex_stream_json(json.dumps({"type": "turn.started", "turn_id": "t1"}))
        assert isinstance(ev, CodexTurnStarted)
        assert ev.turn_id == "t1"

    def test_turn_completed_with_cost(self) -> None:
        line = json.dumps(
            {
                "type": "turn.completed",
                "turn_id": "t1",
                "cost_usd": 0.02,
                "tokens_used": 1234,
            }
        )
        ev = parse_codex_stream_json(line)
        assert isinstance(ev, CodexTurnCompleted)
        assert ev.cost_usd == 0.02
        assert ev.tokens_used == 1234

    def test_turn_failed(self) -> None:
        ev = parse_codex_stream_json(
            json.dumps({"type": "turn.failed", "turn_id": "t1", "error": "boom"})
        )
        assert isinstance(ev, CodexTurnFailed)
        assert ev.error == "boom"

    def test_error_event(self) -> None:
        ev = parse_codex_stream_json(
            json.dumps({"type": "error", "message": "bad", "code": "E_BAD"})
        )
        assert isinstance(ev, CodexError)
        assert ev.message == "bad"
        assert ev.code == "E_BAD"

    def test_item_created_packs_payload(self) -> None:
        line = json.dumps(
            {
                "type": "item.created",
                "id": "i1",
                "kind": "tool_use",
                "name": "shell",
            }
        )
        ev = parse_codex_stream_json(line)
        assert isinstance(ev, CodexItem)
        # type wird normalisiert (item.created ist in der Literal-Union).
        assert ev.type == "item.created"
        assert ev.payload["id"] == "i1"
        assert ev.payload["kind"] == "tool_use"
        # `type`-Feld selbst landet NICHT im payload (wir filtern es heraus).
        assert "type" not in ev.payload

    def test_item_unknown_subtype_falls_back_to_generic_item(self) -> None:
        # Schema-Drift: neuer item.foo-Typ den unsere Literal-Union nicht kennt.
        line = json.dumps({"type": "item.brand_new", "data": 42})
        ev = parse_codex_stream_json(line)
        assert isinstance(ev, CodexItem)
        # Faellt zurueck auf 'item' weil 'item.brand_new' nicht in der Literal-Union steht.
        assert ev.type == "item"
        assert ev.payload["data"] == 42

    # --- Edge cases ---

    def test_empty_line(self) -> None:
        assert parse_codex_stream_json("") is None
        assert parse_codex_stream_json("\n") is None

    def test_invalid_json(self) -> None:
        assert parse_codex_stream_json("garbage{") is None

    def test_unknown_top_type(self) -> None:
        # Nicht 'item.*', nicht in der bekannten Liste -> None.
        assert parse_codex_stream_json(json.dumps({"type": "wat", "x": 1})) is None

    def test_extra_fields_ignored(self) -> None:
        line = json.dumps(
            {
                "type": "thread.started",
                "thread_id": "x",
                "future_unknown_field": True,
            }
        )
        ev = parse_codex_stream_json(line)
        assert isinstance(ev, CodexThreadStarted)


# ---------------------------------------------------------------------------
# read_ndjson_stream-Tests
# ---------------------------------------------------------------------------


def _make_reader(payload: bytes) -> asyncio.StreamReader:
    """Baut einen asyncio.StreamReader mit fertigem Datenpuffer."""
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_read_ndjson_stream_yields_parsed_events() -> None:
    payload = (
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "tools": []})
        + "\n"
        + json.dumps({"type": "assistant", "message": {"role": "assistant"}})
        + "\n"
        + json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "cost_usd": 0.01,
                "num_turns": 1,
            }
        )
        + "\n"
    ).encode("utf-8")

    reader = _make_reader(payload)
    events = [
        ev async for ev in read_ndjson_stream(reader, parser=parse_claude_stream_json)
    ]
    assert len(events) == 3
    assert isinstance(events[0], ClaudeSystemInit)
    assert isinstance(events[1], ClaudeAssistantMessage)
    assert isinstance(events[2], ClaudeResult)


@pytest.mark.asyncio
async def test_read_ndjson_stream_skips_invalid_lines() -> None:
    payload = (
        b"not json at all\n"
        + json.dumps({"type": "system", "subtype": "init", "tools": []}).encode("utf-8")
        + b"\n"
        + b"\n"  # leere Zeile
        + b"another garbage\n"
        + json.dumps(
            {"type": "result", "subtype": "success", "is_error": False}
        ).encode("utf-8")
        + b"\n"
    )
    reader = _make_reader(payload)
    events = [
        ev async for ev in read_ndjson_stream(reader, parser=parse_claude_stream_json)
    ]
    assert len(events) == 2
    assert isinstance(events[0], ClaudeSystemInit)
    assert isinstance(events[1], ClaudeResult)


@pytest.mark.asyncio
async def test_read_ndjson_stream_writes_tee_file(tmp_path: Path) -> None:
    payload = (
        json.dumps({"type": "system", "subtype": "init", "tools": []}).encode("utf-8")
        + b"\n"
        + json.dumps({"type": "result", "subtype": "success", "is_error": False}).encode(
            "utf-8"
        )
        + b"\n"
    )
    reader = _make_reader(payload)
    tee_path = tmp_path / "logs" / "stream.jsonl"

    events = []
    async for ev in read_ndjson_stream(
        reader, parser=parse_claude_stream_json, tee_path=tee_path
    ):
        events.append(ev)

    assert len(events) == 2
    assert tee_path.exists()
    written = tee_path.read_bytes()
    # Wir schreiben jede Roh-Zeile inkl. Newline.
    assert written.count(b"\n") == 2
    assert b'"type": "system"' in written or b'"type":"system"' in written


@pytest.mark.asyncio
async def test_read_ndjson_stream_eof_immediately() -> None:
    reader = _make_reader(b"")
    events = [
        ev async for ev in read_ndjson_stream(reader, parser=parse_claude_stream_json)
    ]
    assert events == []


@pytest.mark.asyncio
async def test_read_ndjson_stream_codex_parser_works() -> None:
    payload = (
        json.dumps({"type": "thread.started", "thread_id": "th-1"}).encode("utf-8")
        + b"\n"
        + json.dumps(
            {"type": "turn.completed", "turn_id": "t1", "cost_usd": 0.01}
        ).encode("utf-8")
        + b"\n"
    )
    reader = _make_reader(payload)
    events = [
        ev async for ev in read_ndjson_stream(reader, parser=parse_codex_stream_json)
    ]
    assert len(events) == 2
    assert isinstance(events[0], CodexThreadStarted)
    assert isinstance(events[1], CodexTurnCompleted)


# Suppress the unused-import linter warning for io (which we may use later).
_ = io
