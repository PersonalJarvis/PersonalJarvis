"""A pause in the middle of a dictation must not cost the words after it.

The reported symptom: speak, stop to think for ten-odd seconds while still
holding the key, speak again — and everything after the pause is missing, while
the bar kept showing an active dictation the whole time. The live log of that
session shows the mechanism in full:

* the live preview re-uploaded the whole open tail on every tick, so a pause was
  billed as a request per tick while producing no text;
* Groq answered ``429 Too Many Requests``, and a refused call correctly leaves
  its segment OPEN — so the tail grew, and the next tick uploaded even more;
* from there each round cost more than the last, the uploads stalled the event
  loop the microphone was being drained on, and the capture queue's drop-oldest
  policy deleted real speech to keep up (``Mic closed (drops=94)`` — 9.4 s);
* and the silence itself came back as invented sentences, which is what put
  "Thank you for watching!" in the middle of a German dictation.

What is pinned here is the shape that breaks that chain: silence is never
uploaded, a backlog is drained in one tick rather than one segment per tick, a
failed call backs off instead of hammering, and the final pass goes out in
pieces so one bad piece cannot cost the whole remainder.

Every judgement about silence is made on ENERGY, never on the text that came
back (AP-27) — a content rule cannot tell a hallucination from a real sentence.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.bus import EventBus
from jarvis.core.config import DictationConfig
from jarvis.speech.pipeline import PipelineState, SpeechPipeline

BYTES_PER_SECOND = 16_000 * 2

#: What a transcription model does with silence. Not a joke: this exact string
#: is in the maintainer's dictation history, in the middle of a German sentence.
HALLUCINATION = "Thank you for watching!"


def _word(index: int, seconds: float = 0.5) -> bytes:
    """A stretch of "speech" whose amplitude identifies it uniquely.

    Being able to tell WHICH words reached the provider is the whole point:
    "some audio was transcribed" is exactly the claim the broken version could
    also make.
    """
    amplitude = 4_000 + index * 500
    samples = int(seconds * 16_000)
    return (np.ones(samples, dtype=np.int16) * amplitude).tobytes()


def _silence(seconds: float) -> bytes:
    return np.zeros(int(seconds * 16_000), dtype=np.int16).tobytes()


class _MarkerSTT:
    """Reports which marker words a piece of audio contains.

    Mimics the real failure modes: silence comes back as a hallucinated
    sentence (never as ``""``), and the first ``fail_first`` calls are refused
    the way a rate limit refuses them.
    """

    def __init__(self, *, fail_first: int = 0) -> None:
        self.calls: list[bytes] = []
        self.silent_calls = 0
        self._fail_first = fail_first

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None):
        self.calls.append(bytes(pcm))
        if len(self.calls) <= self._fail_first:
            raise RuntimeError("429 Too Many Requests")
        samples = np.frombuffer(pcm, dtype=np.int16)
        amplitudes = {int(v) for v in np.unique(np.abs(samples)) if v}
        words = [f"w{(a - 4_000) // 500}" for a in sorted(amplitudes)]
        if not words:
            # Every real transcription model does this, and it is why silence
            # must never be uploaded in the first place.
            self.silent_calls += 1
            return SimpleNamespace(text=HALLUCINATION, language="en")
        return SimpleNamespace(text=" ".join(words), language="de")


class _ScriptedMic:
    """Yields a fixed script of chunks, then holds the stream open."""

    script: list[bytes] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _ScriptedMic:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def stream(self):  # type: ignore[no-untyped-def]
        for chunk in type(self).script:
            yield SimpleNamespace(pcm=chunk)
            # Let the probe run between chunks, so the test exercises the real
            # interleaving of "audio arrives while a transcription is in flight"
            # instead of handing the loop a finished buffer.
            await asyncio.sleep(0)
        await asyncio.Event().wait()


def _pipeline(bus: EventBus, stt: _MarkerSTT, cfg: DictationConfig) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._bus = bus
    pipe._utterance_stt = stt
    pipe._dictation_task = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._dictation_cfg = cfg
    pipe._dictation_max_s = 30.0
    pipe._dictation_wake_block_until = 0.0
    pipe._dictation_completion_published = True
    pipe._ptt_mode = False
    pipe._ptt_partial_interval_s = 0.0
    pipe._state = PipelineState.IDLE
    pipe._muted = False
    pipe._input_device = "default"
    pipe._input_priority = ()
    pipe._hangup_event = asyncio.Event()
    return pipe


async def _run(
    monkeypatch,
    *,
    script: list[bytes],
    stt: _MarkerSTT,
    cfg: DictationConfig,
) -> dict[str, object]:
    """Record the whole script, release the key, return ``_finish``'s kwargs."""
    bus = EventBus()
    pipe = _pipeline(bus, stt, cfg)
    _ScriptedMic.script = script
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _ScriptedMic)

    captured: dict[str, object] = {}

    async def _fake_finish(**kwargs: object) -> str:
        captured.update(kwargs)
        pipe._dictation_completion_published = True
        return str(kwargs.get("raw_text") or "")

    pipe._finish_dictation = _fake_finish  # type: ignore[method-assign]

    assert pipe.start_dictation(target="chat") is True
    # Wait for the recording itself to be complete rather than for a fixed
    # sleep: a timing-based assertion on a loaded box is a flake generator.
    total = sum(len(c) for c in script)
    deadline = asyncio.get_running_loop().time() + 10.0
    while asyncio.get_running_loop().time() < deadline:
        if pipe._dictation_task is None or pipe._dictation_task.done():
            break
        await asyncio.sleep(0.01)
        # The drain has seen everything once the probe can no longer grow.
        if total and stt.calls and asyncio.get_running_loop().time() > deadline - 9.0:
            break
    pipe.stop_dictation()
    await asyncio.wait_for(pipe._dictation_task, timeout=10.0)
    return captured


