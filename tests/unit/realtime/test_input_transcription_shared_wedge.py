"""Sharing one recognizer must not be mistaken for a wedged one.

The recognizer cache is process-wide, so "the engine is busy" has two very
different meanings. Usually it means ANOTHER session is inside a perfectly
healthy recognition — the cache doing its job. Rarely it means the guard was
left set by a holder that is never coming back, which is the AP-24 wedge the
self-heal exists for.

Reading the flag alone conflated them: the second session counted three skips,
evicted the shared engine and called ``recover()`` on the backend WHILE the
first session was still inside an inference. That aborted a live recognition,
made both sessions re-pay a multi-second model load, and left the weights
briefly resident twice — a GPU-sized bill for a cache hit.

So the tests below pin the discriminator: wall-clock against the holder's own
timeout bound, never the flag on its own.
"""

from __future__ import annotations

import math
import struct
import time
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.config import STTConfig
from jarvis.realtime import input_transcription as module
from jarvis.realtime.input_transcription import LocalInputTranscriber, RecognizerBusy

RATE = 24_000
CHUNK_SAMPLES = 480  # 20 ms


def _loud(samples: int = CHUNK_SAMPLES) -> bytes:
    return b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * 180 * n / RATE)))
        for n in range(samples)
    )


SEGMENT = _loud() * 40


class TranscribeBusy(RuntimeError):
    """The class NAME the local Whisper provider raises for its own guard.

    Recognized by shape, never by import, so the realtime path stays importable
    without the local-voice extra. The name is load-bearing here.
    """


class _SharedEngine:
    """A recognizer stand-in that counts every teardown it is put through."""

    native_sample_rate = 16_000

    def __init__(self) -> None:
        self.recover_calls = 0
        self.transcribe_calls = 0

    async def transcribe(self, chunks: Any) -> Any:
        self.transcribe_calls += 1
        async for _ in chunks:
            pass
        return SimpleNamespace(text="heard it")

    def recover(self) -> None:
        self.recover_calls += 1


@pytest.fixture(autouse=True)
def _clean_recognizer_cache():
    module._reset_for_tests()
    yield
    module._reset_for_tests()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> _SharedEngine:
    """One engine behind the configured STT, so both sessions share it."""
    built = _SharedEngine()
    config = type("_Config", (), {"stt": STTConfig(provider="groq-api", language="en")})

    monkeypatch.setattr(
        "jarvis.core.config.load_config", lambda *a, **k: config, raising=True
    )
    monkeypatch.setattr(
        "jarvis.plugins.stt.build_stt_from_config",
        lambda *a, **k: built,
        raising=True,
    )
    return built


async def _two_sessions_on_one_engine() -> tuple[
    LocalInputTranscriber, LocalInputTranscriber
]:
    first = LocalInputTranscriber(sample_rate=RATE, language="de")
    second = LocalInputTranscriber(sample_rate=RATE, language="de")
    assert await first.warm() is True
    assert await second.warm() is True
    assert first._shared is second._shared is not None
    return first, second


# -- (a) contention is not a wedge -------------------------------------


async def test_a_busy_shared_engine_is_never_torn_down_under_its_user(
    engine: _SharedEngine,
) -> None:
    """The headline regression: one session's cache hit killed the other's turn."""
    holder, other = await _two_sessions_on_one_engine()
    shared = holder._shared
    assert shared is not None

    # Session A is two seconds into a recognition bounded well above that.
    holder._set_recognition_busy(True, bound_s=30.0)
    shared.busy_since = time.monotonic() - 2.0

    # Session B keeps arriving and keeps being skipped — far past the streak
    # that used to trigger a rebuild.
    for _ in range(module._RECOVER_AFTER_BUSY + 2):
        with pytest.raises(RecognizerBusy):
            await other.transcribe_audio(SEGMENT, sample_rate=RATE)

    assert engine.recover_calls == 0
    assert other._consecutive_busy == 0
    # The healthy engine stays cached and stays attached to both sessions.
    assert len(module._stt_cache) == 1
    assert other._shared is shared
    assert other._stt is engine
    assert shared.busy is True  # A's in-flight call was left alone

    await holder.close()
    await other.close()


