"""Groq Whisper STT plugin.

Cloud STT via Groq's OpenAI-compatible audio API. Targets ``whisper-large-v3``
(Groq's hosted Whisper-v3 endpoint, ~200-400 ms warm latency).

Plugin contract: structurally compatible with ``jarvis.core.protocols.STTProvider``
without importing from ``jarvis.*``. The returned object is a locally defined
``Transcript`` dataclass with identical field shape; consumers duck-type on the
attributes (``text``, ``language``, ``confidence``, ``is_partial``, ``segments``).

Audio I/O contract (compatible with the Jarvis VAD output):
  * Input chunks expose ``.pcm`` (int16 little-endian bytes), ``.sample_rate``
    (Hz) and optionally ``.channels`` (default 1).
  * All chunks are concatenated and wrapped in an in-memory WAV container
    before multipart upload to Groq.

API key resolution order:
  1. constructor argument
  2. ``GROQ_API_KEY`` env var
  3. Windows Credential Manager via ``keyring`` (service ``personal-jarvis``,
     username ``groq_api_key``) — same convention as the rest of Jarvis,
     without importing ``jarvis.*`` (the third-party ``keyring`` package is
     a soft dependency; if it is missing the lookup is silently skipped).

Never accept a key from voice/chat input (AP-2).
"""
from __future__ import annotations

import asyncio
import io
import os
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-large-v3"

# Whisper accepts up to 224 prompt tokens; ~1000 chars is a safe cap that
# stays under that even for token-dense German compounds (avg ~4 chars/token).
# Going over makes Groq reject the whole turn with HTTP 400 and the user
# experiences total silence — never worth saving a few extra words.
_MAX_PROMPT_CHARS = 1024


@dataclass(frozen=True, slots=True)
class Transcript:
    """Local Transcript shape, mirrors ``jarvis.core.protocols.Transcript``.

    Plugin code must not import from ``jarvis.*``; structural compatibility is
    sufficient because ``STTProvider`` is a ``runtime_checkable`` Protocol and
    consumers access fields by name.
    """

    text: str
    language: str
    confidence: float
    is_partial: bool = False
    segments: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class GroqWhisperAPI:
    """Groq-hosted Whisper STT (cloud, non-streaming)."""

    name = "groq-api"
    supports_streaming = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
        timeout_s: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = (
            api_key
            or os.environ.get("GROQ_API_KEY", "")
            or _read_keyring_secret("personal-jarvis", "groq_api_key")
        )
        self._model = model
        self._endpoint = endpoint
        self._language = language if language and language != "auto" else None
        # Whisper ``prompt`` biases the token distribution toward the words in
        # this string — the standard trick to keep proper nouns and domain
        # vocabulary stable. Strip + cap so a whitespace-only config value
        # behaves like "unset", and an oversized one cannot crash Groq with
        # HTTP 400 ("prompt too long").
        cleaned = (prompt or "").strip()
        self._prompt = cleaned[:_MAX_PROMPT_CHARS] if cleaned else None
        self._temperature = temperature
        self._timeout_s = timeout_s
        self._client = http_client
        self._owns_client = http_client is None

    # ------------------------------------------------------------------
    # Public API (STTProvider contract)
    # ------------------------------------------------------------------

    async def transcribe(self, audio: AsyncIterator[Any]) -> Transcript:
        """Collect audio chunks, upload, return a final Transcript."""
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
        """Groq has no streaming endpoint — yield a single final Transcript."""
        final = await self.transcribe(audio)
        yield final

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> Transcript:
        """Drop-in compat shim mirroring ``FasterWhisperProvider.transcribe_pcm``.

        Used by ``jarvis.speech.pipeline._handle_utterance`` which delivers a
        full VAD-segmented utterance as raw int16 PCM. The Groq endpoint
        accepts a single WAV upload, so we wrap and POST directly without the
        AsyncIterator dance.
        """
        if not pcm_bytes:
            return Transcript(text="", language="unknown", confidence=0.0)
        wav_bytes = _wrap_pcm_as_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
        # Optional per-call language override
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

        ``jarvis.speech.pipeline._warmup`` calls ``_ensure_model`` on the
        STT instance to pre-download the local Whisper weights. For Groq this
        is a no-op; the first transcription request itself is the warm-up.
        """
        return None

    async def aclose(self) -> None:
        """Close the owned HTTP client (no-op when injected externally)."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def _post_transcription(self, wav_bytes: bytes) -> Transcript:
        if not self._api_key:
            raise RuntimeError(
                "GROQ_API_KEY missing; provide api_key=... or set the env var."
            )

        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data: dict[str, str] = {
            "model": self._model,
            "response_format": "verbose_json",
            "temperature": str(self._temperature),
        }
        if self._language:
            data["language"] = self._language
        if self._prompt:
            data["prompt"] = self._prompt

        headers = {"Authorization": f"Bearer {self._api_key}"}
        client = self._get_client()
        response = await client.post(
            self._endpoint, headers=headers, data=data, files=files
        )
        response.raise_for_status()
        payload = response.json()
        return _payload_to_transcript(payload)


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------

def _read_keyring_secret(service: str, username: str) -> str:
    """Best-effort Credential-Manager lookup. Returns ``""`` on any failure."""
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        val = keyring.get_password(service, username)
        return val or ""
    except Exception:  # noqa: BLE001
        return ""


def _wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int, channels: int) -> bytes:
    """Wrap int16 little-endian PCM in a minimal WAV header (in memory)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(max(1, channels))
        wav.setsampwidth(2)  # int16
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def _payload_to_transcript(payload: dict[str, Any]) -> Transcript:
    """Parse Groq's OpenAI-shaped verbose_json response into a Transcript."""
    text = str(payload.get("text", "")).strip()
    language = str(payload.get("language", "unknown")) or "unknown"
    segments_raw = payload.get("segments") or ()

    seg_tuple: tuple[dict[str, Any], ...] = tuple(
        {
            "start": float(s.get("start", 0.0)),
            "end": float(s.get("end", 0.0)),
            "text": str(s.get("text", "")),
            "avg_logprob": float(s.get("avg_logprob", 0.0)),
        }
        for s in segments_raw
    )

    if seg_tuple:
        import math

        avg = sum(s["avg_logprob"] for s in seg_tuple) / len(seg_tuple)
        try:
            confidence = float(math.exp(avg))
        except OverflowError:
            confidence = 0.0
    else:
        confidence = 1.0 if text else 0.0

    return Transcript(
        text=text,
        language=language,
        confidence=min(1.0, max(0.0, confidence)),
        is_partial=False,
        segments=seg_tuple,
    )


__all__ = ["GroqWhisperAPI", "Transcript"]

# Silence unused-import noise when type-checking is off; asyncio is reserved
# for potential future use (e.g. concurrent uploads).
_ = asyncio
