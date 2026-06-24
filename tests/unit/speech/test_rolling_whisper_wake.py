"""Regression tests for the rolling-whisper wake backstop (custom phrases).

Forensic 2026-06-22 (branch feat/fast-boot-bootstrap): a user who sets a custom
wake word ("Hey Ruben") falls onto the ``stt_match`` path = RollingWhisperWake.
Two live symptoms — "riesige Verzoegerung" + "manchmal gar nicht":

- LATENCY (this file): ``detect`` used to ``await transcribe_pcm`` *inside* the
  chunk-consume loop, so while a (CPU "base") transcription ran for ~0.5-1 s the
  loop could not pull new chunks. They piled up (observed ``wsp_q=100``) and the
  detector trailed seconds behind live audio. The consumer must keep draining
  audio while a transcription is in flight, and each transcription must run on
  the *freshest* window, not a stale FIFO backlog.
"""
from __future__ import annotations

import asyncio

import numpy as np

from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.rolling_whisper_wake import (
    RollingWhisperWake,
    _wake_confirmed_unbiased,
)


# --- unbiased hallucination-confirm (forensic 2026-06-24) ---------------------
# A biased turbo can hallucinate the primed "Hey Ruben" onto a different "Hey X".
# An unbiased re-read of the same window reveals the truth; reject only on a clear
# competing wake-name so recall is preserved.


def test_confirm_accepts_unbiased_read_that_still_says_the_name() -> None:
    assert _wake_confirmed_unbiased("Hey Ruben", "ruben") is True


def test_confirm_accepts_mumbled_unbiased_near_name() -> None:
    # A real but quiet "Hey Ruben" the unbiased model heard as "Hey Ruf"
    # (ratio 0.5 vs "ruben") must still confirm — it is not a competing name.
    assert _wake_confirmed_unbiased("Hey, Ruf.", "ruben") is True


def test_confirm_accepts_garbled_unbiased_with_no_competing_name() -> None:
    # A quiet real wake the unbiased model garbled ("Drogen") names nothing else,
    # so it gets the benefit of the doubt (recall over a false reject).
    assert _wake_confirmed_unbiased("Drogen.", "ruben") is True


