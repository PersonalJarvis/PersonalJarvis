"""Local user-speech transcription for transports that do not supply it.

ChatGPT-Live sends assistant transcripts only, so without these events the
provider is deaf to Jarvis: the bar stays blank, the indicators never move,
and every transcript-driven integration (delegate, wiki, project files,
hang-up phrase) sits idle while the model happily talks.

On that transport these events are also the GROUNDING evidence — the adapter
answers only when a fresh local ``speech_started`` proves a human spoke — so
the tests below pin the SECOND and THIRD utterance of one call, not just the
first: every historical "works once, then only listens" report is a turn-2
failure that a single-utterance test cannot see.
"""

from __future__ import annotations

import asyncio
import math
import struct
from types import SimpleNamespace

import pytest

from jarvis.realtime import input_transcription as module
from jarvis.realtime.input_transcription import (
    LocalInputTranscriber,
    RecognizerBusy,
)

RATE = 24_000
CHUNK_SAMPLES = 480  # 20 ms
RECOGNIZER_RATE = 16_000


def _tone(amplitude: int, samples: int = CHUNK_SAMPLES) -> bytes:
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 180 * n / RATE)))
        for n in range(samples)
    )


def _loud(samples: int = CHUNK_SAMPLES) -> bytes:
    return _tone(9000, samples)


def _quiet(samples: int = CHUNK_SAMPLES) -> bytes:
    return b"\x00\x00" * samples


class TranscribeBusy(RuntimeError):
    """The class NAME the local Whisper provider raises when its engine is busy.

    The transcriber recognizes it by shape, never by importing the provider, so
    the realtime path stays importable on a base install without the
    local-voice extra. The name is therefore load-bearing here.
    """


class _FakeSTT:
    def __init__(self, text: str = "hallo welt") -> None:
        self.text = text
        self.calls = 0
        self.seconds: list[float] = []
        self.rates: list[int] = []

    async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
        self.calls += 1
        total = 0
        rate = RECOGNIZER_RATE
        async for chunk in audio:
            total += len(chunk.pcm)
            rate = chunk.sample_rate
        self.rates.append(rate)
        self.seconds.append(total / 2 / rate)
        return SimpleNamespace(text=self.text)


class _CountingSTT:
    """Returns a distinct transcript per call, so turn N is identifiable."""

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
        self.calls += 1
        async for _ in audio:
            pass
        return SimpleNamespace(text=f"utterance {self.calls}")


_EVENT_WAIT_S = 5.0


async def _drain(transcriber: LocalInputTranscriber, expected: int):
    events = []
    for _ in range(expected):
        events.append(
            await asyncio.wait_for(transcriber.next_event(), timeout=_EVENT_WAIT_S)
        )
    return events


def _speak(
    transcriber: LocalInputTranscriber,
    *,
    chunk: bytes | None = None,
    speech_chunks: int = 40,
    silence_chunks: int = 40,
) -> None:
    """Feed one complete utterance: speech, then the silence that closes it."""
    voice = _loud() if chunk is None else chunk
    for _ in range(speech_chunks):
        transcriber.feed(voice, RATE)
    for _ in range(silence_chunks):
        transcriber.feed(_quiet(), RATE)


@pytest.mark.asyncio
async def test_speech_then_silence_yields_speech_start_and_transcript() -> None:
    stt = _FakeSTT("Ben, durchsuche mein Wiki.")
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    _speak(transcriber)
    events = await _drain(transcriber, 2)

    assert events[0].kind == module.SPEECH_STARTED
    assert events[1].kind == module.TRANSCRIPT
    assert events[1].text == "Ben, durchsuche mein Wiki."
    assert events[1].is_final is True
    await transcriber.close()


