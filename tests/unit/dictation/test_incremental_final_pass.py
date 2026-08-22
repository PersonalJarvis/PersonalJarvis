"""The final pass keeps up with the recording instead of starting after it.

What this guards (live history 2026-08-14 → 08-22, 202 dictations): the wait
after key release grew with the recording — 0.66 s median for a short one,
2.2 s for 25–50 s, 4.6 s past 50 s — because the final-quality windows were
read only after release, one after another. Now a window is closed and read
the moment the recording has grown past it, while the user is still talking,
and whatever is left at release is read concurrently when the provider allows
it. The windows themselves are the ones the one-shot pass would have cut; only
WHEN they are read moved.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted
from jarvis.dictation.segment import next_quality_window, quality_windows
from jarvis.speech.pipeline import SpeechPipeline
from jarvis.speech.stt_fallback import FallbackSTT

# 16 kHz mono int16 — the capture contract every dictation records under.
BYTES_PER_SECOND = 16_000 * 2
# Ten tokens per answer: well above the truncation floor for any window these
# tests cut, so the guard that re-reads a short transcript never fires here.
_ANSWERS = [
    "alpha one two three four five six seven eight nine",
    "bravo one two three four five six seven eight nine",
    "charlie one two three four five six seven eight nine",
    "delta one two three four five six seven eight nine",
    "echo one two three four five six seven eight nine",
]


def _voiced(seconds: float) -> bytes:
    """Speech-loud audio; sample 0x2211 = 8721 sits far above every silence floor."""
    return b"\x11\x22" * int(16_000 * seconds)


@dataclass
class _Transcript:
    text: str
    language: str = "en"


class _CountingSTT:
    """A provider answering a fixed script, measuring how many calls overlap."""

    supports_streaming = False

    def __init__(
        self, *, delay_s: float = 0.0, concurrent: bool = False, script: list[str] | None = None
    ) -> None:
        self._script = list(script or _ANSWERS)
        self._delay_s = delay_s
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.call_times: list[float] = []
        if concurrent:
            self.supports_concurrent_requests = True

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        self.calls += 1
        self.call_times.append(time.monotonic())
        index = min(self.calls, len(self._script)) - 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            return _Transcript(text=self._script[index])
        finally:
            self.in_flight -= 1


class _Chunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.timestamp_ns = 0


class _FakeMic:
    """Prepared audio delivered in slices, then it waits to be cancelled."""

    def __init__(self, pcm: bytes, *, slice_s: float = 0.0, pace_s: float = 0.0) -> None:
        self._pcm = pcm
        self._slice = int(slice_s * BYTES_PER_SECOND) or len(pcm)
        self._pace_s = pace_s
        self.delivered = asyncio.Event()

    async def stream(self):  # noqa: ANN201 — an async generator of chunks
        for offset in range(0, len(self._pcm), self._slice):
            yield _Chunk(self._pcm[offset : offset + self._slice])
            if self._pace_s:
                await asyncio.sleep(self._pace_s)
        self.delivered.set()
        await asyncio.sleep(3600)


class _NullCapture:
    def __init__(self, source: Any) -> None:
        self._source = source

    async def __aenter__(self) -> Any:
        return self._source

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _session_pipeline(stt: Any, mic: _FakeMic, **cfg: Any):
    """A pipeline wired for exactly one ``_dictation_session`` run."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = DictationConfig(
        history_enabled=False,
        polish=False,
        # Five-second windows — the smallest the schema allows — so a short
        # synthetic recording holds several of them.
        final_window_seconds=5.0,
        final_overlap_seconds=0.5,
        **cfg,
    )
    pipe._dictation_target = "chat"
    pipe._dictation_completion_published = False
    pipe._dictation_max_s = 60.0
    pipe._dictation_stt_instance = stt
    pipe._stt_final_timeout_s = 8.0
    pipe._hangup_event = asyncio.Event()
    pipe._dictation_stop_event = asyncio.Event()
    events: list[object] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._publish_event_soon = events.append  # type: ignore[assignment]
    pipe._capture_dictation_input = lambda: _NullCapture(mic)  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: SimpleNamespace(  # type: ignore[assignment]
        status="inserted", detail="", method="clipboard+ctrl_v"
    )

    async def _stop_live(task, **_kwargs):  # noqa: ANN001, ANN202
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    pipe._stop_ptt_live_transcription = _stop_live  # type: ignore[assignment]
    return pipe, events