@pytest.mark.asyncio
async def test_words_after_a_long_pause_still_reach_the_transcript(monkeypatch) -> None:
    """The reported bug, end to end.

    Speech, a long think-pause, more speech. Before the fix the pause was
    uploaded tick after tick, which is what spent the rate limit and then took
    the words after it down with the rest.
    """
    script = [_word(0), _word(1), _silence(12.0), _word(2), _word(3)]
    stt = _MarkerSTT()
    cfg = DictationConfig(
        partial_interval_s=0.02,
        segment_seconds=2.0,
        final_quality_pass=False,
    )

    captured = await _run(monkeypatch, script=script, stt=stt, cfg=cfg)

    text = str(captured.get("raw_text") or "")
    for marker in ("w0", "w1", "w2", "w3"):
        assert marker in text, f"{marker} was spoken but never made it into {text!r}"


@pytest.mark.asyncio
async def test_a_pause_is_never_uploaded(monkeypatch) -> None:
    """Silence costs neither a request nor an invented sentence.

    Both halves matter: the requests are what ran into the rate limit, and the
    invented sentence is what the user found in the middle of their own text.
    """
    script = [_word(0), _silence(12.0), _word(1)]
    stt = _MarkerSTT()
    cfg = DictationConfig(partial_interval_s=0.02, segment_seconds=2.0)

    captured = await _run(monkeypatch, script=script, stt=stt, cfg=cfg)

    assert stt.silent_calls == 0, "a silent stretch was sent to the provider"
    assert HALLUCINATION not in str(captured.get("raw_text") or "")


@pytest.mark.asyncio
async def test_the_open_tail_never_grows_past_one_segment(monkeypatch) -> None:
    """No upload may keep getting bigger.

    This is the spiral itself: an ever-growing tail means every tick uploads
    more audio than the last, until the work of building those requests stalls
    the loop the microphone is drained on. The cap holds even when the audio
    arrives faster than the probe can close segments.
    """
    script = [_word(i, seconds=1.0) for i in range(12)]
    stt = _MarkerSTT()
    cfg = DictationConfig(
        partial_interval_s=0.02,
        segment_seconds=2.0,
        final_quality_pass=False,
    )

    await _run(monkeypatch, script=script, stt=stt, cfg=cfg)

    segment_bytes = int(2.0 * BYTES_PER_SECOND)
    biggest = max(len(c) for c in stt.calls)
    assert biggest <= segment_bytes, (
        f"an upload reached {biggest / BYTES_PER_SECOND:.1f}s of audio — the "
        f"tail is growing without bound again"
    )