@pytest.mark.asyncio
async def test_every_utterance_of_one_call_produces_its_own_transcript() -> None:
    """Turn 2 and turn 3 are the whole bug class.

    Every "it works once and then only listens" report is this: a recognizer
    wedged by turn 1, a one-shot task, or a flag latched on the first final
    transcript. A single-utterance test cannot see any of them.
    """
    stt = _CountingSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    transcripts = []
    for _ in range(3):
        _speak(transcriber)
        started, result = await _drain(transcriber, 2)
        assert started.kind == module.SPEECH_STARTED
        assert result.kind == module.TRANSCRIPT, result
        transcripts.append(result.text)

    assert transcripts == ["utterance 1", "utterance 2", "utterance 3"]
    assert stt.calls == 3
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_blip_of_noise_never_becomes_a_transcript() -> None:
    stt = _FakeSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    _speak(transcriber, speech_chunks=8)  # 160 ms - too short to be speech
    events = await _drain(transcriber, 2)

    assert events[0].kind == module.SPEECH_STARTED
    # It must not become a turn - but it must not vanish either: the boundary
    # tells the transport that the turn opened by ``speech_started`` is over.
    assert events[1].kind == module.SPEECH_DISCARDED
    assert events[1].text == ""
    assert events[1].voiced_ms < module._MIN_VOICED_MS  # noqa: SLF001
    assert stt.calls == 0
    await transcriber.close()


@pytest.mark.asyncio
async def test_the_utterance_carries_its_own_onset() -> None:
    """Pre-roll matters: without it the recognizer receives a sentence whose
    first syllable was already spent proving that speech had started."""
    stt = _FakeSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    _speak(transcriber, speech_chunks=50)  # 1.0 s of speech
    await _drain(transcriber, 2)

    # Speech + pre-roll + the trailing silence that closed it.
    assert stt.seconds and stt.seconds[0] > 1.0
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_failing_recognizer_never_kills_the_call() -> None:
    class _Broken:
        async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
            async for _ in audio:
                pass
            raise RuntimeError("recognizer exploded")

    transcriber = LocalInputTranscriber(
        sample_rate=RATE, stt_factory=lambda: _Broken()
    )
    _speak(transcriber)

    first = await asyncio.wait_for(transcriber.next_event(), timeout=_EVENT_WAIT_S)
    assert first.kind == module.SPEECH_STARTED
    # The provider receives one explicit failure boundary so it can promote
    # its own energy-gated preview; closing still completes cleanly.
    failed = await asyncio.wait_for(transcriber.next_event(), timeout=_EVENT_WAIT_S)
    assert failed.kind == module.TRANSCRIPT_FAILED
    await transcriber.close()
    assert (
        await asyncio.wait_for(transcriber.next_event(), timeout=_EVENT_WAIT_S) is None
    )


@pytest.mark.asyncio
async def test_silence_never_vouches_for_a_server_transcript() -> None:
    """The energy gate the provider asks before trusting a server-side user
    transcript. ChatGPT-Live invented "[exhale]" and "a_lee pixelated image"
    while the user sat silent, and each was recorded as something they said."""
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    assert transcriber.speech_recently() is False  # nothing fed yet
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)
    assert transcriber.speech_recently() is False
    await transcriber.close()


@pytest.mark.asyncio
async def test_real_speech_vouches_for_a_server_transcript() -> None:
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    for _ in range(20):  # mid-utterance
        transcriber.feed(_loud(), RATE)
    assert transcriber.speech_recently() is True

    for _ in range(40):  # the silence that closes it
        transcriber.feed(_quiet(), RATE)
    # The far end transcribes with its own latency, so a genuine transcript
    # arrives shortly AFTER the audio stopped.
    assert transcriber.speech_recently() is True
    # ...but the vouching expires, so a transcript arriving much later in the
    # silence is no longer covered by it.
    await asyncio.sleep(0.05)
    assert transcriber.speech_recently(grace_ms=10) is False
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_cough_does_not_vouch_for_a_server_transcript() -> None:
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    _speak(transcriber, speech_chunks=8)  # 160 ms - too short to be an utterance
    assert transcriber.speech_recently() is False
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_failed_recognizer_announces_itself() -> None:
    """Silence would strand the turn: the provider needs to know it must fall
    back to the far end's transcript (AP-30 - never fail without saying so)."""

    class _Broken:
        async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
            async for _ in audio:
                pass
            raise RuntimeError("recognizer exploded")

    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _Broken())
    _speak(transcriber)

    events = await _drain(transcriber, 2)
    assert events[0].kind == module.SPEECH_STARTED
    assert events[1].kind == module.TRANSCRIPT_FAILED
    await transcriber.close()