def _completed(events: list[object]) -> DictationCompleted:
    return next(e for e in events if isinstance(e, DictationCompleted))


def _audit(events: list[object], key: str) -> int:
    token = next(t for t in _completed(events).stt_audit if t.startswith(key + ":"))
    return int(token.split(":", 1)[1])


@pytest.fixture
def _no_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """No on-device engine and no cloud preview budget: every provider call in
    these tests belongs to the final pass."""
    import jarvis.dictation.local_preview as local_preview
    import jarvis.dictation.preview_budget as preview_budget

    monkeypatch.setattr(local_preview, "local_preview", lambda: None)
    monkeypatch.setattr(
        preview_budget,
        "preview_budget",
        lambda: SimpleNamespace(try_spend=lambda: False),
    )


# --------------------------------------------------------------------------
# Windows are read while the user is still speaking
# --------------------------------------------------------------------------


async def test_windows_are_read_before_release_and_only_the_tail_after(
    _no_preview: None,
) -> None:
    # Twelve seconds of speech arriving in half-second slices while the probe
    # ticks every 20 ms: the 5 s windows at 0 s and ~4.5 s are complete long
    # before the key is released, so they are read then; only the ~3 s tail
    # is left for release.
    stt = _CountingSTT()
    mic = _FakeMic(_voiced(12.0), slice_s=0.5, pace_s=0.01)
    pipe, events = _session_pipeline(
        stt, mic, segment_seconds=0.0, partial_interval_s=0.02
    )

    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.wait_for(mic.delivered.wait(), timeout=10)
    # A few probe ticks so the last prefetchable window is launched and read.
    await asyncio.sleep(0.2)
    calls_before_release = stt.calls
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)

    assert calls_before_release >= 2, "prefetched windows were not read before release"
    prefetched = _audit(events, "final_windows_prefetched")
    # At least two windows were closed while the recording was still growing
    # (the quietest-point search may cut a window short of its nominal length,
    # so the exact count depends on the audio); exactly ONE window — the open
    # tail — was left for release, and every window was read exactly once.
    assert prefetched >= 2
    assert _audit(events, "final_windows") == prefetched + 1
    assert stt.calls == prefetched + 1
    # The windows are assembled in recording order, whatever order they came
    # back in.
    assert _completed(events).raw_text.split()[0] == "alpha"
    assert "charlie" in _completed(events).raw_text
    assert _completed(events).raw_text.index("bravo") < _completed(events).raw_text.index(
        "charlie"
    )


async def test_a_short_recording_is_one_window_read_at_release(_no_preview: None) -> None:
    stt = _CountingSTT()
    mic = _FakeMic(_voiced(3.0))
    pipe, events = _session_pipeline(stt, mic, segment_seconds=0.0, partial_interval_s=0.0)

    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.sleep(0)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)

    assert stt.calls == 1
    assert _audit(events, "final_windows_prefetched") == 0
    assert _audit(events, "final_windows") == 1
    assert _completed(events).raw_text.startswith("alpha")


# --------------------------------------------------------------------------
# What is left at release is read together, when the provider allows it
# --------------------------------------------------------------------------


async def _release_with_windows_left(stt: _CountingSTT) -> list[object]:
    # No probe at all (interval 0): nothing is prefetched, so every window of
    # a 14 s recording is still to be read when the key is released — the
    # shape of a slow or absent probe, and the one that used to serialise.
    mic = _FakeMic(_voiced(14.0))
    pipe, events = _session_pipeline(stt, mic, segment_seconds=0.0, partial_interval_s=0.0)
    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.sleep(0)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)
    return events


