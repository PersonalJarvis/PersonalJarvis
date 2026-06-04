"""Contract-Test für das WebSocket-Wire-Schema.

Sichert ab, dass der WSMessageIn/WSCommand-Discriminator im Backend dasselbe
Format versteht, das das Frontend über `src/schema/ws.ts` sendet. Der Test
nutzt realistische JSON-Payloads wie sie aus `useWebSocket.ts` kommen würden.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from jarvis.ui.web.schema import WSCommand, WSMessageIn, WSWelcome


def test_message_in_text() -> None:
    raw = {"type": "message", "kind": "text", "content": "hallo welt"}
    msg = WSMessageIn.model_validate(raw)
    assert msg.kind == "text"
    assert msg.content == "hallo welt"
    assert msg.metadata is None


def test_message_in_with_metadata() -> None:
    raw = {
        "type": "message",
        "kind": "text",
        "content": "hallo",
        "metadata": {"thread_id": "abc-123"},
    }
    msg = WSMessageIn.model_validate(raw)
    assert (msg.metadata or {})["thread_id"] == "abc-123"


def test_message_in_invalid_kind_rejected() -> None:
    raw = {"type": "message", "kind": "keyboard", "content": "x"}
    with pytest.raises(ValidationError):
        WSMessageIn.model_validate(raw)


def test_command_ping() -> None:
    raw = {"type": "command", "action": "ping", "payload": {"ts": 123}}
    cmd = WSCommand.model_validate(raw)
    assert cmd.action == "ping"
    assert cmd.payload == {"ts": 123}


def test_command_unknown_action_rejected() -> None:
    raw = {"type": "command", "action": "drop_database", "payload": {}}
    with pytest.raises(ValidationError):
        WSCommand.model_validate(raw)


def test_welcome_serializable() -> None:
    w = WSWelcome(session_id="s1", version="0.1.0", token=None)
    encoded = json.loads(w.model_dump_json())
    assert encoded["type"] == "welcome"
    assert encoded["session_id"] == "s1"
    assert encoded["version"] == "0.1.0"
