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
        self._language = language if language and language != "auto" else None
        # A bias/vocabulary hint (proper nouns). Appended to the instruction so
        # the model favours those spellings; never treated as required content.
        self._prompt = (prompt or "").strip() or None
        self._temperature = temperature
        self._timeout_s = timeout_s
        self._client = client

    # ------------------------------------------------------------------
    # Public API (STTProvider contract + pipeline compat shims)
    # ------------------------------------------------------------------

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
        return await self._post_transcription(wav_bytes)

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
        """
        if not pcm_bytes:
            return Transcript(text="", language="unknown", confidence=0.0)
        wav_bytes = _wrap_pcm_as_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
        if language and language != "auto":
            previous = self._language
            self._language = language
            try:
                return await self._post_transcription(wav_bytes)
            finally:
                self._language = previous
        return await self._post_transcription(wav_bytes)

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
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "Gemini STT needs the 'google-genai' package (installed with the "
                "'[full]' extra). Install it, or use a different STT provider."
            ) from exc
        self._client = genai.Client(api_key=key)
        return self._client

    def _build_contents(self, wav_bytes: bytes) -> list[dict[str, Any]]:
        """Build the raw-dict ``contents`` payload (no google-genai types import).

        The SDK accepts a plain dict with an ``inline_data`` part whose ``data``
        is a base64 string — the exact shape the Gemini brain uses for images —
        so building it here keeps the module import-clean and unit-testable with
        a fake client.
        """
        instruction = _TRANSCRIBE_INSTRUCTION
        if self._language:
            instruction += f" The spoken language is '{self._language}'."
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

    async def _post_transcription(self, wav_bytes: bytes) -> Transcript:
        client = self._ensure_client()
        contents = self._build_contents(wav_bytes)
        # ``config`` as a plain dict is accepted by google-genai; temperature 0.0
        # keeps the transcription as deterministic as a generative model allows.
        config = {"temperature": self._temperature}
        try:
            # google-genai's ``generate_content`` is synchronous, so run it off
            # the event loop (same pattern as the Gemini Flash TTS plugin).
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self._model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 — degrade honestly (AP-22)
            raise RuntimeError(f"Gemini STT request failed: {exc}") from exc
        return _response_to_transcript(response, self._language)


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


def _clean_transcript(text: str) -> str:
    """Light cleanup of generative preamble artifacts — never a content filter.

    A generative model occasionally wraps the transcript in matched quotes even
    when told not to. Strip ONE matched pair of surrounding quotes. This is
    word-agnostic and never inspects the transcript for any specific phrase
    (AP-27): it only removes an outer quote pair, so recognized speech is
    preserved verbatim.
    """
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _response_to_transcript(response: Any, language: str | None) -> Transcript:
    """Parse a google-genai response into a Transcript.

    Uses the SDK's ``.text`` convenience accessor. Gemini returns no per-segment
    timings or confidence, so confidence is a plain presence signal (1.0 for
    non-empty text, else 0.0) and segments stay empty — the same convention the
    OpenRouter plugin uses when segments are absent.
    """
    text = _clean_transcript(str(getattr(response, "text", None) or ""))
    return Transcript(
        text=text,
        language=language or "unknown",
        confidence=1.0 if text else 0.0,
        is_partial=False,
        segments=(),
    )


__all__ = ["GeminiSTT", "Transcript"]