def test_confirm_rejects_unbiased_competing_hey_name() -> None:
    # "Hey Jarvis" / "Hey John" spoken -> biased hallucinates "Hey Ruben"; the
    # unbiased read names the real wake -> reject.
    assert _wake_confirmed_unbiased("Hey Jarvis", "ruben") is False
    assert _wake_confirmed_unbiased("Hey John.", "ruben") is False
    assert _wake_confirmed_unbiased("Hallo Computer", "ruben") is False


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    """A 100 ms @ 16 kHz chunk, loud enough to pass the rms/peak gates.

    ``marker`` is encoded in the (constant) sample value so a test STT can read
    back which slice of audio it was handed.
    """
    val = 12000 + (marker % 80) * 100  # stays < 32767, well above min_peak
    arr = np.full(n, val, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _GatedSTT:
    """``transcribe_pcm`` blocks on an event until the test releases it."""

    def __init__(self) -> None:
        self.calls = 0
        self.first_call_started = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> Transcript:
        self.calls += 1
        self.first_call_started.set()
        await self.release.wait()
        return Transcript(text="nothing here", language="de", confidence=0.9)


class _NeverMatch:
    """A matcher that never fires, so ``detect`` loops forever (no early yield)."""

    def search(self, text: str):  # noqa: ANN001 - duck-typed re.Pattern
        return None


class _PhraseSTT:
    """Always transcribes the same (matching) phrase."""

    def __init__(
        self,
        phrase: str,
        *,
        confidence: float = 0.9,
        no_speech_prob: float | None = None,
    ) -> None:
        self._phrase = phrase
        self._confidence = confidence
        self._no_speech_prob = no_speech_prob

    async def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> Transcript:
        segments = ()
        if self._no_speech_prob is not None:
            segments = ({"no_speech_prob": self._no_speech_prob},)
        return Transcript(
            text=self._phrase,
            language="de",
            confidence=self._confidence,
            segments=segments,
        )


class _BiasAwareSTT:
    """A biased CUDA wake-Whisper for the unbiased-confirm pass: returns
    ``biased_text`` on a normal (biased) call and ``unbiased_text`` when the
    detector re-reads with ``use_bias=False``."""

    def __init__(
        self, biased_text: str, unbiased_text: str, *, confidence: float = 0.9
    ) -> None:
        self._biased = biased_text
        self._unbiased = unbiased_text
        self._confidence = confidence
        self._initial_prompt = "Hey Ruben"  # biased -> confirm is eligible
        self._device = "cuda"               # fast -> confirm actually runs

    async def transcribe_pcm(
        self,
        pcm: bytes,
        sample_rate: int = 16000,
        language: str | None = None,
        use_bias: bool = True,
    ) -> Transcript:
        text = self._biased if use_bias else self._unbiased
        return Transcript(text=text, language="de", confidence=self._confidence)


async def _feed_until(src: asyncio.Queue, stop: asyncio.Event) -> None:
    i = 0
    while not stop.is_set():
        await src.put(_loud_chunk(i))
        i += 1
        await asyncio.sleep(0.002)


async def test_detect_yields_keyword_when_transcript_matches() -> None:
    """Refactor guard: a real wake (matching transcript) still yields."""
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("hey ruben"),
        pattern=re.compile(r"ruben"),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw == "ruben"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_accepts_short_live_wake_phrase_confidence() -> None:
    """Live two-word wake phrases can score below the generic 0.55 gate."""
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("Hey Ruben!", confidence=0.499, no_speech_prob=0.05734),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw.lower() == "hey ruben"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_accepts_real_world_low_confidence_wake() -> None:
    """The live base/cpu model scores a real, cleanly-heard wake well below 0.45.

    Forensic 2026-06-23 (running app, branch feat/fast-boot-bootstrap): the
    rolling-whisper wake rejected a CORRECTLY transcribed "Ruben." at confidence
    0.318 — and 141 more like it (142 rejects / 0 accepts in a whole evening). The
    old ``min_wake_confidence=0.45`` gate, built to suppress *prompt-bias*
    hallucinations, also kills genuine quiet wakes (the bias is now disabled, so
    the gate guards a problem that no longer exists). A matching transcript with
    real-speech ``no_speech_prob`` at the live-observed confidence must wake.
    """
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("Hey Ruben", confidence=0.318, no_speech_prob=0.05),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw.lower() == "hey ruben"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_rejects_matching_low_confidence_transcript() -> None:
    """A prompt-biased or mumbled window may hallucinate the wake phrase.

    Matching text alone is not enough: the wake backstop must fail closed when
    Whisper itself reports the transcript as unreliable.
    """
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("hey ruben", confidence=0.2),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        try:
            kw = await asyncio.wait_for(_first_keyword(), timeout=0.7)
        except asyncio.TimeoutError:
            kw = ""
        assert kw == ""
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_rejects_matching_high_no_speech_transcript() -> None:
    """faster-whisper exposes per-segment no_speech_prob for silence/noise.

    If that says the window probably contains no speech, a text match must not
    wake the assistant.
    """
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("hey ruben", confidence=0.9, no_speech_prob=0.95),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        try:
            kw = await asyncio.wait_for(_first_keyword(), timeout=0.7)
        except asyncio.TimeoutError:
            kw = ""
        assert kw == ""
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_rejects_biased_hallucination_via_unbiased_confirm() -> None:
    """A biased turbo that prints 'hey ruben' for a spoken 'hey jarvis' is dropped.

    The unbiased re-read (use_bias=False) names the real wake -> reject. This is
    the false-wake the user hit (2026-06-24): "Hey Jarvis"/"Hey John" fired the
    "Hey Ruben" wake because the bias collapsed every "Hey X" onto the primed name.
    """
    import re

    wake = RollingWhisperWake(
        _BiasAwareSTT(biased_text="hey ruben", unbiased_text="hey jarvis"),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        try:
            kw = await asyncio.wait_for(_first_keyword(), timeout=0.7)
        except TimeoutError:
            kw = ""
        assert kw == "", f"hallucinated wake was not rejected: {kw!r}"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_confirms_real_wake_when_unbiased_agrees() -> None:
    """A genuine 'hey ruben' the unbiased read also supports still fires."""
    import re

    wake = RollingWhisperWake(
        _BiasAwareSTT(biased_text="hey ruben", unbiased_text="hey ruben"),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw.lower() == "hey ruben"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


def test_default_poll_interval_is_responsive() -> None:
    """The poll cadence sets the idle gap before a fresh window is transcribed.

    Forensic 2026-06-24: the wake-reaction latency is dominated by the ~1.4s CPU
    base-Whisper transcription itself (beam_size has no measurable effect — the
    cost is the encoder pass over the window, not the decoder). The poll interval
    is the only *artificial* idle gap on the cadence, so keep it small. This locks
    in the faster default and guards against a regression back to the sluggish
    0.3-0.5s cadence.
    """
    wake = RollingWhisperWake(_PhraseSTT("hi"), save_debug_wavs=False)
    assert wake._poll_interval_s <= 0.15


async def test_gated_out_windows_are_counted_for_observability() -> None:
    """A too-quiet window is dropped *before* Whisper — and must be observable.

    Forensic 2026-06-24: the rms/peak gate skips the transcription with a bare
    ``continue`` and no log, so a user whose mic is too quiet sees "it never
    fires" with zero evidence in the log (both deep-dive passes flagged this as
    the blind spot). The drop must bump a counter the heartbeat can surface, so a
    future "doesn't fire" turn is diagnosable (high gated-out = mic too quiet vs.
    low gated-out + high rejects = model/confidence).
    """
    stt = _PhraseSTT("should not be called")
    wake = RollingWhisperWake(
        stt,
        pattern=_NeverMatch(),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        # Floors well above the (near-silent) quiet chunks below, so every poll
        # is gated out pre-Whisper.
        min_rms=0.5,
        min_peak=0.5,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _quiet_feeder() -> None:
        # ~ -60 dBFS noise: passes the >=1s buffer fill, fails the rms/peak gate.
        i = 0
        while not stop_feed.is_set():
            arr = np.full(1600, 30, dtype=np.int16)  # tiny amplitude
            await src.put(
                AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=i)
            )
            i += 1
            await asyncio.sleep(0.002)

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _drive() -> None:
        async for _kw in wake.detect(_iter()):
            pass

    feeder = asyncio.create_task(_quiet_feeder())
    detect_task = asyncio.create_task(_drive())
    try:
        await asyncio.sleep(0.4)
        assert wake._gated_out >= 1, "quiet windows were not counted as gated-out"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        detect_task.cancel()
        for t in (feeder, detect_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass


async def test_consumer_keeps_draining_while_transcription_is_in_flight() -> None:
    stt = _GatedSTT()
    wake = RollingWhisperWake(
        stt,
        pattern=_NeverMatch(),
        poll_interval_s=0.02,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    consumed = 0
    stop_feed = asyncio.Event()

    async def _iter():
        nonlocal consumed
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            consumed += 1
            yield chunk

    async def _feeder() -> None:
        # Emulate a live mic stream: chunks keep arriving regardless of how slow
        # the STT is. A real mic never pauses for the transcriber.
        i = 0
        while not stop_feed.is_set():
            await src.put(_loud_chunk(i))
            i += 1
            await asyncio.sleep(0.002)

    async def _drive() -> None:
        async for _kw in wake.detect(_iter()):
            pass

    feeder = asyncio.create_task(_feeder())
    detect_task = asyncio.create_task(_drive())
    try:
        # Wait until the first (now-blocked) transcription is in flight.
        await asyncio.wait_for(stt.first_call_started.wait(), timeout=3.0)
        consumed_when_blocked = consumed

        # The mic keeps streaming while the transcription is stuck.
        await asyncio.sleep(0.3)
        progressed = consumed - consumed_when_blocked

        # Coupled (buggy) code is stuck on ``await transcribe`` and cannot pull
        # from the iterator -> progressed ~= 0 (the queue just backs up, exactly
        # the observed wsp_q=100). Decoupled (fixed) code keeps draining audio
        # while the transcription runs -> progressed is large.
        assert progressed >= 30, (
            f"consumer blocked on in-flight transcription: progressed={progressed} "
            f"chunks while a transcription was stuck (consumed={consumed}, "
            f"was {consumed_when_blocked} when the transcribe started)"
        )
        # And it must not fire overlapping transcriptions while one is in flight.
        assert stt.calls == 1, f"overlapping transcriptions in flight: {stt.calls}"
    finally:
        stop_feed.set()
        stt.release.set()
        await src.put(None)
        feeder.cancel()
        detect_task.cancel()
        for t in (feeder, detect_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