@pytest.mark.asyncio
async def test_an_empty_result_also_announces_itself() -> None:
    stt = _FakeSTT("   ")
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)
    _speak(transcriber)

    events = await _drain(transcriber, 2)
    assert events[1].kind == module.TRANSCRIPT_FAILED
    await transcriber.close()


# -- sample rate ------------------------------------------------------------


class _StrictRateSTT:
    """A recognizer that rejects any rate but its own.

    Not a contrived fake: the local Whisper provider raises
    ``ValueError("Expected 16 kHz, got 24000 Hz")`` for exactly this, so before
    the conversion below every local-STT user got zero transcripts on every
    single turn while cloud users saw nothing wrong.
    """

    def __init__(self, native_rate: int = RECOGNIZER_RATE) -> None:
        self.native_sample_rate = native_rate
        self.seen_rates: list[int] = []

    async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
        async for chunk in audio:
            self.seen_rates.append(chunk.sample_rate)
            if chunk.sample_rate != self.native_sample_rate:
                raise ValueError(
                    f"Expected {self.native_sample_rate} Hz, "
                    f"got {chunk.sample_rate} Hz"
                )
        return SimpleNamespace(text="resampled fine")


@pytest.mark.asyncio
async def test_capture_audio_is_converted_to_the_recognizer_rate() -> None:
    stt = _StrictRateSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    _speak(transcriber)
    events = await _drain(transcriber, 2)

    assert events[1].kind == module.TRANSCRIPT, events[1]
    assert events[1].text == "resampled fine"
    assert stt.seen_rates == [RECOGNIZER_RATE]
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_provider_declaring_its_own_rate_is_honoured() -> None:
    """16 kHz is the documented fallback, not an assumption baked into the path."""
    stt = _StrictRateSTT(native_rate=8_000)
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    _speak(transcriber)
    events = await _drain(transcriber, 2)

    assert events[1].kind == module.TRANSCRIPT, events[1]
    assert stt.seen_rates == [8_000]
    await transcriber.close()


@pytest.mark.asyncio
async def test_output_recovery_is_converted_to_the_recognizer_rate_too() -> None:
    stt = _StrictRateSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    text = await transcriber.transcribe_audio(_loud() * 40, sample_rate=RATE)

    assert text == "resampled fine"
    assert stt.seen_rates == [RECOGNIZER_RATE]
    await transcriber.close()


# -- AP-24: a wedged engine must be rebuilt, never re-polled forever ---------


class _WedgingSTT:
    """Models a native engine whose worker thread cannot be cancelled.

    The first call enters the engine and never returns. When the caller's
    timeout cancels the await, the ENGINE stays occupied — that is the whole
    AP-24 trap: a timeout bounds the wait, it does not recover the engine. Every
    later call therefore reports itself busy until ``recover()`` rebuilds it.
    """

    def __init__(self) -> None:
        self.busy = False
        self.wedged = True
        self.recover_calls = 0
        self.transcribe_calls = 0

    async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
        self.transcribe_calls += 1
        async for _ in audio:
            pass
        if self.busy:
            raise TranscribeBusy("a transcription is already in flight on this model")
        self.busy = True
        if self.wedged:
            # Cancelling this await leaves ``busy`` set, exactly as an abandoned
            # ctranslate2 worker thread leaves the real engine occupied.
            await asyncio.sleep(3600)
        self.busy = False
        return SimpleNamespace(text="recovered engine works")

    def recover(self) -> None:
        self.recover_calls += 1
        self.busy = False
        self.wedged = False


