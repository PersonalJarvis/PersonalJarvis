"""RUB-35: a long screen response must not exhaust the input replay reader."""

import asyncio
import time
from types import SimpleNamespace

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.constants import HANGUP_ERROR, HANGUP_HOTKEY, HANGUP_IDLE_TIMEOUT
from jarvis.speech.pipeline import TurnTakingState, _SessionInputBuffer
from tests.unit.speech.test_idle_spawn_inflight import _make_active_session_pipeline
from tests.unit.speech.test_turn_taking import FakeSTT, _make_pipeline


class EndpointPerChunk:
    async def utterances(self, chunks):
        async for chunk in chunks:
            yield chunk.pcm


@pytest.mark.asyncio
async def test_long_inline_screen_answer_keeps_next_user_command() -> None:
    pipe = _make_active_session_pipeline(idle_timeout_s=30)
    pipe._idle_hangup_enabled = False
    pipe._post_tts_listen_suppression_s = 0.3
    pipe._vad = EndpointPerChunk()
    buffer = _SessionInputBuffer()
    start_ns = time.time_ns() - 70_000_000_000
    buffer.put(AudioChunk(pcm=b"translate-screen", sample_rate=16000, timestamp_ns=start_ns))
    received = []

    async def handle(pcm):
        received.append(pcm)
        if len(received) == 1:
            await pipe._set_turn_state(TurnTakingState.PROCESSING)
            # 60 seconds of actual PCM volume while the inline tool/generation
            # owns the loop. This exceeds the unchanged 30-second replay cap.
            for second in range(1, 61):
                buffer.put(AudioChunk(
                    pcm=b"\x00\x00" * 16000,
                    sample_rate=16000,
                    timestamp_ns=start_ns + second * 1_000_000_000,
                ))
            await pipe._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            # Exercise the existing half-duplex policy after final audio.
            pipe._suppress_session_input_after_tts("response")
            await pipe._set_turn_state(TurnTakingState.LISTENING)
            buffer.put(AudioChunk(
                pcm=b"next-user-command", sample_rate=16000,
                timestamp_ns=pipe._input_suppressed_until_ns + 1,
            ))
            return True
        pipe._session_end_reason = HANGUP_HOTKEY  # Test-controlled stop after follow-up.
        return False

    pipe._handle_utterance = handle
    try:
        reason = await asyncio.wait_for(pipe._active_session(input_buffer=buffer), timeout=2)
    finally:
        await buffer.close()

    assert received == [b"translate-screen", b"next-user-command"]
    assert reason == HANGUP_HOTKEY
    assert not getattr(pipe, "_termination_detail", {}).get("input_replay_overrun")


@pytest.mark.asyncio
@pytest.mark.parametrize("cutoff", [0, 2])
async def test_unsuppressed_command_prefix_overflow_remains_an_error(cutoff: int) -> None:
    buffer = _SessionInputBuffer(max_buffer_bytes=4)
    buffer.put(AudioChunk(pcm=b"aaaa", sample_rate=16000, timestamp_ns=1))
    stream = buffer.stream(discard_before_ns=lambda: cutoff)
    assert (await anext(stream)).pcm == b"aaaa"
    buffer.put(AudioChunk(pcm=b"bbbb", sample_rate=16000, timestamp_ns=2))
    buffer.put(AudioChunk(pcm=b"cccc", sample_rate=16000, timestamp_ns=3))
    with pytest.raises(RuntimeError, match="refusing to drop the command prefix"):
        await anext(stream)
    await buffer.close()


@pytest.mark.asyncio
async def test_startup_prefix_guard_cannot_be_bypassed_by_old_echo_cutoff() -> None:
    buffer = _SessionInputBuffer(max_buffer_bytes=4)
    buffer.put(AudioChunk(pcm=b"aaaa", sample_rate=16000, timestamp_ns=1))
    buffer.put(AudioChunk(pcm=b"bbbb", sample_rate=16000, timestamp_ns=2))
    with pytest.raises(RuntimeError, match="refusing to drop the command prefix"):
        await anext(buffer.stream(discard_before_ns=lambda: 100))
    await buffer.close()


