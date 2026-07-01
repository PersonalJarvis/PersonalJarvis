"""Sub-agent no-op path. Plan §8.7 + AD-6.

When the ``JARVIS_DEPTH > 0`` env var is set, the OverlayBridge is a no-op
stub. Caller code sees the same interface, but events are not sent
anywhere.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.overlay import (
    NoOpOverlayBridge,
    get_overlay,
    is_sub_agent_process,
    start_overlay,
    stop_overlay,
)


# -------------------------------------------------------------------------
# is_sub_agent_process Detection
# -------------------------------------------------------------------------


def test_no_jarvis_depth_is_main_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_DEPTH", raising=False)
    assert is_sub_agent_process() is False


def test_jarvis_depth_zero_is_main_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_DEPTH", "0")
    assert is_sub_agent_process() is False


@pytest.mark.parametrize("depth", ["1", "2", "5"])
def test_jarvis_depth_positive_is_sub_agent(
    depth: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_DEPTH", depth)
    assert is_sub_agent_process() is True


def test_invalid_jarvis_depth_falls_back_to_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_DEPTH", "garbage")
    assert is_sub_agent_process() is False


# -------------------------------------------------------------------------
# get_overlay liefert NoOp-Stub in Sub-Agents
# -------------------------------------------------------------------------


def test_get_overlay_returns_noop_stub_in_sub_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_DEPTH", "2")
    bridge = get_overlay()
    assert bridge is not None
    assert isinstance(bridge, NoOpOverlayBridge)


# -------------------------------------------------------------------------
# NoOpOverlayBridge — all emit methods are no-ops, no IPC traffic
# -------------------------------------------------------------------------


def test_noop_emit_state_returns_false() -> None:
    bridge = NoOpOverlayBridge()
    assert bridge.emit_state("listening") is False


def test_noop_emit_action_started_returns_action_id() -> None:
    bridge = NoOpOverlayBridge()
    aid = bridge.emit_action_started("click", duration_hint_ms=100)
    assert isinstance(aid, str)


def test_noop_emit_action_ended_returns_false() -> None:
    bridge = NoOpOverlayBridge()
    assert bridge.emit_action_ended("noop") is False


def test_noop_emit_click_returns_false() -> None:
    bridge = NoOpOverlayBridge()
    assert bridge.emit_click(100, 200, button="left") is False


def test_noop_emit_error_returns_false() -> None:
    bridge = NoOpOverlayBridge()
    assert bridge.emit_error("test") is False


def test_noop_send_nowait_returns_false() -> None:
    bridge = NoOpOverlayBridge()
    assert bridge.send_nowait(object()) is False


def test_noop_dropped_count_is_zero() -> None:
    bridge = NoOpOverlayBridge()
    bridge.emit_state("listening")
    bridge.emit_state("typing")
    bridge.emit_state("idle")
    assert bridge.dropped == 0
    assert bridge.connected_count == 0


@pytest.mark.asyncio
async def test_noop_start_stop_lifecycle() -> None:
    bridge = NoOpOverlayBridge()
    await bridge.start()
    await bridge.stop()
    # No exception. No state.


# -------------------------------------------------------------------------
# start_overlay() im Sub-Agent
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_overlay_in_sub_agent_returns_noop_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_DEPTH", "2")
    try:
        bridge = await start_overlay()
        assert isinstance(bridge, NoOpOverlayBridge)
    finally:
        await stop_overlay()
        monkeypatch.delenv("JARVIS_DEPTH", raising=False)
