"""A failed transcription must never delete audio from a dictation.

The reported symptom was "I said more than that" — a transcript that is missing
a stretch from the middle, with nothing anywhere saying so. The cause was in the
segment loop: it closed a segment and advanced past its audio *regardless* of
whether the transcription had succeeded, on the reasoning that an empty result
means silence. An empty result has two causes, and the other one is a failed
call — so one 429 / socket reset deleted eight seconds of speech permanently (a
closed segment is never re-sent), and the next successful call reset
``stt_error`` back to ``None``, which took the last trace of it away too.

Pinned here:

* a failed segment transcription leaves the segment OPEN, so the final pass
  still sees every byte the microphone captured;
* a successful one still closes it (no O(n²) regression — that loop is the
  reason segmenting exists);
* the FINAL transcription is retried, because it is the last thing that ever
  sees the audio;
* the filler cleanup resolves a language even when the provider reports none.
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


def _speech_then_pause(loud_bytes: int, quiet_bytes: int) -> bytes:
    """PCM that is loud, then silent — so ``quietest_cut`` cuts at the pause.

    Constant-amplitude audio would put the cut at the very first probe window
    (every window scores the same), which is below the minimum segment size and
    would close no segment at all — the test would then pass for the wrong
    reason.
    """
    loud = (np.ones(loud_bytes // 2, dtype=np.int16) * 5_000).tobytes()
    quiet = np.zeros(quiet_bytes // 2, dtype=np.int16).tobytes()
    return loud + quiet


#: Three chunks of "speech, then a pause", each long enough to close a segment
#: at ``segment_seconds = 0.5``.
_CHUNKS = [_speech_then_pause(12_800, 6_400) for _ in range(3)]
_TOTAL_BYTES = sum(len(c) for c in _CHUNKS)


class _ScriptedSTT:
    """Records every PCM it is handed; fails the first ``fail_first`` calls."""

    def __init__(self, *, fail_first: int, text: str = "recovered") -> None:
        self.calls: list[bytes] = []
        self._fail_first = fail_first
        self._text = text

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None):
        self.calls.append(bytes(pcm))
        if len(self.calls) <= self._fail_first:
            raise RuntimeError("provider hiccup")
        return SimpleNamespace(text=self._text, language="de", is_partial=False)


class _ScriptedMic:
    """Yields the fixed chunk list, then holds the stream open."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _ScriptedMic:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def stream(self):  # type: ignore[no-untyped-def]
        for chunk in _CHUNKS:
            yield SimpleNamespace(pcm=chunk)
        await asyncio.Event().wait()


def _pipeline(bus: EventBus, stt: _ScriptedSTT, cfg: DictationConfig) -> SpeechPipeline:
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


async def _run_dictation(
    monkeypatch, stt: _ScriptedSTT, cfg: DictationConfig, *, wait_for_calls: int
) -> dict[str, object]:
    """Run one dictation to completion; return the kwargs ``_finish`` received."""
    bus = EventBus()
    pipe = _pipeline(bus, stt, cfg)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _ScriptedMic)

    captured: dict[str, object] = {}

    async def _fake_finish(**kwargs: object) -> str:
        captured.update(kwargs)
        pipe._dictation_completion_published = True
        return str(kwargs.get("raw_text") or "")

    pipe._finish_dictation = _fake_finish  # type: ignore[method-assign]

    assert pipe.start_dictation(target="chat") is True
    # Wait for the probe to have done its work rather than sleeping a fixed
    # amount — a timing-based test on a loaded CI box is a flake generator.
    deadline = asyncio.get_running_loop().time() + 5.0
    while len(stt.calls) < wait_for_calls:
        if asyncio.get_running_loop().time() > deadline:
            break
        await asyncio.sleep(0.01)
    pipe.stop_dictation()
    await asyncio.wait_for(pipe._dictation_task, timeout=10.0)
    return captured


@pytest.mark.asyncio
async def test_a_failed_segment_does_not_delete_its_audio(monkeypatch) -> None:
    """Every byte reaches the final pass when the provider keeps failing.

    Before the fix each probe tick closed its segment anyway, so by the time
    the user let go the final call only saw whatever was left after the holes —
    and the holes were the words the transcript was missing.
    """
    stt = _ScriptedSTT(fail_first=99)  # nothing ever succeeds
    cfg = DictationConfig(partial_interval_s=0.02, segment_seconds=0.5)

    await _run_dictation(monkeypatch, stt, cfg, wait_for_calls=3)

    assert stt.calls, "the probe must have attempted at least one transcription"
    assert max(len(c) for c in stt.calls) == _TOTAL_BYTES, (
        "no call ever saw the whole recording — audio was closed off unread"
    )


