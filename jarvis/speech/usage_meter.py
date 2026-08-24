"""Meter what speech spends — without ever standing in the way of speech.

Speech-to-text and text-to-speech do not bill by token, so nothing on the
model-cost path sees them: STT is billed per minute of audio, TTS per
character of input. Neither number is recorded anywhere in the app today,
which is why the Spend section's speech row is permanently empty.

Instrumenting call sites would not fix that. ``synthesize`` alone is called
from ten places in :mod:`jarvis.speech.pipeline` and more outside it, and the
eleventh call site — the one somebody adds next month — would silently miss.
So the *provider* is wrapped instead: one decorator per protocol, applied once
where the provider is built, and every call site is covered, including the
ones that do not exist yet.

Three properties are non-negotiable, in this order:

**It never delays speech.** Measuring is arithmetic on values already in hand:
``len(text)``, and the length of the PCM a chunk already carries. The sink is
called synchronously and nothing here awaits it (AP-9). The wrappers are
straight pass-throughs — a chunk is yielded onward the moment it arrives, and
no audio is ever buffered.

**It never breaks speech.** A sink that raises, a provider attribute that is
missing, a clock that misbehaves — all of it is caught and logged, and the
utterance continues. A metering bug that silenced Jarvis would be a far worse
bug than the missing ledger it was written to fix.

**It reports what was actually spent, not what completed.** A cancelled turn
that sent 400 characters to ElevenLabs spent those 400 characters, and a
transcription abandoned mid-stream still consumed the audio the provider read.
Every measured call reports from a ``finally``, so an abandoned generator, a
cancellation and an exception all still reach the ledger.

Wrapping is free when nothing is listening: ``meter_tts(provider, None)``
returns the very same object, so an install with no cost sink pays nothing —
not an attribute lookup, not a stack frame.

Pricing lives next door in :mod:`jarvis.costs.speech_rates`; this module only
counts. Nothing here initialises at import time (AP-26).
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

STAGE_STT = "stt"
STAGE_TTS = "tts"

#: Every PCM buffer that crosses this app is signed 16-bit — the STT
#: protocol says so (``detect_end_of_turn`` returns "raw int16 PCM bytes")
#: and every plugin honours it. Two bytes per sample per channel.
BYTES_PER_SAMPLE = 2

#: Fallback when a caller hands ``transcribe_pcm`` bytes without naming the
#: rate. It is the default in every shipped STT plugin's own signature, so
#: assuming it here reproduces exactly what the provider itself assumed.
DEFAULT_PCM_SAMPLE_RATE = 16_000


@dataclass(frozen=True, slots=True)
class SpeechUsage:
    """One metered speech call.

    ``chars`` and ``audio_ms`` are both filled whenever they are knowable,
    because which one bills depends on the vendor and we do not want the
    pricing table's answer baked into the measurement. TTS bills on ``chars``
    and reports ``audio_ms`` as the by-product; STT bills on ``audio_ms`` and
    leaves ``chars`` at 0 — deliberately, since counting transcript characters
    would mean reading the user's words to price them.

    ``model_or_voice`` is the identifier that actually decides the rate: the
    model for STT, and for TTS the model where the provider exposes one (an
    ElevenLabs Flash character costs half a Multilingual one) falling back to
    the voice, which is all some providers publish.
    """

    stage: str
    provider: str
    model_or_voice: str
    chars: int
    audio_ms: int
    ts_ms: int
    trace_id: str


@runtime_checkable
class UsageSink(Protocol):
    """Where a metered call is reported.

    Deliberately synchronous. The meter runs inside a ``finally`` that may be
    executing during ``GeneratorExit``, where awaiting is illegal, and on the
    voice critical path, where awaiting is forbidden anyway (AP-9). An
    implementation that needs I/O queues the record and returns.
    """

    def record(self, usage: SpeechUsage) -> None:
        """Take one usage record. Must not block and must not raise."""
        ...


#: A fixed id, or something that answers with the id of the turn in flight.
TraceIdSource = str | Callable[[], str] | None


def pcm_duration_ms(pcm: bytes, sample_rate: int, channels: int = 1) -> float:
    """Milliseconds of audio in a raw int16 PCM buffer.

    Fractional on purpose. A TTS chunk is often a few tens of milliseconds, so
    truncating each one to a whole millisecond before summing loses close to a
    second over a long answer — the caller rounds once, at the end.
    """
    if not pcm or sample_rate <= 0:
        return 0.0
    frame_bytes = BYTES_PER_SAMPLE * max(1, channels)
    return len(pcm) / frame_bytes / sample_rate * 1000.0


def _chunk_ms(chunk: Any) -> float:
    """Duration of one :class:`~jarvis.core.protocols.AudioChunk`.

    Read through ``getattr`` so a provider that yields its own chunk-shaped
    object — or a test fake — is measured rather than crashed on.
    """
    return pcm_duration_ms(
        getattr(chunk, "pcm", b"") or b"",
        int(getattr(chunk, "sample_rate", 0) or 0),
        int(getattr(chunk, "channels", 1) or 1),
    )


class _CountingAudio:
    """Passes an ``AudioChunk`` stream through, adding up what was consumed.

    The provider gets a stream that behaves exactly like the one the caller
    handed over; the total is whatever the provider actually pulled, so a
    provider that stops early is billed for what it read.
    """

    __slots__ = ("_source", "audio_ms")

    def __init__(self, source: AsyncIterator[Any]) -> None:
        self._source = source
        self.audio_ms = 0.0

    def __aiter__(self) -> _CountingAudio:
        return self

    async def __anext__(self) -> Any:
        chunk = await self._source.__anext__()
        self.audio_ms += _chunk_ms(chunk)
        return chunk


class _MeteredProvider:
    """Attribute forwarding and reporting shared by both wrappers.

    Everything the concrete provider exposes stays reachable: ``__getattr__``
    forwards the rest of its surface (``transcribe_container``,
    ``last_used_model``, ``recover``, a provider-specific method added
    tomorrow) so wrapping cannot quietly remove a capability someone probes
    for with ``hasattr``.
    """

    _stage = ""

    def __init__(
        self,
        provider: Any,
        sink: UsageSink,
        *,
        trace_id: TraceIdSource = None,
    ) -> None:
        self._inner = provider
        self._sink = sink
        self._trace_id = trace_id
        self._sink_broken = False
        # Copied rather than delegated: the protocol declares both as plain
        # attributes, and every shipped provider sets them once as class
        # constants. This mirrors ``FallbackTTS``, which does the same.
        self.name: str = str(getattr(provider, "name", "") or "")
        self.supports_streaming: bool = bool(getattr(provider, "supports_streaming", False))

    def __getattr__(self, name: str) -> Any:
        # ``_inner`` is bound first in __init__; guarding it stops a path that
        # bypasses __init__ (copy, pickle) from recursing forever.
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover — logging nicety
        return f"{type(self).__name__}({self.name or type(self._inner).__name__})"

    def _report(self, *, model_or_voice: str, chars: int, audio_ms: float) -> None:
        """Hand one record to the sink. Never raises, never blocks.

        Called from a ``finally``, which may be running during
        ``GeneratorExit`` — so this is synchronous throughout.
        """
        try:
            trace = self._trace_id
            trace_id = str(trace() or "") if callable(trace) else str(trace or "")
            self._sink.record(
                SpeechUsage(
                    stage=self._stage,
                    provider=self._provider_label(),
                    model_or_voice=model_or_voice,
                    chars=max(0, chars),
                    audio_ms=max(0, round(audio_ms)),
                    ts_ms=int(time.time() * 1000),
                    trace_id=trace_id,
                )
            )
        except Exception as exc:
            # Silence is right here, and it is the whole point of the module:
            # accounting must never cost an utterance (AP-9/AP-30). The first
            # failure is loud enough to find, the rest are debug so a broken
            # sink cannot flood the log during a live conversation.
            if not self._sink_broken:
                self._sink_broken = True
                log.warning(
                    "Speech usage sink failed (%s: %s); metering continues, records are lost.",
                    type(exc).__name__, exc,
                )
            else:
                log.debug("Speech usage sink failed again (%s).", exc)

    def _provider_label(self) -> str:
        """Who actually did the work.

        ``FallbackTTS`` answers to the primary's ``name`` even when the backup
        spoke, ``FallbackSTT`` delegates ``name`` to its primary, and Cartesia
        and ElevenLabs each fall back internally as well. Every one of them
        records the vendor that really ran — ``last_voice_provider`` on the TTS
        side, ``last_used_provider`` on the STT side — so the ledger names the
        vendor that was billed rather than the one that was asked.
        """
        for attr in ("last_voice_provider", "last_used_provider"):
            spoke = getattr(self._inner, attr, None)
            if spoke:
                return str(spoke)
        return self.name


class MeteredTTS(_MeteredProvider):
    """A :class:`~jarvis.core.protocols.TTSProvider` that counts characters."""

    _stage = STAGE_TTS

    # NOT ``async def`` returning a coroutine: every implementation is an async
    # GENERATOR, and both calling conventions are live in the pipeline —
    # ``chunks = tts.synthesize(text, language_code=lang)`` with no await, and
    # ``async for c in tts.synthesize(phrase)``. Declaring this as a generator
    # too keeps both working and keeps the stream lazy: nothing runs until the
    # first ``__anext__``, and each chunk is forwarded the instant it arrives.
    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
        **extra: Any,
    ) -> AsyncIterator[Any]:
        """Forward the synthesis, counting the input characters exactly once.

        ``**extra`` is forwarded untouched so a provider-specific keyword still
        reaches the provider, and the optional keywords are only passed on when
        the caller actually gave them — a third-party plugin with a narrower
        signature keeps working.
        """
        chars = len(text or "")
        kwargs: dict[str, Any] = dict(extra)
        if voice is not None:
            kwargs["voice"] = voice
        if language_code is not None:
            kwargs["language_code"] = language_code

        audio_ms = 0.0
        try:
            async for chunk in self._inner.synthesize(text, **kwargs):
                audio_ms += _chunk_ms(chunk)
                yield chunk
        finally:
            # Reached on exhaustion, on an exception, on cancellation, and on
            # the ``aclose()`` of a generator the caller walked away from. The
            # characters left the building when the request did.
            self._report(
                model_or_voice=self._tts_rate_key(voice),
                chars=chars,
                audio_ms=audio_ms,
            )

    def _tts_rate_key(self, voice: str | None) -> str:
        """The identifier the TTS rate actually depends on.

        The model where the provider has one — ElevenLabs Flash and
        Multilingual differ by 2x on the same voice — and the voice otherwise,
        which is the only handle Cartesia and Grok expose. ``_model`` /
        ``_model_name`` are private by the plugins' own convention; reading
        them is deliberate and degrades to the voice when they are absent.
        """
        for attr in ("last_used_model", "_model", "_model_name"):
            value = getattr(self._inner, attr, None)
            if value:
                return str(value)
        spoken = getattr(self._inner, "last_voice", None)
        return str(spoken or voice or "")


class MeteredSTT(_MeteredProvider):
    """An :class:`~jarvis.core.protocols.STTProvider` that counts audio time."""

    _stage = STAGE_STT

    async def transcribe(self, audio: AsyncIterator[Any], *args: Any, **kwargs: Any) -> Any:
        counter = _CountingAudio(audio)
        try:
            return await self._inner.transcribe(counter, *args, **kwargs)
        finally:
            self._report(
                model_or_voice=self._stt_rate_key(),
                chars=0,
                audio_ms=counter.audio_ms,
            )

    # An async generator for the same reason ``synthesize`` is one: the
    # implementations are generators, callers iterate the return value without
    # awaiting it, and partials must keep arriving as they are produced.
    async def stream_transcribe(
        self, audio: AsyncIterator[Any], *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        counter = _CountingAudio(audio)
        try:
            async for transcript in self._inner.stream_transcribe(counter, *args, **kwargs):
                yield transcript
        finally:
            self._report(
                model_or_voice=self._stt_rate_key(),
                chars=0,
                audio_ms=counter.audio_ms,
            )

    def __getattr__(self, name: str) -> Any:
        # ``transcribe_pcm`` is the path the live microphone really takes —
        # every shipped plugin has it and ``pipeline`` calls it, not
        # ``transcribe`` — but it is NOT on the protocol, so it is metered
        # here rather than declared. Resolving it through the inner provider
        # first keeps ``hasattr`` honest for a provider that lacks it.
        if name == "transcribe_pcm":
            if not hasattr(self._inner, "transcribe_pcm"):
                raise AttributeError(name)
            return self._metered_transcribe_pcm
        return super().__getattr__(name)

    async def _metered_transcribe_pcm(
        self, pcm_bytes: bytes, *args: Any, **kwargs: Any
    ) -> Any:
        """Meter the VAD-segmented-utterance path.

        The shared signature across every plugin is
        ``transcribe_pcm(pcm_bytes, sample_rate=16_000, language=None, ...)``,
        so the rate is either the first positional extra or the keyword; the
        rest is forwarded untouched, including the arguments only one plugin
        takes (faster-whisper's ``ignore_initial_prompt``).
        """
        rate = int(args[0]) if args else int(kwargs.get("sample_rate", DEFAULT_PCM_SAMPLE_RATE))
        audio_ms = pcm_duration_ms(pcm_bytes or b"", rate)
        try:
            return await self._inner.transcribe_pcm(pcm_bytes, *args, **kwargs)
        finally:
            self._report(
                model_or_voice=self._stt_rate_key(),
                chars=0,
                audio_ms=audio_ms,
            )

    def _stt_rate_key(self) -> str:
        """The model that priced this transcription.

        Read after the call: every cloud plugin narrows or falls back on a
        rejected model and records the result on ``last_used_model``, so the
        effective model is the one the invoice will show.
        """
        for attr in ("last_used_model", "_model", "_model_name"):
            value = getattr(self._inner, attr, None)
            if value:
                return str(value)
        return ""


def meter_tts(provider: Any, sink: UsageSink | None, *, trace_id: TraceIdSource = None) -> Any:
    """Wrap a TTS provider so every ``synthesize`` lands in the ledger.

    Returns ``provider`` itself when there is nothing to report to, or when it
    is already wrapped — an install without a cost sink pays nothing at all.

    Typed ``Any`` in and ``Any`` out, like ``wrap_stt_with_dictionary`` next
    door and for the same reason: ``TTSProvider.synthesize`` is declared with
    ``async def`` while every implementation (including this wrapper) is an
    async generator, so the nominal type does not describe the real contract.
    """
    if provider is None or sink is None or isinstance(provider, MeteredTTS):
        return provider
    return MeteredTTS(provider, sink, trace_id=trace_id)


def meter_stt(provider: Any, sink: UsageSink | None, *, trace_id: TraceIdSource = None) -> Any:
    """Wrap an STT provider so every transcription lands in the ledger.

    Covers ``transcribe``, ``stream_transcribe`` and ``transcribe_pcm``.
    ``transcribe_container`` — the UltraWiki path for an already-encoded voice
    note — is forwarded unmetered: its duration is not derivable without
    decoding the container, and decoding to price it is exactly the kind of
    work that must never appear here.

    Returns ``provider`` itself when ``sink`` is ``None`` or it is already
    wrapped.
    """
    if provider is None or sink is None or isinstance(provider, MeteredSTT):
        return provider
    return MeteredSTT(provider, sink, trace_id=trace_id)


__all__ = [
    "BYTES_PER_SAMPLE",
    "DEFAULT_PCM_SAMPLE_RATE",
    "STAGE_STT",
    "STAGE_TTS",
    "MeteredSTT",
    "MeteredTTS",
    "SpeechUsage",
    "TraceIdSource",
    "UsageSink",
    "meter_stt",
    "meter_tts",
    "pcm_duration_ms",
]
