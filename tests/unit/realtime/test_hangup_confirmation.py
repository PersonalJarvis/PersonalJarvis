"""Call control stays separate from spoken tasks and their approvals."""

import asyncio

import pytest

from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from tests.unit.realtime.test_session import FakeProvider, FakeToolBridge, _cfg


@pytest.mark.parametrize("answer", ["No", "Yes, keep talking", "Explain the screen"])
async def test_unconfirmed_call_stays_open_even_if_model_calls_end_call(answer):
    provider = FakeProvider([
        RealtimeEvent(type="input_transcript", text="Hang up", is_final=True),
        RealtimeEvent(type="tool_call", call_id="end-1", tool_name="end_call"),
        RealtimeEvent(type="turn_complete"),
        RealtimeEvent(type="speech_started"),
        RealtimeEvent(type="input_transcript", text=answer, is_final=True),
        RealtimeEvent(type="tool_call", call_id="end-2", tool_name="end_call"),
        RealtimeEvent(type="turn_complete"),
        RealtimeEvent(type="speech_started"),
        RealtimeEvent(type="input_transcript", text="Yes", is_final=True),
        RealtimeEvent(type="tool_call", call_id="end-3", tool_name="end_call"),
        RealtimeEvent(type="turn_complete"),
    ])
    messages = []
    session = RealtimeVoiceSession(
        session_id="hangup-confirmation", provider=provider, config=_cfg(), bus=None,
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda msg: messages.append(msg) or asyncio.sleep(0),
        tool_bridge=FakeToolBridge(),
    )
    await session.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.wait_for(session.wait_finished(), 5)
    assert not session.hangup_reason
    assert not any(m.get("type") == "hangup" for m in messages)
    assert all(not result[2]["success"] for result in provider.session.tool_results)
    await session.end(reason="client_stop")


async def test_request_does_not_authorize_an_action_and_button_still_ends_immediately():
    bridge = FakeToolBridge()
    provider = FakeProvider([])
    messages = []
    session = RealtimeVoiceSession(
        session_id="hangup-button", provider=provider, config=_cfg(), bus=None,
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda msg: messages.append(msg) or asyncio.sleep(0), tool_bridge=bridge,
    )
    session._turn_id = "request"
    session._last_user_text = "Hang up"
    assert await session._handle_voice_hangup_input()
    assert not session.hangup_reason
    assert bridge.calls == []
    assert any(m.get("type") == "error_spoken" for m in messages)
    await session.handle_control({"type": "audio_stop"})
    assert session._ended


@pytest.mark.parametrize("text", [
    "Don't hang up", 'What does "hang up" mean?', "Please quit the editor",
])
async def test_closing_words_in_normal_speech_never_prompt(text):
    messages = []
    session = RealtimeVoiceSession(
        session_id="normal-speech", provider=FakeProvider([]), config=_cfg(), bus=None,
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda msg: messages.append(msg) or asyncio.sleep(0),
    )
    session._turn_id = "ordinary-turn"
    session._last_user_text = text
    assert not await session._handle_voice_hangup_input()
    assert messages == []
    await session.end(reason="client_stop")
