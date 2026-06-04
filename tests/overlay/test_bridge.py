"""OverlayBridge — Singleton + emit_methods + Backpressure."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.overlay.bridge import OverlayBridge, _BoundedOutbox
from jarvis.overlay.schema import (
    ActionEndedEnvelope,
    ActionStartedEnvelope,
    ClickEnvelope,
    ErrorEnvelope,
    StateEnvelope,
)


# -------------------------------------------------------------------------
# Bounded-Outbox Drop-Policy
# -------------------------------------------------------------------------


def test_outbox_drops_oldest_non_state_when_full() -> None:
    box = _BoundedOutbox(maxsize=3)
    box.put("click", b"c1")
    box.put("click", b"c2")
    box.put("click", b"c3")
    # Voll. Naechster click: c1 wird gedroppt.
    box.put("click", b"c4")
    assert box.dropped == 1
    assert len(box) == 3


def test_outbox_state_messages_are_protected() -> None:
    box = _BoundedOutbox(maxsize=3)
    box.put("state", b"s1")
    box.put("click", b"c1")
    box.put("click", b"c2")
    box.put("click", b"c3")
    # Voll. Click drueckt rein -> aelteste non-state (c1) raus.
    # state s1 BLEIBT.
    assert box.dropped == 1
    assert len(box) == 3


@pytest.mark.asyncio
async def test_outbox_get_blocks_until_item() -> None:
    box = _BoundedOutbox()
    fetched = []

    async def consumer() -> None:
        item = await box.get()
        fetched.append(item)

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    assert fetched == []
    box.put("click", b"hello")
    await task
    assert fetched == [b"hello"]


# -------------------------------------------------------------------------
# emit_state / emit_action_started / emit_action_ended / emit_click /
# emit_error — alle produzieren korrekt schema'd Envelopes auf der Queue.
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_state_produces_state_envelope() -> None:
    bridge = OverlayBridge()
    await bridge.start()
    try:
        bridge.emit_state("listening", reason="wakeword")
        raw = await bridge.outbox.get()
        env = StateEnvelope.model_validate_json(raw)
        assert env.payload.state == "listening"
        assert env.payload.reason == "wakeword"
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_emit_action_started_returns_action_id() -> None:
    bridge = OverlayBridge()
    await bridge.start()
    try:
        action_id = bridge.emit_action_started("click", duration_hint_ms=100)
        assert isinstance(action_id, str)
        assert len(action_id) > 0
        raw = await bridge.outbox.get()
        env = ActionStartedEnvelope.model_validate_json(raw)
        assert env.payload.kind == "click"
        assert env.payload.action_id == action_id
        assert env.payload.duration_hint_ms == 100
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_emit_action_ended_with_action_id() -> None:
    bridge = OverlayBridge()
    await bridge.start()
    try:
        bridge.emit_action_ended(
            "01HZX000000000000000000000",
            succeeded=False,
            duration_actual_ms=42,
        )
        raw = await bridge.outbox.get()
        env = ActionEndedEnvelope.model_validate_json(raw)
        assert env.payload.action_id == "01HZX000000000000000000000"
        assert env.payload.succeeded is False
        assert env.payload.duration_actual_ms == 42
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_emit_click_produces_click_envelope() -> None:
    bridge = OverlayBridge()
    await bridge.start()
    try:
        bridge.emit_click(100, 200, monitor="0", button="right")
        raw = await bridge.outbox.get()
        env = ClickEnvelope.model_validate_json(raw)
        assert env.payload.x == 100
        assert env.payload.y == 200
        assert env.payload.monitor == "0"
        assert env.payload.button == "right"
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_emit_error_produces_error_envelope() -> None:
    bridge = OverlayBridge()
    await bridge.start()
    try:
        bridge.emit_error(
            "TimeoutError: pyautogui hung",
            recoverable=True,
            context={"action_id": "abc"},
        )
        raw = await bridge.outbox.get()
        env = ErrorEnvelope.model_validate_json(raw)
        assert env.payload.message == "TimeoutError: pyautogui hung"
        assert env.payload.recoverable is True
        assert env.payload.context == {"action_id": "abc"}
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_state_emit_caches_last_state_envelope() -> None:
    """§10.5 Resync — last state envelope wird beim Reconnect zuerst gesendet."""
    bridge = OverlayBridge()
    await bridge.start()
    try:
        bridge.emit_state("typing")
        await bridge.outbox.get()  # consume
        last = bridge.last_state_envelope
        assert last is not None
        assert last.payload.state == "typing"
    finally:
        await bridge.stop()