@pytest.mark.asyncio
async def test_a_cancelled_recovery_does_not_deafen_the_rest_of_the_call() -> None:
    """The headline regression: works once, then only listens.

    A missing assistant transcript sends the adapter into ``transcribe_audio``
    under its own timeout. When that timeout cancels the await, the native
    engine keeps running — so every later USER utterance used to fail,
    permanently, with no attempt to rebuild.
    """
    stt = _WedgingSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)
    assert await transcriber.warm() is True

    # The adapter's bounded output-transcript recovery, cancelled mid-flight.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            transcriber.transcribe_audio(_loud() * 40, sample_rate=RATE),
            timeout=0.05,
        )
    assert stt.busy is True  # the engine is still occupied

    # The engine reports itself busy for a bounded streak, and is then rebuilt
    # instead of being re-polled forever.
    kinds: list[str] = []
    for _ in range(module._RECOVER_AFTER_BUSY):  # noqa: SLF001
        _speak(transcriber)
        started, result = await _drain(transcriber, 2)
        assert started.kind == module.SPEECH_STARTED
        kinds.append(result.kind)

    assert kinds == [module.TRANSCRIPT_FAILED] * module._RECOVER_AFTER_BUSY  # noqa: SLF001
    assert stt.recover_calls == 1

    # ...and the very next utterance is heard again.
    _speak(transcriber)
    started, result = await _drain(transcriber, 2)
    assert started.kind == module.SPEECH_STARTED
    assert result.kind == module.TRANSCRIPT, result
    assert result.text == "recovered engine works"
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_second_caller_skips_instead_of_queueing_behind_a_hung_engine() -> None:
    stt = _WedgingSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)
    await transcriber.warm()

    first = asyncio.create_task(
        transcriber.transcribe_audio(_loud() * 40, sample_rate=RATE)
    )
    await asyncio.sleep(0.05)  # let it enter the engine

    with pytest.raises(RecognizerBusy):
        await transcriber.transcribe_audio(_loud() * 40, sample_rate=RATE)

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_recognizer_timeout_is_bounded_by_the_audio_it_was_given(
    monkeypatch,
) -> None:
    monkeypatch.setattr(module, "_RECOGNITION_TIMEOUT_BASE_S", 0.05)
    monkeypatch.setattr(module, "_RECOGNITION_TIMEOUT_PER_AUDIO_S", 0.0)
    stt = _WedgingSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)
    await transcriber.warm()

    _speak(transcriber)
    events = await _drain(transcriber, 2)

    # The hung call is abandoned by OUR bound, and reported rather than hidden.
    assert events[1].kind == module.TRANSCRIPT_FAILED
    await transcriber.close()


def test_the_recognition_bound_scales_with_the_segment() -> None:
    """A flat ceiling either aborts long legitimate audio or shelters a wedge."""
    transcriber = LocalInputTranscriber(sample_rate=RATE)
    short = transcriber._recognition_timeout_s(  # noqa: SLF001
        b"\x00\x00" * RECOGNIZER_RATE, RECOGNIZER_RATE
    )
    long = transcriber._recognition_timeout_s(  # noqa: SLF001
        b"\x00\x00" * RECOGNIZER_RATE * 60, RECOGNIZER_RATE
    )
    assert long > short
    assert long <= module._RECOGNITION_TIMEOUT_MAX_S  # noqa: SLF001


# -- AP-23: the gate must not be a property of the maintainer's mic ----------


@pytest.mark.asyncio
async def test_a_quiet_microphone_still_endpoints_after_calibration() -> None:
    """Speech below the legacy absolute gate still opens an utterance.

    On a quieter input path real speech lands under the hardcoded threshold, so
    the endpointer never fired — and because a missing ``speech_started`` is
    missing GROUNDING, the adapter then rejected and interrupted every provider
    response. The user talked and was cut off, on every turn.
    """
    stt = _FakeSTT("quiet but audible")
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    hiss = _tone(45)
    speech = _tone(280)
    assert module._rms(speech) < module._SPEECH_RMS  # noqa: SLF001

    for _ in range(150):  # 3 s of room tone teaches the session its floor
        transcriber.feed(hiss, RATE)
    assert transcriber.noise_floor < module._SPEECH_RMS  # noqa: SLF001

    _speak(transcriber, chunk=speech)
    events = await _drain(transcriber, 2)

    assert events[0].kind == module.SPEECH_STARTED
    assert events[1].kind == module.TRANSCRIPT, events[1]
    await transcriber.close()