@pytest.mark.asyncio
async def test_capture_failure_is_not_mislabeled_as_shutdown() -> None:
    pipe = _make_active_session_pipeline(idle_timeout_s=30)
    pipe._vad = EndpointPerChunk()
    buffer = _SessionInputBuffer(max_buffer_bytes=4)
    buffer.put(AudioChunk(pcm=b"aaaa", sample_rate=16000, timestamp_ns=1))
    buffer.put(AudioChunk(pcm=b"bbbb", sample_rate=16000, timestamp_ns=2))
    try:
        reason = await pipe._active_session(input_buffer=buffer)
    finally:
        await buffer.close()
    assert reason == HANGUP_ERROR
    assert pipe._termination_producer == "speech.pipeline._active_session.vad_error"
    assert pipe._termination_detail["input_error_type"] == "RuntimeError"
    assert pipe._termination_detail["input_replay_overrun"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_completed_information_request_cannot_end_conversation(streaming: bool) -> None:
    response = "Die Markierung erklärt die Gedanken eines Agenten. [[END_CALL]]"  # i18n-allow
    pipe = _make_pipeline(
        FakeSTT(text="Kannst du mir bitte den markierten Text erklären?"),  # i18n-allow
        brain_response=response,
        continue_listening_after_response=True,
    )
    pipe._streaming_enabled = lambda: streaming
    if streaming:
        async def brain_streaming(_text, _lang):
            return response, False

        async def stall_guard(coro, **_kwargs):
            return await coro

        pipe._brain_streaming = brain_streaming
        pipe._run_brain_with_stall_guard = stall_guard
    keep_listening = await pipe._handle_utterance(b"\x01\x00" * 1024)
    assert keep_listening
    assert not pipe._hangup_event.is_set()
    assert pipe._session_end_reason is None
    assert pipe._termination_detail["hangup_pattern_matched"] is False
    assert pipe._termination_detail["end_call_signal"] is True


@pytest.mark.asyncio
async def test_buffered_tool_and_tts_get_fresh_idle_window_after_final_audio(monkeypatch) -> None:
    pipe = _make_active_session_pipeline(idle_timeout_s=30)
    pipe._idle_hangup_enabled = True
    pipe._post_readback_grace_s = 30
    clock = [0.0]
    monkeypatch.setattr(pipeline_mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    generation_gate = asyncio.Event()
    speech_started = asyncio.Event()
    speech_gate = asyncio.Event()

    async def generate(_text, **_kwargs):
        await generation_gate.wait()
        return "Here is the explanation of the selected screen text."

    async def speak(_text, **_kwargs):
        speech_started.set()
        await speech_gate.wait()

    pipe._brain = SimpleNamespace(generate=generate)
    pipe._streaming_enabled = lambda: False
    pipe._speak = speak
    response_task = asyncio.create_task(
        pipe._handle_flushed_pending_text("Explain my screen", "en")
    )
    await asyncio.sleep(0)
    waits = []

    async def wait(_tasks, *, timeout, return_when):  # noqa: ASYNC109 - asyncio.wait test double
        waits.append(timeout)
        clock[0] += timeout
        if len(waits) == 2:
            generation_gate.set()  # Tool/generation takes 60 seconds.
            await speech_started.wait()
        elif len(waits) == 3:
            speech_gate.set()  # TTS finishes at 90 seconds.
            await response_task
        return set(), _tasks

    monkeypatch.setattr(pipeline_mod.asyncio, "wait", wait)
    buffer = _SessionInputBuffer()
    try:
        reason = await pipe._active_session(input_buffer=buffer)
    finally:
        await buffer.close()
        if not response_task.done():
            response_task.cancel()
        await asyncio.gather(response_task, return_exceptions=True)

    assert waits == [30, 30, 30, 30]
    assert clock[0] == 120  # A full 30 seconds AFTER final audio.
    assert reason == HANGUP_IDLE_TIMEOUT
    assert pipe._assistant_work_count == 0