@pytest.mark.asyncio
async def test_a_successful_segment_still_closes(monkeypatch) -> None:
    """The gegenprobe: segmenting must keep working, or every dictation pays
    the O(n²) cost this design exists to avoid."""
    stt = _ScriptedSTT(fail_first=0)
    cfg = DictationConfig(partial_interval_s=0.02, segment_seconds=0.5)

    captured = await _run_dictation(monkeypatch, stt, cfg, wait_for_calls=3)

    assert len(stt.calls[-1]) < _TOTAL_BYTES, (
        "the final call re-sent the whole buffer — no segment was ever closed"
    )
    assert str(captured.get("raw_text") or "")


@pytest.mark.asyncio
async def test_the_final_transcription_is_retried(monkeypatch) -> None:
    """The last call is the last chance the audio ever gets.

    With the live probe off there is exactly one transcription, at the end. A
    single transient failure there used to cost the entire dictation — at the
    precise moment the user has stopped speaking and is waiting for their text.
    """
    stt = _ScriptedSTT(fail_first=1, text="the words")
    cfg = DictationConfig(partial_interval_s=0.0, segment_seconds=0.5)

    captured = await _run_dictation(monkeypatch, stt, cfg, wait_for_calls=0)

    assert len(stt.calls) >= 2, "the failed final transcription was not retried"
    assert captured.get("raw_text") == "the words"
    assert captured.get("stt_error") is None


@pytest.mark.asyncio
async def test_the_retry_gives_up_instead_of_hanging(monkeypatch) -> None:
    """A genuinely dead provider must not turn into an endless retry loop."""
    stt = _ScriptedSTT(fail_first=99)
    cfg = DictationConfig(partial_interval_s=0.0, segment_seconds=0.5)

    captured = await _run_dictation(monkeypatch, stt, cfg, wait_for_calls=0)

    assert len(stt.calls) == pipeline_mod._DICTATION_FINAL_ATTEMPTS
    assert captured.get("raw_text") == ""
    # The failure survives to the delivery half, which is what separates
    # "the provider rejected us" from "you said nothing".
    assert captured.get("stt_error")


# --------------------------------------------------------------------------
# Cleanup language resolution
# --------------------------------------------------------------------------


class _NullBus:
    async def publish(self, event: object) -> None:
        return None


def _delivery_pipeline() -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._bus = _NullBus()
    pipe._dictation_cfg = DictationConfig(target="chat", language="auto")
    pipe._dictation_completion_published = True
    return pipe


@pytest.mark.asyncio
async def test_fillers_are_removed_even_when_the_provider_names_no_language(
    monkeypatch,
) -> None:
    """``transcribe_pcm`` is only contracted to return TEXT.

    A provider that reports no language left the cleanup with nothing to match
    on, so it resolved to "no rules for this language" and every hesitation
    sound the user made went into the text — while the setting said filler
    removal was on. The language is read off the transcript instead, through
    the one canonical resolver.
    """
    pipe = _delivery_pipeline()
    recorded: dict[str, object] = {}

    async def _no_history(**kwargs: object) -> None:
        recorded.update(kwargs)

    pipe._record_dictation = _no_history  # type: ignore[method-assign]

    # The German hesitation sounds ARE the input under test (CLAUDE.md §1 #4):
    # the filler rules are per-language, so proving they ran needs a sentence
    # in a language that has rules.
    spoken = "Also ähm ich wollte äh kurz Bescheid sagen"  # i18n-allow: input under test

    cleaned = await pipe._finish_dictation(
        raw_text=spoken,
        language="",  # the provider said nothing
        duration_s=1.0,
        target="chat",
        hung_up=False,
    )

    assert "ähm" not in cleaned  # i18n-allow: input vocabulary under test
    assert "äh " not in cleaned  # i18n-allow: input vocabulary under test
    assert "Bescheid sagen" in cleaned  # i18n-allow: content words must survive
    assert recorded.get("language") == "de"


@pytest.mark.asyncio
async def test_a_reported_language_still_wins_over_the_text(monkeypatch) -> None:
    """Detection is the LAST resort, not a second opinion: a provider that did
    report a language keeps it, so a short or mixed transcript can never
    reclassify a dictation the provider was sure about."""
    pipe = _delivery_pipeline()
    recorded: dict[str, object] = {}

    async def _no_history(**kwargs: object) -> None:
        recorded.update(kwargs)

    pipe._record_dictation = _no_history  # type: ignore[method-assign]

    await pipe._finish_dictation(
        raw_text="uh the thing is basically fine",
        language="English",
        duration_s=1.0,
        target="chat",
        hung_up=False,
    )

    assert recorded.get("language") == "English"