@pytest.mark.asyncio
async def test_refused_calls_do_not_cost_the_words(monkeypatch) -> None:
    """A refused call leaves its audio open, so a later attempt still reads it.

    The refusal under test is the real one: HTTP 429. The recording is untouched
    by it — the words have to arrive in full once a call gets through.
    """
    script = [_word(0), _word(1), _word(2)]
    stt = _MarkerSTT(fail_first=2)
    cfg = DictationConfig(partial_interval_s=0.02, segment_seconds=1.0)

    captured = await _run(monkeypatch, script=script, stt=stt, cfg=cfg)

    text = str(captured.get("raw_text") or "")
    for marker in ("w0", "w1", "w2"):
        assert marker in text, f"{marker} was lost to the refused calls: {text!r}"


@pytest.mark.asyncio
async def test_one_dead_piece_does_not_cost_the_others(monkeypatch) -> None:
    """The point of finalizing in pieces.

    A piece whose every attempt fails is a real loss — but a bounded one. As a
    single upload of the whole remainder, that same failure took every second
    still open with it, which is how a transient error at the end used to cost
    a dictation the user had already finished speaking.
    """

    class _OneBadPiece(_MarkerSTT):
        async def transcribe_pcm(self, pcm: bytes, language: str | None = None):
            samples = np.frombuffer(pcm, dtype=np.int16)
            if 4_000 in {int(v) for v in np.unique(np.abs(samples))}:
                self.calls.append(bytes(pcm))
                raise RuntimeError("429 Too Many Requests")
            return await super().transcribe_pcm(pcm, language)

    script = [_word(i, seconds=1.0) for i in range(4)]
    stt = _OneBadPiece()
    cfg = DictationConfig(
        partial_interval_s=0.0,
        segment_seconds=1.0,
        final_quality_pass=False,
    )

    captured = await _run(monkeypatch, script=script, stt=stt, cfg=cfg)

    text = str(captured.get("raw_text") or "")
    assert "w0" not in text, "the failing piece was expected to be lost"
    for marker in ("w1", "w2", "w3"):
        assert marker in text, (
            f"{marker} was taken down by an unrelated failing piece: {text!r}"
        )


@pytest.mark.asyncio
async def test_the_final_pass_goes_out_in_pieces(monkeypatch) -> None:
    """One timeout must not cost every second still open.

    With the preview off, everything lands on the final pass. It has to leave in
    segment-sized pieces — a single upload of the whole remainder is the shape
    where one failure loses all of it.
    """
    script = [_word(i, seconds=1.0) for i in range(6)]
    stt = _MarkerSTT()
    cfg = DictationConfig(
        partial_interval_s=0.0,
        segment_seconds=2.0,
        final_quality_pass=False,
    )

    captured = await _run(monkeypatch, script=script, stt=stt, cfg=cfg)

    assert len(stt.calls) > 1, "the whole remainder went out as one upload"
    text = str(captured.get("raw_text") or "")
    for marker in (f"w{i}" for i in range(6)):
        assert marker in text, f"{marker} was lost by the chunked final pass: {text!r}"


@pytest.mark.asyncio
async def test_a_recording_that_ends_early_is_reported(monkeypatch) -> None:
    """A truncated transcript must never be presented as the whole thing.

    The handoff buffer a dictation borrows from the wake loop raises once its
    replay window is exceeded. That killed the drain task, and the failure was
    swallowed with the teardown's cancellations — so the user got a transcript
    that stopped halfway with nothing anywhere saying why.
    """

    class _DyingMic(_ScriptedMic):
        async def stream(self):  # type: ignore[no-untyped-def]
            yield SimpleNamespace(pcm=_word(0))
            raise RuntimeError("Voice input exceeded the 30-second replay window")

    stt = _MarkerSTT()
    cfg = DictationConfig(partial_interval_s=0.0, segment_seconds=2.0)
    bus = EventBus()
    pipe = _pipeline(bus, stt, cfg)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _DyingMic)

    captured: dict[str, object] = {}

    async def _fake_finish(**kwargs: object) -> str:
        captured.update(kwargs)
        pipe._dictation_completion_published = True
        return str(kwargs.get("raw_text") or "")

    pipe._finish_dictation = _fake_finish  # type: ignore[method-assign]

    assert pipe.start_dictation(target="chat") is True
    await asyncio.wait_for(pipe._dictation_task, timeout=10.0)

    # The reason CODE, not the exception text: this value ends up under the
    # user's own words in the history, where a stack-trace fragment explains
    # nothing. The exception itself goes to the log.
    assert str(captured.get("stt_error") or "") == "recording_interrupted", (
        "the recording died and the dictation reported nothing about it"
    )