async def test_a_concurrent_provider_reads_the_remaining_windows_side_by_side(
    _no_preview: None,
) -> None:
    stt = _CountingSTT(delay_s=0.15, concurrent=True)

    events = await _release_with_windows_left(stt)

    assert stt.calls >= 3
    assert stt.max_in_flight >= 2
    # Side by side: the whole pass took about one call, not one per window.
    assert _audit(events, "release_wait_ms") < 400
    text = _completed(events).raw_text
    assert text.index("alpha") < text.index("bravo") < text.index("charlie")


async def test_a_native_engine_never_sees_two_reads_at_once(_no_preview: None) -> None:
    # No capability flag: the provider is treated as a native engine (AP-24)
    # and its windows are read strictly one after another.
    stt = _CountingSTT(delay_s=0.05)

    events = await _release_with_windows_left(stt)

    assert stt.calls >= 3
    assert stt.max_in_flight == 1
    text = _completed(events).raw_text
    assert text.index("alpha") < text.index("bravo") < text.index("charlie")


# --------------------------------------------------------------------------
# The fallback chain keeps a native alternate safe under concurrent callers
# --------------------------------------------------------------------------


async def test_fallback_chain_serialises_a_provider_without_the_capability() -> None:
    native = _CountingSTT(delay_s=0.05)
    chain = FallbackSTT(native, alternates=[], build=lambda name: None, primary_name="native")

    assert chain.supports_concurrent_requests is False
    await asyncio.gather(*(chain.transcribe_pcm(b"\x11\x22" * 16_000) for _ in range(3)))

    assert native.calls == 3
    assert native.max_in_flight == 1


async def test_fallback_chain_lets_a_capable_provider_run_concurrently() -> None:
    cloud = _CountingSTT(delay_s=0.05, concurrent=True)
    chain = FallbackSTT(cloud, alternates=[], build=lambda name: None, primary_name="cloud")

    assert chain.supports_concurrent_requests is True
    await asyncio.gather(*(chain.transcribe_pcm(b"\x11\x22" * 16_000) for _ in range(3)))

    assert cloud.max_in_flight == 3


# --------------------------------------------------------------------------
# The incremental cut is the one-shot cut
# --------------------------------------------------------------------------


def test_incremental_windows_equal_the_one_shot_windows() -> None:
    # Speech with quiet dips every ~4 s, so the quietest-point search has
    # somewhere to land and the incremental step must find the SAME spot the
    # one-shot pass does from the finished recording.
    pieces = []
    for _ in range(9):
        pieces.append(b"\x11\x22" * int(16_000 * 3.7))
        pieces.append(b"\x05\x00" * int(16_000 * 0.3))
    pcm = b"".join(pieces)
    window_bytes = 5 * BYTES_PER_SECOND
    overlap_bytes = BYTES_PER_SECOND // 2

    one_shot = quality_windows(pcm, window_bytes=window_bytes, overlap_bytes=overlap_bytes)

    incremental: list[tuple[int, int]] = []
    start = 0
    # Grow the "recording" in half-second steps and cut whenever a full
    # window lies past ``start`` — exactly what the probe does per tick.
    for total in range(BYTES_PER_SECOND // 2, len(pcm) + 1, BYTES_PER_SECOND // 2):
        while total - start > window_bytes:
            scan = pcm[start : start + window_bytes]
            window, start = next_quality_window(
                scan,
                start=start,
                total=total,
                window_bytes=window_bytes,
                overlap_bytes=overlap_bytes,
            )
            incremental.append(window)
    tail = quality_windows(pcm[start:], window_bytes=window_bytes, overlap_bytes=overlap_bytes)
    incremental.extend((start + a, start + b) for a, b in tail)

    assert incremental == one_shot
    assert len(one_shot) >= 6