@pytest.mark.asyncio
async def test_calibration_is_lower_only_so_a_loud_microphone_is_unchanged() -> None:
    """Every historical measurement and pin still holds on a normal mic."""
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    assert transcriber._speech_gate() == module._SPEECH_RMS  # noqa: SLF001
    for _ in range(200):  # a genuinely noisy room cannot RAISE the gate
        transcriber.feed(_tone(2000), RATE)
    assert transcriber._speech_gate() <= module._SPEECH_RMS  # noqa: SLF001
    await transcriber.close()


@pytest.mark.asyncio
async def test_digital_silence_never_becomes_speech_at_any_calibration() -> None:
    """AP-27 by construction: a hallucination sits AT the floor, speech above it."""
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    for _ in range(400):  # a muted mic drives the floor to its absolute minimum
        transcriber.feed(_quiet(), RATE)

    assert transcriber.speech_recently() is False
    assert transcriber._speech_gate() >= module._SPEECH_GATE_ABS_MIN  # noqa: SLF001
    await transcriber.close()


# -- grounding evidence must survive a stalled consumer ---------------------


@pytest.mark.asyncio
async def test_a_full_queue_never_drops_a_speech_boundary() -> None:
    """A dropped ``speech_started`` is what makes a genuine answer get cut off.

    The adapter counts it as the proof that a human spoke, so losing one to
    queue pressure turns the model's next real response into a rejected
    self-echo.
    """
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    total = module._EVENT_QUEUE_MAX + 50  # noqa: SLF001
    for index in range(total):
        transcriber._emit(  # noqa: SLF001
            module.InputTranscriptEvent(
                kind=module.SPEECH_STARTED
                if index % 2 == 0
                else module.TRANSCRIPT_FAILED
            )
        )

    queued = list(transcriber._events)  # noqa: SLF001
    assert len(queued) <= module._EVENT_QUEUE_MAX  # noqa: SLF001
    boundaries = sum(1 for event in queued if event.kind == module.SPEECH_STARTED)
    assert boundaries == (total + 1) // 2
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_rate_mismatch_is_reported_once_and_loudly(caplog) -> None:
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    with caplog.at_level("WARNING"):
        for _ in range(20):
            transcriber.feed(_loud(), 16_000)

    warnings = [
        record
        for record in caplog.records
        if "dropping every microphone" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "16000" in warnings[0].getMessage()
    assert "24000" in warnings[0].getMessage()
    await transcriber.close()


# -- warm path --------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_builds_and_primes_the_recognizer_before_the_first_utterance() -> (
    None
):
    """A model load inside the first utterance delays that turn's transcript AND
    guarantees a concurrent output-transcript recovery times out (AP-24)."""
    builds = 0
    primed = 0

    class _Primed(_FakeSTT):
        def warm_up(self) -> None:
            nonlocal primed
            primed += 1

    def factory():  # noqa: ANN202 - test wiring
        nonlocal builds
        builds += 1
        return _Primed("warmed")

    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=factory)

    assert await transcriber.warm() is True
    assert builds == 1
    assert primed == 1

    _speak(transcriber)
    events = await _drain(transcriber, 2)
    assert events[1].kind == module.TRANSCRIPT
    assert builds == 1  # the utterance reused the warmed engine
    await transcriber.close()


@pytest.mark.asyncio
async def test_warm_never_raises_when_no_recognizer_can_be_built() -> None:
    def factory():  # noqa: ANN202 - test wiring
        raise RuntimeError("no STT configured")

    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=factory)

    assert await transcriber.warm() is False
    # The call still runs; the turn just learns it got no words.
    _speak(transcriber)
    events = await _drain(transcriber, 2)
    assert events[1].kind == module.TRANSCRIPT_FAILED
    await transcriber.close()
