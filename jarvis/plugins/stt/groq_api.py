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

#: The per-call ``language`` value that REQUESTS detection instead of a pinned
#: language. Spelled out per plugin because plugins may not import ``jarvis.*``.
AUTO_LANGUAGE = "auto"


def _detect_or(language: str | None, configured: str | None) -> str | None:
    """The language for ONE call. ``None`` means "let the service detect".

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
    #: What the vendor returned BEFORE the cleanup filter ran. Read by the two
    #: callers that must not get an edited string - the dictation lane (which
    #: owns the user's filler switch) and wake verification.
    raw_text: str = ""


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
        self._last_used_model = ""
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

    @property
    def last_used_model(self) -> str:
        """Effective model that produced the latest successful transcript."""
        return self._last_used_model

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
        return await self._post_transcription(wav_bytes, language=self._language)

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

    async def transcribe_container(
        self, data: bytes, *, filename: str = "recording", language: str | None = None
    ) -> Transcript:
        """Transcribe an ENCODED audio file (m4a, opus, mp3, mp4, wav, ...).

        The optional capability the UltraWiki enrichment stage looks for. The
        live microphone path delivers raw PCM, which everything else here
        wraps in a WAV container; an imported voice note is already encoded and
        this endpoint takes those formats directly. Passing the container
        through untouched is deliberate — re-wrapping it would corrupt it.
        """
        if not data:
            return Transcript(text="", language="unknown", confidence=0.0)
        return await self._post_transcription(
            data, filename=filename, language=_detect_or(language, self._language)
        )

    async def _post_transcription(
        self,
        wav_bytes: bytes,
        *,
        filename: str = "audio.wav",
        language: str | None = None,
    ) -> Transcript:
        if not self._api_key:
            raise RuntimeError(
                "GROQ_API_KEY missing; provide api_key=... or set the env var."
            )

        # The NAME carries the container format to the service, so an imported
        # `.opus` must not be announced as `audio.wav`.
        upload_name = _upload_name(filename)
        mime = "audio/wav" if upload_name.endswith(".wav") else "application/octet-stream"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        client = self._get_client()

        from jarvis.plugins.stt.capabilities import (
            MAX_REQUEST_DOWNGRADES,
            error_text,
            is_model_rejection,
            log_model_fallback,
            remember_shape,
            resolve_shape,
            shape_after_rejection,
        )

        model = self._model
        shape = resolve_shape(self.name, model)
        for _attempt in range(MAX_REQUEST_DOWNGRADES):
            # A fresh file tuple per attempt: httpx consumes the buffer it is
            # handed, so a retry with the same object would upload nothing.
            files = {"file": (upload_name, wav_bytes, mime)}
            data: dict[str, str] = {
                "model": model,
                "response_format": shape.response_format,
            }
            if shape.temperature:
                data["temperature"] = str(self._temperature)
            # Omitted entirely when None — that is what asks Whisper to detect
            # the spoken language instead of decoding it as a pinned one.
            if language and shape.language:
                data["language"] = language
            if self._prompt and shape.prompt:
                data["prompt"] = self._prompt

            response = await client.post(
                self._endpoint, headers=headers, data=data, files=files
            )
            if response.status_code == 400:
                # A refusal that names an optional field is EVIDENCE about the
                # model, not a failure: drop that field and ask again, so a
                # transcription model with a narrower contract than Whisper's
                # still transcribes instead of losing the utterance.
                detail = error_text(response)
                narrowed = shape_after_rejection(shape, detail)
                if narrowed is not None:
                    remember_shape(self.name, model, narrowed)
                    shape = narrowed
                    continue
                if model != DEFAULT_MODEL and is_model_rejection(detail, model):
                    log_model_fallback("Groq", model, DEFAULT_MODEL, detail)
                    model = DEFAULT_MODEL
                    shape = resolve_shape(self.name, model)
                    continue
            # ``httpx.HTTPStatusError`` carries the status on
            # ``.response.status_code`` and the ``Retry-After`` header on
            # ``.response.headers`` — which is exactly why the pipeline's
            # transient-error retry ladder worked for THIS provider and for no
            # other one, and what the shared
            # ``jarvis.plugins.stt.errors.STTHTTPError`` reproduces for the
            # plugins that used to flatten every status into a bare
            # RuntimeError. This plugin deliberately does NOT raise that shared
            # type: it is the one STT plugin held to a total ``jarvis.*``-import
            # ban outside its own plugin package (CLAUDE.md §5, pinned by
            # tests/contract/test_stt_protocol.py). It already emits the
            # classifiable shape, so there is nothing to gain and a purity
            # contract to lose. Do not "unify" this line.
            response.raise_for_status()
            transcript = _payload_to_transcript(response.json())
            self._last_used_model = model
            return transcript

        response.raise_for_status()
        transcript = _payload_to_transcript(response.json())
        self._last_used_model = model
        return transcript


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------

def _read_keyring_secret(service: str, username: str) -> str:
    """Best-effort Credential-Manager lookup. Returns ``""`` on any failure."""
    # No jarvis.* import here (plugin purity contract): the host's plugin
    # loader (jarvis.core.registry.load) installs the process-wide keyring
    # backend — on macOS the single-vault-item wrapper (BUG-103) — before this
    # module is loaded, so this direct read is served from the bundled vault
    # instead of a per-item Keychain entry with its own permission dialog.
    # Standalone (non-Jarvis) use degrades to the plain OS keyring.
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        val = keyring.get_password(service, username)
        return val or ""
    except Exception:  # noqa: BLE001
        return ""


#: Container extensions these transcription APIs accept. A name outside the
#: list is uploaded as `.wav`, which is what the live path always sends.
_ACCEPTED_UPLOAD_SUFFIXES: frozenset[str] = frozenset(
    {".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".webm", ".mpga", ".mpeg"}
)


def _upload_name(filename: str) -> str:
    """A safe multipart filename that still carries the real extension."""
    from pathlib import PurePosixPath  # noqa: PLC0415 — tiny, local

    suffix = PurePosixPath(str(filename or "")).suffix.lower()
    return f"audio{suffix}" if suffix in _ACCEPTED_UPLOAD_SUFFIXES else "audio.wav"


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
    """Parse Groq's OpenAI-shaped verbose_json response into a Transcript.

    The text is filtered on the way in (see
    :func:`jarvis.plugins.stt.transcript_filter.clean_stt_text`), which is the
    last point where the recognizer's own artifacts can be removed before every
    consumer downstream reads the string. The untouched payload text stays on
    ``raw_text``, and the per-segment texts are left exactly as delivered: they
    carry the timings the flight recorder needs and are never read as prose.
    """
    raw = str(payload.get("text", "")).strip()
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
        # Presence is judged on the RAW text: a cleanup that emptied the string
        # is a filter defect, and reporting 0.0 would call it silence instead.
        confidence = 1.0 if raw else 0.0

    # Local import, like the credential lookup and the error mapper: the module
    # top stays ``jarvis.*``-free (entry-point purity).
    from jarvis.plugins.stt.transcript_filter import clean_stt_text

    return Transcript(
        text=clean_stt_text(raw, language=language),
        language=language,
        confidence=min(1.0, max(0.0, confidence)),
        is_partial=False,
        segments=seg_tuple,
        raw_text=raw,
    )


__all__ = ["GroqWhisperAPI", "Transcript"]

# Silence unused-import noise when type-checking is off; asyncio is reserved
# for potential future use (e.g. concurrent uploads).
_ = asyncio
