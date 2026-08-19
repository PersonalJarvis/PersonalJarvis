"""BUG-157: an in-flight native play is not a stalled realtime turn."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.realtime.protocol import RealtimeEvent
from tests.unit.realtime.test_session import (
    FakeBrain,
    FakeToolBridge,
    ScriptedTextTurnsProvider,
    _session,
)


@pytest.mark.asyncio
async def test_in_flight_native_tool_is_not_a_stalled_turn(monkeypatch):
    """Live 2026-08-19 18:17 session 128fbac6: youtube_music play was still
    running when the 20 s stall watchdog closed the turn and spoke
    "that took too long". The live model is blocked on the function call
    (ADR-0034), so provider silence during execute is work, not a wedge."""
    import jarvis.realtime.session as session_module

    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.2)
    monkeypatch.setattr(session_module, "_INSTANT_ACK_GRACE_S", 5.0)

    class _SlowBridge(FakeToolBridge):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(self, *, wire_name, arguments):
            self.calls.append((wire_name, arguments))
            self.started.set()
            await self.release.wait()
            return "youtube_music", {
                "success": True,
                "output": "playing",
                "error": None,
            }

    user_text = (
        "Kannst du bitte für mich ein richtig geiles Lied abspielen?"  # i18n-allow: live 18:17
    )
    jsons: list[dict] = []
    bridge = _SlowBridge()
    # Same native-call release shape as the BUG-154 in-flight guard: the
    # empty-turn re-ask is what delivers the tool_call on this fake
    # transport. The stall watchdog is what 18:17 actually tripped.
    provider = ScriptedTextTurnsProvider(
        [
            RealtimeEvent(type="input_transcript", text=user_text, is_final=True),
            RealtimeEvent(type="turn_complete"),
        ],
        [
            RealtimeEvent(
                type="tool_call",
                call_id="call-yt-stall",
                tool_name="youtube_music",
                tool_args={"action": "play", "query": "aktuelle Hits 2026"},
            ),
        ],
    )
    sess = _session(
        provider,
        brain=FakeBrain(replies=("never spoken",)),
        tool_bridge=bridge,
        tool_mode="hybrid",
        jsons=jsons,
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(bridge.started.wait(), timeout=8)
    # Shrink the stall window only after execute is live, so session
    # startup (first-import planning) cannot close the turn first.
    monkeypatch.setattr(session_module, "_TURN_STALL_TIMEOUT_S", 0.15)
    monkeypatch.setattr(session_module, "_TURN_STALL_POLL_S", 0.02)
    await asyncio.sleep(0.5)
    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert spoken == [], "in-flight youtube_music must not trip the stall watchdog"
    assert sess._native_tools_in_flight == 1
    assert sess._turn_id, "the play turn must stay open while the tool runs"
    bridge.release.set()
    await sess.end(reason="test")
