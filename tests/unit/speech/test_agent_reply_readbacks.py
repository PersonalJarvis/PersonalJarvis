"""Agent replies remain owed until audio finishes, across voice-call boundaries."""

import asyncio

import pytest

from jarvis.core.events import AnnouncementRequested
from jarvis.speech.pipeline import TurnTakingState
from tests.unit.speech.test_realtime_announcement_bridge import _pipeline
from tests.unit.speech.test_realtime_mode import _FakeRealtimeSession, _FakeTTS, _pipe, _SilentMic


def reply(name="Nala"):
    return AnnouncementRequested(
        source_layer="society.lead",
        kind="completion",
        language="en",
        text=f"{name} reports: The draft is ready.",
        detail=f"agent={name} trace=conversation",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["idle", "hangup", "muted", "connecting"])
async def test_agent_reply_waits_for_an_available_call(blocked):
    pipe, tts, player, realtime = _pipeline(accepted=True)
    event = reply()
    if blocked == "idle":
        pipe._turn_state = TurnTakingState.IDLE
    elif blocked == "hangup":
        pipe._hangup_event.set()
    elif blocked == "muted":
        pipe._muted = True
    else:
        pipe._voice_engine_transitioning = True
    await pipe._on_announcement(event)
    await pipe._on_announcement(event)
    assert pipe._deferred_announcements == [event]
    assert not realtime.calls and not tts.calls and not player.plays
    pipe._hangup_event.clear()
    pipe._muted = False
    pipe._voice_engine_transitioning = False
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.02)
    assert len(realtime.calls) == 1
    assert pipe._agent_reply_inflight == event


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Nala", "Scout", "Mail agent"])
async def test_interrupted_reply_retries_next_session_and_completed_reply_does_not(name):
    pipe, tts, _, realtime = _pipeline(accepted=True)
    event = reply(name)
    await pipe._on_announcement(event)
    await pipe._on_announcement(event)
    assert len(realtime.calls) == 1
    pipe._settle_agent_reply(completed=False)
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.02)
    assert len(realtime.calls) == 1  # No immediate repetition after a barge-in.
    pipe._restore_agent_replies()
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.02)
    assert len(realtime.calls) == 2
    assert realtime.calls[0] == realtime.calls[1]
    assert not tts.calls
    pipe._settle_agent_reply(completed=True)
    pipe._restore_agent_replies()
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.02)
    assert len(realtime.calls) == 2
    assert pipe._agent_reply_inflight is None


@pytest.mark.asyncio
async def test_multiple_agent_replies_are_spoken_one_at_a_time():
    pipe, _, _, realtime = _pipeline(accepted=True)
    first, second = reply(), reply("Scout")
    await pipe._on_announcement(first)
    await pipe._on_announcement(second)
    assert pipe._agent_reply_inflight == first
    assert pipe._deferred_announcements == [second]
    pipe._settle_agent_reply(completed=True)
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.02)
    assert len(realtime.calls) == 2
    assert pipe._agent_reply_inflight == second


@pytest.mark.asyncio
async def test_hangup_during_handoff_does_not_start_classic_tts():
    pipe, tts, player, _ = _pipeline(accepted=False)

    class Handle:
        async def deliver_announcement(self, **kwargs):
            pipe._hangup_event.set()
            pipe._active_realtime_handle = None
            return False

    pipe._active_realtime_handle = Handle()
    event = reply()
    await pipe._on_announcement(event)
    assert pipe._agent_reply_inflight is None
    assert pipe._deferred_announcements == [event]
    assert not tts.calls and not player.plays


@pytest.mark.asyncio
async def test_idle_boundary_retry_does_not_need_another_user_utterance():
    pipe, _, _, realtime = _pipeline(accepted=False)
    event = reply()
    await pipe._on_announcement(event)
    # The speaker is idle, but the provider wrapper is still resetting.
    realtime.accepted = True
    await asyncio.sleep(0.15)
    assert len(realtime.calls) == 2
    assert pipe._agent_reply_inflight == event
    await pipe._set_turn_state(TurnTakingState.IDLE)


@pytest.mark.asyncio
async def test_hangup_cancels_a_boundary_retry_without_losing_the_message():
    pipe, _, _, realtime = _pipeline(accepted=False)
    event = reply()
    await pipe._on_announcement(event)
    pipe._hangup_event.set()
    await pipe._set_turn_state(TurnTakingState.IDLE)
    assert pipe._agent_reply_retry_task.done()
    assert len(realtime.calls) == 1
    assert pipe._deferred_announcements == [event]


@pytest.mark.asyncio
async def test_classic_playback_failure_keeps_the_agent_reply():
    pipe, _, _, _ = _pipeline(accepted=False)
    pipe._active_voice_mode = "pipeline"
    pipe._active_realtime_handle = None

    class Player:
        async def play_chunks(self, chunks, **kwargs):
            async for _ in chunks:
                pass
            return False

    pipe._player = Player()
    event = reply()
    await pipe._on_announcement(event)
    assert pipe._deferred_announcements == [event]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["complete", "cancel", "hangup", "surface"])
async def test_real_desktop_callbacks_confirm_only_a_completed_readback(monkeypatch, ending):
    pipe = _pipe()
    pipe._turn_state = TurnTakingState.LISTENING
    event = reply()
    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts",
        lambda *args: _FakeTTS(b"\x02\x00" * 16),
    )

    class Session(_FakeRealtimeSession):
        async def handle_control(self, message):
            await self._send_json(
                {
                    "type": "audio_ready",
                    "provider": "fake-live",
                    "input_sample_rate": 16000,
                    "output_sample_rate": 24000,
                }
            )

        async def wait_finished(self):
            pipe._agent_reply_inflight = event
            pipe._agent_reply_inflight_text = event.text
            if ending == "surface":
                await self._send_json(
                    {
                        "type": "error_spoken",
                        "text": event.text,
                        "language": "en",
                        "spoken_kind": "completion",
                        "detail": event.detail,
                    }
                )
                pipe._hangup_event.set()
                await self._forever.wait()
                return
            await self._send_binary(b"\x01\x00" * 16)
            if ending == "cancel":
                await self._send_json({"type": "tts_cancel"})
            if ending != "hangup":
                await self._send_json({"type": "turn_complete"})
            if ending != "complete":
                pipe._hangup_event.set()
            await self._forever.wait()

    monkeypatch.setattr(
        "jarvis.realtime.factory.build_realtime_session",
        lambda **kw: Session(kw["send_binary"], kw["send_json"]),
    )
    monkeypatch.setattr("jarvis.speech.pipeline.MicrophoneCapture", lambda **kw: _SilentMic())
    await asyncio.wait_for(pipe._active_realtime_session(), timeout=3)
    assert pipe._agent_reply_inflight is None
    assert getattr(pipe, "_agent_reply_retries", []) == (
        [] if ending in {"complete", "surface"} else [event]
    )