async def test_the_other_session_is_heard_again_once_the_holder_returns(
    engine: _SharedEngine,
) -> None:
    """Skipping is a pause, not a deafness: no rebuild, so no cold start."""
    holder, other = await _two_sessions_on_one_engine()

    holder._set_recognition_busy(True, bound_s=30.0)
    for _ in range(module._RECOVER_AFTER_BUSY):
        with pytest.raises(RecognizerBusy):
            await other.transcribe_audio(SEGMENT, sample_rate=RATE)
    holder._set_recognition_busy(False, bound_s=0.0)

    assert await other.transcribe_audio(SEGMENT, sample_rate=RATE) == "heard it"
    assert engine.recover_calls == 0
    assert engine.transcribe_calls == 1

    await holder.close()
    await other.close()


# -- (b) an overdue guard IS a wedge -----------------------------------


async def test_a_guard_held_past_every_bound_rebuilds_the_engine(
    engine: _SharedEngine,
) -> None:
    """A holder that will never clear the flag must not deafen the process."""
    holder, other = await _two_sessions_on_one_engine()
    shared = holder._shared
    assert shared is not None

    holder._set_recognition_busy(True, bound_s=5.0)
    shared.busy_since = time.monotonic() - (5.0 + module._BUSY_WEDGE_GRACE_S + 1.0)

    for _ in range(module._RECOVER_AFTER_BUSY):
        with pytest.raises(RecognizerBusy):
            await other.transcribe_audio(SEGMENT, sample_rate=RATE)

    assert engine.recover_calls == 1
    assert not module._stt_cache
    assert other._stt is None
    assert other._shared is None

    await holder.close()
    await other.close()


async def test_the_wedge_threshold_scales_with_the_holders_own_timeout(
    engine: _SharedEngine,
) -> None:
    """A long segment's recognition is allowed to take long — nothing else is."""
    holder, other = await _two_sessions_on_one_engine()
    shared = holder._shared
    assert shared is not None

    # Exactly the span of a wedge under a five-second bound, but this holder
    # runs under a sixty-second one, so it is still healthy.
    holder._set_recognition_busy(True, bound_s=60.0)
    shared.busy_since = time.monotonic() - (5.0 + module._BUSY_WEDGE_GRACE_S + 1.0)

    for _ in range(module._RECOVER_AFTER_BUSY):
        with pytest.raises(RecognizerBusy):
            await other.transcribe_audio(SEGMENT, sample_rate=RATE)

    assert engine.recover_calls == 0
    assert len(module._stt_cache) == 1

    await holder.close()
    await other.close()


async def test_a_recovery_from_another_cause_still_spares_a_live_inference(
    engine: _SharedEngine,
) -> None:
    """Eviction is safe for everyone; ``recover()`` is not.

    A rebuild triggered by this session's OWN failures must still not tear the
    model down under a holder it cannot see. Retiring the cache entry is enough:
    the next utterance builds a fresh engine either way.
    """
    holder, other = await _two_sessions_on_one_engine()

    holder._set_recognition_busy(True, bound_s=30.0)
    await other._recover_recognizer("2 consecutive failures (ValueError)")

    assert engine.recover_calls == 0
    assert not module._stt_cache  # nobody is handed the suspect engine again
    assert other._stt is None

    await holder.close()
    await other.close()


# -- (c) the single-session wedge path is unchanged --------------------


class _StuckEngine:
    """One session's own engine with an abandoned worker thread inside it.

    It reports itself busy — our guard is clear, so nobody legitimate is in
    there — until ``recover()`` rebuilds it. That report needs no wall-clock
    proof and must keep healing after the historical streak.
    """

    native_sample_rate = 16_000

    def __init__(self) -> None:
        self.wedged = True
        self.recover_calls = 0

    async def transcribe(self, chunks: Any) -> Any:
        async for _ in chunks:
            pass
        if self.wedged:
            raise TranscribeBusy("a transcription is already in flight on this model")
        return SimpleNamespace(text="recovered engine works")

    def recover(self) -> None:
        self.recover_calls += 1
        self.wedged = False


async def test_a_providers_own_busy_report_still_rebuilds_after_the_streak() -> None:
    stuck = _StuckEngine()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stuck)
    assert await transcriber.warm() is True

    for _ in range(module._RECOVER_AFTER_BUSY):
        with pytest.raises(TranscribeBusy):
            await transcriber.transcribe_audio(SEGMENT, sample_rate=RATE)

    assert stuck.recover_calls == 1
    # ...and the next utterance is heard again.
    assert (
        await transcriber.transcribe_audio(SEGMENT, sample_rate=RATE)
        == "recovered engine works"
    )
    await transcriber.close()
