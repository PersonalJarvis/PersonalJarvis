"""Regression tests for the long-dictation truncation bug.

When the user dictates continuously past the VAD max-utterance cap, the VAD
force-cuts the utterance (``reason="max_utterance"``) and yields only a
fragment. The pipeline must ACCUMULATE consecutive forced-cut fragments and
only finalize (transcribe + brain turn) at a natural endpoint
(``silence`` / ``stt_stable``). Otherwise a long dictation is chopped into
independent turns and the earlier words are "forgotten" — the user-reported
symptom ("it forgets the old words and starts over").

Reason vocabulary lives in :mod:`jarvis.audio.vad_reasons` (single source of
truth shared by the VAD producer and this consumer).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import Transcript
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


class RecordingSTT:
    """Records every PCM blob handed to ``transcribe_pcm``; returns empty text.

    Empty text makes ``_handle_utterance`` short-circuit right after STT (the
    empty-transcript guard), so the test exercises the accumulation seam
    without needing to stub the entire brain path.
    """

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def transcribe_pcm(self, pcm: bytes) -> Transcript:
        self.calls.append(bytes(pcm))
        return Transcript(text="", language="de", confidence=0.0, is_partial=False)


def _make_pipeline(stt: RecordingSTT) -> tuple[SpeechPipeline, list[TurnTakingState]]:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = stt
    pipe._stt_final_timeout_s = 1.0
    pipe._config = SimpleNamespace(latency=SimpleNamespace(enabled=False))
    pipe._bus = EventBus()
    pipe._latency_first_audio_marked = False
    pipe._latency_tracker = None
    # Accumulation state (the fields under test).
    pipe._last_endpoint_reason = None
    pipe._carry_pcm = bytearray()
    pipe._carry_started_monotonic = None

    states: list[TurnTakingState] = []

    async def _set_turn_state(state: TurnTakingState) -> None:
        states.append(state)

    async def _publish_event(_event: object) -> None:
        return None

    pipe._set_turn_state = _set_turn_state  # type: ignore[method-assign]
    pipe._publish_event = _publish_event  # type: ignore[method-assign]
    return pipe, states


@pytest.mark.asyncio
async def test_forced_cut_is_buffered_without_transcription() -> None:
    stt = RecordingSTT()
    pipe, states = _make_pipeline(stt)

    pipe._last_endpoint_reason = "max_utterance"
    keep_going = await pipe._handle_utterance(b"AAAA")

    assert keep_going is True
    # A forced mid-speech cut must NOT trigger a brain turn → no STT yet.
    assert stt.calls == []
    # The fragment is held for the next segment.
    assert bytes(pipe._carry_pcm) == b"AAAA"
    assert states and states[-1] is TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_natural_end_after_forced_cut_transcribes_merged_pcm() -> None:
    stt = RecordingSTT()
    pipe, _states = _make_pipeline(stt)

    pipe._last_endpoint_reason = "max_utterance"
    await pipe._handle_utterance(b"AAAA")

    pipe._last_endpoint_reason = "silence"
    await pipe._handle_utterance(b"BBBB")

    # STT sees the whole dictation ONCE, not two truncated turns.
    assert stt.calls == [b"AAAABBBB"]
    # Carry cleared after finalize.
    assert bytes(pipe._carry_pcm) == b""


@pytest.mark.asyncio
async def test_three_forced_cuts_then_silence_merge_in_order() -> None:
    stt = RecordingSTT()
    pipe, _states = _make_pipeline(stt)

    for blob in (b"one ", b"two ", b"three "):
        pipe._last_endpoint_reason = "max_utterance"
        await pipe._handle_utterance(blob)
    pipe._last_endpoint_reason = "silence"
    await pipe._handle_utterance(b"four")

    assert stt.calls == [b"one two three four"]


@pytest.mark.asyncio
async def test_natural_end_alone_is_single_turn() -> None:
    stt = RecordingSTT()
    pipe, _states = _make_pipeline(stt)

    pipe._last_endpoint_reason = "silence"
    await pipe._handle_utterance(b"CCCC")

    assert stt.calls == [b"CCCC"]
    assert bytes(pipe._carry_pcm) == b""


@pytest.mark.asyncio
async def test_stt_stable_also_finalizes() -> None:
    stt = RecordingSTT()
    pipe, _states = _make_pipeline(stt)

    pipe._last_endpoint_reason = "max_utterance"
    await pipe._handle_utterance(b"AAAA")
    pipe._last_endpoint_reason = "stt_stable"
    await pipe._handle_utterance(b"BBBB")

    assert stt.calls == [b"AAAABBBB"]


@pytest.mark.asyncio
async def test_empty_tail_flush_finalizes_carry() -> None:
    """Contract for the VAD tail flush (2026-06-09 "listens forever" fix):
    after a forced cut the VAD yields an EMPTY pcm with reason ``silence``
    when the user never resumes speaking — that empty flush must finalize
    the buffered carry as the turn."""
    stt = RecordingSTT()
    pipe, _states = _make_pipeline(stt)

    pipe._last_endpoint_reason = "max_utterance"
    await pipe._handle_utterance(b"AAAA")
    pipe._last_endpoint_reason = "silence"
    await pipe._handle_utterance(b"")

    assert stt.calls == [b"AAAA"]
    assert bytes(pipe._carry_pcm) == b""


@pytest.mark.asyncio
async def test_empty_flush_without_carry_skips_stt() -> None:
    """An empty tail flush with nothing buffered (e.g. the runaway guard
    already finalized the carry) must not waste an STT round-trip on zero
    bytes of audio — just keep listening."""
    stt = RecordingSTT()
    pipe, states = _make_pipeline(stt)

    pipe._last_endpoint_reason = "silence"
    keep_going = await pipe._handle_utterance(b"")

    assert keep_going is True
    assert stt.calls == []
    assert states and states[-1] is TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_runaway_guard_finalizes_even_on_forced_cut(monkeypatch) -> None:
    stt = RecordingSTT()
    pipe, _states = _make_pipeline(stt)

    # Shrink the runaway cap so a single small fragment trips it.
    monkeypatch.setattr(
        "jarvis.speech.pipeline._MAX_CARRY_PCM_BYTES", 2, raising=True
    )

    pipe._last_endpoint_reason = "max_utterance"
    await pipe._handle_utterance(b"AAAA")  # 4 bytes > 2 → runaway → finalize anyway

    # Stuck-mic guard: finalize instead of accumulating forever.
    assert stt.calls == [b"AAAA"]
    assert bytes(pipe._carry_pcm) == b""
