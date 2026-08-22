"""No passage goes missing: pauses are trimmed before upload, dropped tails are
read back in on the transcript's own clock, and a hardware overflow is counted.

What this guards (live 2026-08-22): a Whisper-class recognizer handed a window
with a sustained pause inside it stops at the pause and silently drops the rest
— "3 tokens for 22.4 s of speech" in the log, three times in the last forty
dictations. Two defences, both energy- and timestamp-based, never content-based
(AP-27):

* every sustained pause in an uploaded window is shortened to half a second, so
  the recognizer never sits in the stretch it gives up in;
* when the transcript's last segment ends well before the window's speech does,
  only that tail is re-read and merged onto the head the provider got right.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from jarvis.audio.capture import MicrophoneCapture
from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted
from jarvis.dictation.segment import compress_pauses, speech_runs
from jarvis.speech.pipeline import SpeechPipeline

BYTES_PER_SECOND = 16_000 * 2


def _voiced(seconds: float) -> bytes:
    return b"\x11\x22" * int(16_000 * seconds)


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(16_000 * seconds)


# --------------------------------------------------------------------------
# compress_pauses
# --------------------------------------------------------------------------


def test_a_long_pause_is_cut_to_half_a_second_and_the_speech_kept_whole() -> None:
    pcm = _voiced(3.0) + _silence(2.5) + _voiced(3.0)

    sent = compress_pauses(pcm, bytes_per_second=BYTES_PER_SECOND)

    # About 3 + 0.5 + 3 s remain (plus the runs' own onset/offset pads).
    assert 6.3 * BYTES_PER_SECOND <= len(sent) <= 7.0 * BYTES_PER_SECOND
    # Every voiced byte survived: the speech runs of the upload hold exactly
    # as much speech as the original's.
    voiced_before = sum(b - a for a, b in speech_runs(pcm, bytes_per_second=BYTES_PER_SECOND))
    voiced_after = sum(b - a for a, b in speech_runs(sent, bytes_per_second=BYTES_PER_SECOND))
    assert abs(voiced_before - voiced_after) <= 0.4 * BYTES_PER_SECOND
    # And no pause longer than the cap is left inside the upload.
    assert len(speech_runs(sent, bytes_per_second=BYTES_PER_SECOND)) == 1


def test_leading_and_trailing_silence_are_trimmed_too() -> None:
    pcm = _silence(2.0) + _voiced(2.0) + _silence(3.0)

    sent = compress_pauses(pcm, bytes_per_second=BYTES_PER_SECOND)

    assert len(sent) < 3.5 * BYTES_PER_SECOND


def test_a_window_without_a_long_pause_is_returned_as_is() -> None:
    pcm = _voiced(2.0) + _silence(0.3) + _voiced(2.0)
    assert compress_pauses(pcm, bytes_per_second=BYTES_PER_SECOND) is pcm


def test_silence_and_empty_input_are_left_alone() -> None:
    silent = _silence(3.0)
    assert compress_pauses(silent, bytes_per_second=BYTES_PER_SECOND) is silent
    assert compress_pauses(b"", bytes_per_second=BYTES_PER_SECOND) == b""


# --------------------------------------------------------------------------
# The session: upload shape and the timestamp tail repair
# --------------------------------------------------------------------------


@dataclass
class _Transcript:
    text: str
    language: str = "en"
    segments: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class _ScriptedSTT:
    """Answers a script, one entry per call, and keeps every upload it saw."""

    def __init__(self, script: list[_Transcript]) -> None:
        self._script = list(script)
        self.calls = 0
        self.uploads: list[bytes] = []

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        self.calls += 1
        self.uploads.append(pcm)
        return self._script[min(self.calls, len(self._script)) - 1]


class _Chunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.timestamp_ns = 0


class _FakeMic:
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
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = DictationConfig(
        history_enabled=False,
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


def _audit(events: list[object], key: str) -> int:
    token = next(t for t in _completed(events).stt_audit if t.startswith(key + ":"))
    return int(token.split(":", 1)[1])


_TWELVE = "one two three four five six seven eight nine ten eleven twelve"


async def test_the_upload_never_contains_the_long_pause() -> None:
    # 3 s speech, a 2.5 s thinking pause, 3 s speech — the exact shape that
    # makes a recognizer stop. Twelve tokens keep the token-rate guard quiet,
    # and a transcript end at the end of the upload keeps the clock guard
    # quiet, so the ONE call is the upload under test.
    stt = _ScriptedSTT([_Transcript(_TWELVE, segments=({"start": 0.0, "end": 6.6},))])
    pipe, events = _session_pipeline(stt, _voiced(3.0) + _silence(2.5) + _voiced(3.0))

    await _run_session(pipe)

    assert stt.calls == 1
    uploaded = stt.uploads[0]
    assert len(uploaded) < 7.2 * BYTES_PER_SECOND
    assert len(speech_runs(uploaded, bytes_per_second=BYTES_PER_SECOND)) == 1
    # The trim is reported: roughly two seconds of pause left out.
    assert 1500 <= _audit(events, "pause_trim_ms") <= 2500
    assert _audit(events, "tail_repairs") == 0


async def test_a_transcript_that_ends_early_gets_its_tail_read_back_in() -> None:
    # Seven seconds of continuous speech; the provider answers with a fluent
    # head whose last segment ends at 3.0 s — it stopped and dropped four
    # seconds of speech. Ten tokens for seven voiced seconds sit ABOVE the
    # token floor (1.3/s -> 9.1), so only the transcript's clock can see this.
    head = _Transcript(
        "please use simple words and be exact yes indeed now",
        segments=({"start": 0.0, "end": 3.0},),
    )
    tail = _Transcript(
        "and this is the part that was dropped",
        segments=({"start": 0.0, "end": 4.4},),
    )
    stt = _ScriptedSTT([head, tail])
    pipe, events = _session_pipeline(stt, _voiced(7.0))

    await _run_session(pipe)

    assert stt.calls == 2
    # The tail re-read started half a second before the transcript's end, not
    # at the start of the window.
    assert 4.0 * BYTES_PER_SECOND <= len(stt.uploads[1]) <= 4.8 * BYTES_PER_SECOND
    text = _completed(events).raw_text
    assert text.startswith("please use simple words")
    assert "part that was dropped" in text
    assert _audit(events, "tail_repairs") == 1
    assert _audit(events, "truncation_repairs") == 1


async def test_a_transcript_that_runs_to_the_end_is_not_reread() -> None:
    stt = _ScriptedSTT(
        [_Transcript(_TWELVE, segments=({"start": 0.0, "end": 6.6},))]
    )
    pipe, events = _session_pipeline(stt, _voiced(7.0))

    await _run_session(pipe)

    assert stt.calls == 1
    assert _audit(events, "tail_repairs") == 0


async def test_without_timestamps_the_token_floor_still_decides() -> None:
    # No segments at all (a provider without them): the old energy-versus-
    # token guard is all there is, and it does not fire on a healthy count.
    stt = _ScriptedSTT([_Transcript(_TWELVE)])
    pipe, events = _session_pipeline(stt, _voiced(7.0))

    await _run_session(pipe)

    assert stt.calls == 1
    assert _audit(events, "truncation_repairs") == 0


# --------------------------------------------------------------------------
# The microphone's own overflow flag is no longer thrown away
# --------------------------------------------------------------------------


def test_portaudio_input_overflow_is_counted() -> None:
    mic = MicrophoneCapture()
    mic._loop = None  # no event loop: the callback must still count
    indata = b"\x00\x00" * 16

    mic._callback(indata, 16, None, SimpleNamespace(input_overflow=True))
    mic._callback(indata, 16, None, SimpleNamespace(input_overflow=False))
    mic._callback(indata, 16, None, None)

    assert mic.overflow_count == 1
