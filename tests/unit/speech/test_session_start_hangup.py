"""Regression coverage for closing a voice session during startup dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.core.events import VoiceSessionEnded, VoiceSessionStarted
from jarvis.sessions.constants import HANGUP_HOTKEY
from jarvis.speech.pipeline import PipelineState, SpeechPipeline, TurnTakingState


class _FakeTTS:
    name = "fake-tts"
    supports_streaming = False

    async def synthesize(self, text: str, **_kwargs) -> AsyncIterator[bytes]:
        if False:  # pragma: no cover - protocol-shaped empty iterator
            yield text.encode()


@pytest.mark.asyncio
async def test_close_during_session_started_dispatch_skips_audio_startup() -> None:
    """A close accepted while a slow start subscriber runs must stay closed.

    The bar is already interactive while VoiceSessionStarted is being
    dispatched. Once the dispatch returns, a pending hangup must bypass the
    LISTENING transition, acknowledgement, and microphone/session startup.
    """
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipeline._activation_allowed = lambda: True  # type: ignore[method-assign]

    ack_calls: list[bool] = []
    active_calls: list[bool] = []
    ended_events: list[VoiceSessionEnded] = []
    cycle_done = asyncio.Event()

    async def _publish(event) -> None:  # noqa: ANN001
        if isinstance(event, VoiceSessionStarted):
            # Model the Tk-thread request being accepted while the owner loop is
            # still inside a slow VoiceSessionStarted subscriber. Delay the
            # actual loop callback deliberately: the synchronous, thread-safe
            # pending latch must be sufficient to stop startup on its own.
            class _DelayedOwnerLoop:
                def is_running(self) -> bool:
                    return True

                def call_soon_threadsafe(self, _callback) -> None:  # noqa: ANN001
                    return None

            pipeline._runtime_loop = _DelayedOwnerLoop()
            pipeline.request_hangup()
        elif isinstance(event, VoiceSessionEnded):
            ended_events.append(event)

    async def _set_turn_state(state, **_kwargs) -> None:  # noqa: ANN001
        if state is TurnTakingState.IDLE and ended_events:
            cycle_done.set()

    async def _play_ack(*, ptt: bool) -> None:
        ack_calls.append(ptt)

    async def _active_session() -> str:
        active_calls.append(True)
        return "unexpected"

    async def _play_earcon(*_args, **_kwargs) -> None:
        return None

    pipeline._publish_event = _publish  # type: ignore[method-assign]
    pipeline._set_turn_state = _set_turn_state  # type: ignore[method-assign]
    pipeline._play_ack = _play_ack  # type: ignore[method-assign]
    pipeline._active_session = _active_session  # type: ignore[method-assign]
    pipeline._play_earcon = _play_earcon  # type: ignore[method-assign]
    pipeline._call_event.set()

    state_task = asyncio.create_task(pipeline._state_loop())
    try:
        await asyncio.wait_for(cycle_done.wait(), timeout=1.0)
    finally:
        state_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state_task

    assert ack_calls == []
    assert active_calls == []
    assert pipeline._state is PipelineState.IDLE
    assert len(ended_events) == 1
    assert ended_events[0].hangup_reason == HANGUP_HOTKEY
