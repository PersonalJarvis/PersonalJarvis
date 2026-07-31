"""A final-pass window that stops at a pause is re-read, not believed.

The failure this guards (live 2026-07-31): gpt-4o-class recognizers handed a
recording with a sustained mid-recording pause transcribe up to the pause and
silently drop everything after it. An 11.6 s dictation whose speaker breathed
after the first sentence came back as that sentence alone — fluent, punctuated,
and half of what was said — and it did so twice in a row, so this is a
behaviour, not a flake.

The detection is energy-only (AP-27's lesson applied to dictation): a window
whose transcript carries far fewer spoken tokens than its VOICED seconds must
have contained is missing speech. The repair re-reads the window split at its
pauses and keeps whichever reading carries more speech — never less.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted
from jarvis.dictation.merge import transcript_token_count
from jarvis.dictation.segment import speech_runs
from jarvis.speech.pipeline import SpeechPipeline

# 16 kHz mono int16 — the capture contract every dictation records under.
BYTES_PER_SECOND = 16_000 * 2


def _paused_recording(first_s: float = 3.0, pause_s: float = 1.2, second_s: float = 3.0) -> bytes:
    """Speech, a sentence-length pause, speech — the shape that truncates.

    Sample value 0x2211 = 8721 is far above every silence threshold; the pause
    is digital zero.
    """
    return (
        b"\x11\x22" * int(16_000 * first_s)
        + b"\x00\x00" * int(16_000 * pause_s)
        + b"\x11\x22" * int(16_000 * second_s)
    )


# --------------------------------------------------------------------------
# speech_runs — the energy scan the guard is built on
# --------------------------------------------------------------------------


def test_a_sustained_pause_splits_the_recording_into_two_runs() -> None:
    pcm = _paused_recording()
    runs = speech_runs(pcm, bytes_per_second=BYTES_PER_SECOND)

    assert len(runs) == 2
    first, second = runs
    assert first[0] == 0
    assert second[1] == len(pcm)
    # The seam lies inside the silent stretch, not inside either run.
    assert first[1] < second[0]
    assert 3.0 * BYTES_PER_SECOND <= first[1] <= 3.4 * BYTES_PER_SECOND
    assert 3.8 * BYTES_PER_SECOND <= second[0] <= 4.2 * BYTES_PER_SECOND


def test_an_inter_word_pause_stays_inside_its_run() -> None:
    pcm = _paused_recording(pause_s=0.3)
    assert len(speech_runs(pcm, bytes_per_second=BYTES_PER_SECOND)) == 1


def test_continuous_speech_is_one_run_and_silence_is_none() -> None:
    loud = b"\x11\x22" * (3 * 16_000)
    assert len(speech_runs(loud, bytes_per_second=BYTES_PER_SECOND)) == 1
    silent = b"\x00\x00" * (3 * 16_000)
    assert speech_runs(silent, bytes_per_second=BYTES_PER_SECOND) == []


# --------------------------------------------------------------------------
# transcript_token_count — spoken tokens, with no default language
# --------------------------------------------------------------------------


def test_tokens_are_words_for_spaced_scripts() -> None:
    assert transcript_token_count("Please use simple words.") == 4


def test_tokens_are_characters_for_space_free_scripts() -> None:
    # Each ideograph or kana is roughly one spoken syllable; one "word" per
    # sentence would make every Chinese transcript look truncated.
    # i18n-allow: Japanese transcription fixture
    assert transcript_token_count("今日は東京へ行きます") == 10
    # i18n-allow: mixed-script fixture
    assert transcript_token_count("ok 東京") == 3


# --------------------------------------------------------------------------
# The whole session — a truncated window is repaired, a healthy one is not
# --------------------------------------------------------------------------


@dataclass
class _Transcript:
    text: str
    language: str = "en"


class _ScriptedSTT:
    """A provider answering a fixed script, one entry per call."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        self.calls += 1
        step = self._script[min(self.calls, len(self._script)) - 1]
        if isinstance(step, BaseException):
            raise step
        return _Transcript(text=step)


class _Chunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.timestamp_ns = 0


class _FakeMic:
    """One burst of prepared audio, then it waits to be cancelled."""

    def __init__(self, pcm: bytes) -> None:
        self._pcm = pcm

    async def stream(self):  # noqa: ANN201 — an async generator of chunks
        yield _Chunk(self._pcm)
        await asyncio.sleep(3600)


class _NullCapture:
    def __init__(self, source: Any) -> None:
        self._source = source

    async def __aenter__(self) -> Any:
        return self._source

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _session_pipeline(stt: Any, pcm: bytes):
    """A pipeline wired for exactly one ``_dictation_session`` run."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = DictationConfig(
        history_enabled=False,
        # Unsegmented and probe-free: every provider call in the test belongs
        # to the final pass, so the script maps one entry per window or run.
        segment_seconds=0.0,
        partial_interval_s=0.0,
        polish=False,
    )
    pipe._dictation_target = "chat"
    pipe._dictation_completion_published = False
    pipe._dictation_max_s = 30.0
    pipe._dictation_stt_instance = stt
    pipe._stt_final_timeout_s = 8.0
    pipe._hangup_event = asyncio.Event()
    pipe._dictation_stop_event = asyncio.Event()
    events: list[object] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._publish_event_soon = events.append  # type: ignore[assignment]
    pipe._capture_dictation_input = lambda: _NullCapture(_FakeMic(pcm))  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: SimpleNamespace(  # type: ignore[assignment]
        status="inserted", detail="", method="clipboard+ctrl_v"
    )

    async def _stop_live(task, **_kwargs):  # noqa: ANN001, ANN202
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    pipe._stop_ptt_live_transcription = _stop_live  # type: ignore[assignment]
    return pipe, events


async def _run_session(pipe: Any) -> None:
    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.sleep(0)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)


def _completed(events: list[object]) -> DictationCompleted:
    return next(e for e in events if isinstance(e, DictationCompleted))


async def test_a_window_cut_at_a_pause_is_reread_and_the_tail_recovered() -> None:
    # 6 s of voiced audio; the full-window read answers with 4 tokens — the
    # exact live shape: a fluent first sentence and a silently dropped tail.
    stt = _ScriptedSTT(
        [
            "Please use simple words.",
            "Please use simple words.",
            "by expressing yourself in simple, precise language.",
        ]
    )
    pipe, events = _session_pipeline(stt, _paused_recording())

    await _run_session(pipe)

    completed = _completed(events)
    assert "precise language" in completed.raw_text
    assert stt.calls == 3
    assert "truncation_repairs:1" in completed.stt_audit


async def test_a_healthy_window_is_not_reread() -> None:
    # Same pause, but the transcript's token count covers its voiced seconds
    # — eight tokens for ~6 s of speech is a normal reading, not a truncation.
    stt = _ScriptedSTT(["one two three four five six seven eight"])
    pipe, events = _session_pipeline(stt, _paused_recording())

    await _run_session(pipe)

    assert stt.calls == 1
    assert "truncation_repairs:0" in _completed(events).stt_audit


async def test_a_failed_run_read_keeps_the_original_transcript() -> None:
    # The second run's read dies for good. Delivering the half-merged split
    # would trade a dropped tail for a dropped middle — keep the original.
    stt = _ScriptedSTT(
        [
            "Please use simple words.",
            RuntimeError("provider went away"),
        ]
    )
    pipe, events = _session_pipeline(stt, _paused_recording())

    await _run_session(pipe)

    completed = _completed(events)
    assert completed.raw_text == "Please use simple words."
    assert "truncation_repairs:0" in completed.stt_audit
