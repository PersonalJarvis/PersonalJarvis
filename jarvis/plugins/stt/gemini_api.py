"""Gemini STT plugin — cloud transcription via google-genai inline audio.

The Gemini-only downloader is the recommended-default persona, yet the STT
cross-family table named ``gemini-api`` with no plugin behind it — so a user
whose ONLY key is a Google AI-Studio (Gemini) key got NO cloud speech-to-text
and dead-ended on the local ``faster-whisper`` engine the base install never
bundles. This plugin closes that gap by transcribing through the SAME Gemini
credential the brain and TTS already use — no second key.

How it works (and its honest limits): Gemini has no dedicated transcription
endpoint. Instead this uses the model's multimodal audio understanding —
``generate_content`` with the utterance as an inline ``audio/wav`` part and a
tight instruction to emit the verbatim transcript only. That is GENERATIVE
transcription, so it is best-effort: it is fed a full VAD-segmented utterance
(real speech that already passed voice-activity detection), returns free-form
text with no per-segment timings or confidence, and can add stray preamble that
the light output cleanup below trims. It is a working cloud STT for a Gemini-only
user, not a drop-in equal of a dedicated ASR model.

Model default: the same widely-served Gemini flash model the brain defaults to.
Gate on the capability, never a fancier model id (AP-21) — any audio-capable
Gemini model works; a user can set another in the STT model field.

Plugin contract: structurally compatible with
``jarvis.core.protocols.STTProvider`` WITHOUT importing ``jarvis.*`` at import
time (entry-point plugins stay import-clean). The credential lookup imports
``jarvis.core.config`` lazily inside a method, mirroring the Gemini brain. The
returned object is a locally defined ``Transcript`` dataclass with the identical
field shape; consumers duck-type on ``text`` / ``language`` / ``confidence`` /
``is_partial`` / ``segments``.

Audio I/O contract (compatible with the Jarvis VAD output):
  * ``transcribe`` consumes chunks exposing ``.pcm`` (int16 little-endian
    bytes), ``.sample_rate`` (Hz) and optionally ``.channels`` (default 1).
  * ``transcribe_pcm`` receives a full VAD-segmented utterance as raw int16 PCM
    at 16 kHz mono (the pipeline default) — the drop-in shim the speech
    pipeline actually calls.
  * All PCM is wrapped in an in-memory WAV container before the inline upload.

Credential resolution reuses ``jarvis.core.config.resolve_provider_endpoint``
(keyring -> ENV -> .env -> local-file fallback), exactly like the Gemini brain.
A missing key, an unavailable ``google-genai`` package, or an API error raises a
clear English error so the STT factory degrades to the key-free local
``faster-whisper`` floor instead of bricking voice input for a single-provider
user (AP-22). Never accept a key from voice/chat input (AP-2).
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

# Widely-served Gemini flash model with audio understanding. NOT pinned to a
# preview/pro id (AP-21): any audio-capable Gemini model transcribes; a user can
# override it in the STT model field. Kept in step with the Gemini brain default.
DEFAULT_MODEL = "gemini-3-flash-preview"

#: The per-call ``language`` value that REQUESTS detection instead of a pinned
#: language. Spelled out per plugin because plugins may not import ``jarvis.*``.
AUTO_LANGUAGE = "auto"


def _detect_or(language: str | None, configured: str | None) -> str | None:
    """The language for ONE call. ``None`` means "let the model detect".

    Three cases, and the middle one is the whole point:

    * a concrete code (``"de"``) — transcribe as that language;
    * ``"auto"`` — an explicit request to DETECT, which clears ``configured`` for
      this call. Treating it as "no argument given" is what let dictation's auto
      mode inherit ``[stt].language`` and write German speech in English
      (live bug 2026-07-28);
    * ``None`` / empty — no per-call opinion, so the configured pin stands.
    """
    if language is None or not str(language).strip():
        return configured
    return None if str(language).strip().lower() == AUTO_LANGUAGE else str(language)

# The transcription directive. Tight on purpose: a generative model must be told
# to emit ONLY the verbatim words, or it wraps the transcript in commentary. The
# output cleanup below is a light safety net, not a content filter (AP-27): it
# never inspects the transcript for a wake word or rewrites recognized speech.
_TRANSCRIBE_INSTRUCTION = (
    "Transcribe the speech in this audio verbatim. Output ONLY the exact words "
    "spoken, with no preamble, no explanation, no speaker labels, and no "
    "quotation marks around the text. If the audio contains no discernible "
    "speech, output nothing at all."
)


@dataclass(frozen=True, slots=True)
class Transcript:
    """Local Transcript shape, mirrors ``jarvis.core.protocols.Transcript``.

    Plugin code must not import from ``jarvis.*``; structural compatibility is
    sufficient because ``STTProvider`` is a ``runtime_checkable`` Protocol and
    consumers access the fields by name.
    """

    text: str
    language: str
    confidence: float
    is_partial: bool = False
    segments: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: What the model returned BEFORE the cleanup filter ran. Read by the two
    #: callers that must not get an edited string - the dictation lane (which
    #: owns the user's filler switch) and wake verification.
    raw_text: str = ""


class GeminiSTT:
    """Google Gemini cloud STT (non-streaming, generative audio understanding).

    The provider id is ``gemini-api`` (NOT ``gemini``) on purpose: the Gemini
    *brain* already owns the ``gemini`` id in the shared model-catalog and
    provider-spec namespaces, so the STT variant takes a distinct id — mirroring
    the repo's own ``openrouter`` (brain) vs ``openrouter-stt`` (STT) split. The
    underlying credential is still SHARED with the brain/TTS, so no second key is
    needed.
    """

    name = "gemini-api"
    supports_streaming = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
        timeout_s: float = 30.0,
        client: Any | None = None,
    ) -> None:
        # An explicitly injected ``api_key`` / ``client`` wins (tests, team
        # setups). Otherwise the key is resolved lazily on the first request via
        # ``resolve_provider_endpoint`` so construction stays cheap and never
        # triggers a config load on the boot critical path (AP-26).
        self._api_key = (api_key or "").strip() or None
        self._model = model or DEFAULT_MODEL
        self._last_used_model = ""
        self._language = language if language and language != "auto" else None
        # A bias/vocabulary hint (proper nouns). Appended to the instruction so
        # the model favours those spellings; never treated as required content.
        self._prompt = (prompt or "").strip() or None
        self._temperature = temperature
        # Wired into the SDK client below — see ``_http_options``. It used to be
        # assigned here and read by nothing at all, which meant this provider had
        # NO timeout at any layer.
        self._timeout_s = timeout_s
        self._client = client

    # ------------------------------------------------------------------
    # Public API (STTProvider contract + pipeline compat shims)
    # ------------------------------------------------------------------

    @property
    def last_used_model(self) -> str:
        """Effective model that produced the latest successful transcript."""
        return self._last_used_model

    async def transcribe(self, audio: AsyncIterator[Any]) -> Transcript:
        """Collect audio chunks, upload once, return a final Transcript."""
        pcm_pieces: list[bytes] = []
        sample_rate = 16_000
        channels = 1
        async for chunk in audio:
            pcm_pieces.append(bytes(chunk.pcm))
            sample_rate = int(getattr(chunk, "sample_rate", sample_rate))
            channels = int(getattr(chunk, "channels", channels))

        if not pcm_pieces:
            return Transcript(text="", language="unknown", confidence=0.0)

        wav_bytes = _wrap_pcm_as_wav(
            b"".join(pcm_pieces), sample_rate=sample_rate, channels=channels
        )
        return await self._post_transcription(wav_bytes, language=self._language)

    async def stream_transcribe(
        self, audio: AsyncIterator[Any]
    ) -> AsyncIterator[Transcript]:
        """Gemini STT is non-streaming — yield a single final Transcript."""
        final = await self.transcribe(audio)
        yield final

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> Transcript:
        """Drop-in compat shim mirroring ``FasterWhisperProvider.transcribe_pcm``.

        The speech pipeline delivers a full VAD-segmented utterance as raw int16
        PCM (mono, 16 kHz by default). We wrap it in a WAV container and send it
        as a single inline-audio request.

        ``language="auto"`` forces per-utterance detection for THIS call even
        when a language is configured — see :func:`_detect_or`.
        """
        if not pcm_bytes:
            return Transcript(text="", language="unknown", confidence=0.0)
        wav_bytes = _wrap_pcm_as_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
        return await self._post_transcription(
            wav_bytes, language=_detect_or(language, self._language)
        )

    def _ensure_model(self) -> None:
        """No-op compat shim — cloud STT has nothing to warm up.

        ``jarvis.speech.pipeline`` calls ``_ensure_model`` to pre-download local
        Whisper weights. For a cloud provider the first request is the warm-up.
        """
        return None

    async def aclose(self) -> None:
        """No owned network client to close (the genai client is stateless)."""
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _http_options(self) -> dict[str, int]:
        """Transport options for the google-genai client — the request timeout.

        This is the ONLY thing standing between a stalled Gemini request and a
        permanently stuck lane. google-genai does not merely lack a default
        timeout, it actively FORCES ``timeout=None`` onto its own HTTP client
        when the caller passes no ``http_options`` (``_api_client.py``:
        ``if 'timeout' not in args: args['timeout'] = None``), which also
        overrides httpx's own defaults. And ``generate_content`` is synchronous,
        so it runs in ``asyncio.to_thread`` — a thread nothing can cancel, so no
        ``wait_for`` above this line can actually stop the call; it only stops
        WAITING for it while the thread leaks. A Gemini-only user could hang the
        dictation lane forever with the microphone already closed.

        ``HttpOptions.timeout`` is in MILLISECONDS, not seconds — verified
        against the installed google-genai (its ``get_timeout_in_seconds``
        divides by 1000), and the field description says so. Getting that wrong
        by 1000x is the whole reason this is a named, unit-tested method instead
        of an inline literal. The floor of one second keeps a nonsensical
        configured value (0, negative) from becoming "time out instantly", which
        would brick the provider rather than bound it.
        """
        return {"timeout": int(max(1.0, float(self._timeout_s)) * 1000)}

    def _ensure_client(self) -> Any:
        """Return the google-genai client, building it lazily from the key.

        An injected client (tests / team setups) wins and skips the SDK import
        entirely. Raises a clear English error when no Gemini key is configured
        or ``google-genai`` is not installed, so the STT factory degrades to the
        local floor rather than bricking voice input (AP-22).
        """
        if self._client is not None:
            return self._client

        key = self._api_key
        if not key:
            # Lazy import keeps the plugin ``jarvis.*``-free at import time.
            from jarvis.core import config as _cfg

            ep = _cfg.resolve_provider_endpoint("gemini")
            key = ep.credential or None
        if not key:
            raise RuntimeError(
                "No Gemini API key found (gemini_api_key / GEMINI_API_KEY / "
                "GOOGLE_AIStudio_API_KEY). Add it in the app's API-Keys view; the "
                "key is shared with the Gemini brain and TTS."
            )
        try:
            # Availability check only — the client itself is built below by the
            # routed builder. A plain import (not find_spec) so a broken
            # dependency chain also lands in the clear RuntimeError.
            from google import genai  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Gemini STT needs the 'google-genai' package (installed with the "
                "'[full]' extra). Install it, or use a different STT provider."
            ) from exc
        # A plain dict is an accepted ``HttpOptionsDict`` at runtime; the local
        # ``Any`` keeps that fact from needing a types import the plugin must
        # not make (the SDK is absent on a base install).
        http_options: Any = self._http_options()
        # Routed builder: AI Studio or Vertex express, decided per key. Lazy
        # import, like the config import above (plugin stays jarvis.*-free at
        # import time).
        from jarvis.core.google_genai import build_genai_client

        self._client = build_genai_client(key, http_options=http_options)
        return self._client

    def _build_contents(
        self, wav_bytes: bytes, *, language: str | None = None
    ) -> list[dict[str, Any]]:
        """Build the raw-dict ``contents`` payload (no google-genai types import).

        The SDK accepts a plain dict with an ``inline_data`` part whose ``data``
        is a base64 string — the exact shape the Gemini brain uses for images —
        so building it here keeps the module import-clean and unit-testable with
        a fake client.

        A ``language`` of None leaves the sentence out entirely, which is what
        asks the model to transcribe whatever language it actually hears.
        """
        instruction = _TRANSCRIBE_INSTRUCTION
        if language:
            instruction += f" The spoken language is '{language}'."
        if self._prompt:
            instruction += (
                f" Expected vocabulary and proper nouns (favour these spellings): "
                f"{self._prompt}."
            )
        audio_part = {
            "inline_data": {
                "mime_type": "audio/wav",
                "data": base64.b64encode(wav_bytes).decode("ascii"),
            }
        }
        return [{"role": "user", "parts": [audio_part, {"text": instruction}]}]

    async def _post_transcription(
        self, wav_bytes: bytes, *, language: str | None = None
    ) -> Transcript:
        # First use builds the client: google-genai import + (for an AQ. key)
        # the one-time routing probe — neither belongs on the event loop.
        client = await asyncio.to_thread(self._ensure_client)
        contents = self._build_contents(wav_bytes, language=language)
        # ``config`` as a plain dict is accepted by google-genai; temperature 0.0
        # keeps the transcription as deterministic as a generative model allows.
        config = {"temperature": self._temperature}

        async def _generate(model: str) -> Any:
            # google-genai's ``generate_content`` is synchronous, so run it off
            # the event loop (same pattern as the Gemini Flash TTS plugin).
            return await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=contents,
                config=config,
            )

        model = self._model
        try:
            response = await _generate(model)
        except Exception as exc:  # noqa: BLE001 — degrade honestly (AP-22)
            # Imported here, not at module top, to keep the plugin
            # ``jarvis.*``-free at import time (the entry-point purity contract)
            # — the same lazy seam the credential lookup uses. This runs only on
            # an already-failed request.
            from jarvis.plugins.stt.capabilities import (
                is_model_rejection,
                log_model_fallback,
            )
            from jarvis.plugins.stt.errors import STTHTTPError, status_from_exception

            failure: BaseException = exc
            # A model this account cannot call is a CONFIGURATION problem, not a
            # reason to lose the utterance: fall back to the default model once
            # and say so, exactly like the OpenAI-shaped plugins. A failure of
            # the FALLBACK is reported through the same classification path
            # below — a raw SDK error escaping here would cost the caller the
            # status code its retry ladder runs on.
            if model != DEFAULT_MODEL and is_model_rejection(str(exc), model):
                log_model_fallback("Gemini", model, DEFAULT_MODEL, str(exc))
                try:
                    transcript = _response_to_transcript(
                        await _generate(DEFAULT_MODEL), language
                    )
                    self._last_used_model = DEFAULT_MODEL
                    return transcript
                except Exception as retry_exc:  # noqa: BLE001 — classify, never leak
                    failure = retry_exc

            message = f"Gemini STT request failed: {failure}"
            # The SDK raises its own ``APIError``, which carries the HTTP status
            # as ``.code``. Flattening that into a bare RuntimeError threw away
            # the one fact the caller needs to tell a retryable 429 from a
            # hopeless 401 — so the retry ladder never ran for a Gemini user and
            # a bursty rate limit ate the turn. We cannot import the SDK's error
            # type (google-genai is absent on a base install), hence the
            # duck-typed lookup. A transport error / timeout has no status and
            # stays a plain RuntimeError: inventing one would be a lie.
            status = status_from_exception(failure)
            if status is None:
                raise RuntimeError(message) from failure
            raise STTHTTPError(
                message,
                status=status,
                headers=getattr(getattr(failure, "response", None), "headers", None),
            ) from failure
        transcript = _response_to_transcript(response, language)
        self._last_used_model = model
        return transcript


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------

def _wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int, channels: int) -> bytes:
    """Wrap int16 little-endian PCM in a minimal WAV header (in memory)."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(max(1, channels))
        wav.setsampwidth(2)  # int16
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def _response_to_transcript(response: Any, language: str | None) -> Transcript:
    """Parse a google-genai response into a Transcript.

    Uses the SDK's ``.text`` convenience accessor. Gemini returns no per-segment
    timings or confidence, so confidence is a plain presence signal (1.0 for
    non-empty text, else 0.0) and segments stay empty — the same convention the
    OpenRouter plugin uses when segments are absent.

    The text is filtered on the way in. This plugin used to carry its own
    two-line version of that (strip one matched quote pair, which a generative
    model adds even when told not to); the shared filter does the same and adds
    the artifacts every recognizer produces — repetition loops, hesitation
    sounds, stutters, NFD umlauts. It stays word-agnostic: nothing is rejected
    for what it says (AP-27). The untouched string stays on ``raw_text``.
    """
    raw = str(getattr(response, "text", None) or "").strip()
    tag = language or "unknown"
    # Local import so the module top stays ``jarvis.*``-free (entry-point
    # purity), the same seam the error mapper already uses.
    from jarvis.plugins.stt.transcript_filter import clean_stt_text

    return Transcript(
        text=clean_stt_text(raw, language=tag),
        language=tag,
        # Presence on the RAW text: a cleanup that emptied the string is a
        # filter defect, not silence.
        confidence=1.0 if raw else 0.0,
        is_partial=False,
        segments=(),
        raw_text=raw,
    )


__all__ = ["GeminiSTT", "Transcript"]
