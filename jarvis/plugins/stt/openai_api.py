"""OpenAI Whisper STT plugin — cloud transcription via the OpenAI audio API.

One OpenAI API key — the SAME ``openai_api_key`` slot the OpenAI *brain* already
uses — unlocks cloud speech-to-text, so a downloader whose only credential is an
OpenAI key gets working voice input with no second key. This closes the single
biggest single-key STT gap: the cross-family table named ``openai-api`` but
shipped no plugin, so an OpenAI-only user dead-ended on the local
``faster-whisper`` engine the base install never bundles.

Endpoint (OpenAI-compatible audio API, identical wire shape to the Groq plugin):
  * ``POST {base_url}/audio/transcriptions`` (multipart upload),
  * Headers: ``Authorization: Bearer <key>``,
  * multipart fields: ``file`` (an in-memory WAV), ``model``, ``response_format``
    (``verbose_json`` for segment timings + confidence), optional ``language`` /
    ``prompt`` / ``temperature``.

Model default: ``whisper-1`` — the universally-available transcription model
every OpenAI account can call. Gate on the capability, never a fancier model id
(AP-21); a user who wants a newer transcription model sets it in the STT model
field and the wire contract is unchanged.

Plugin contract: structurally compatible with
``jarvis.core.protocols.STTProvider`` WITHOUT importing ``jarvis.*`` at import
time (entry-point plugins stay import-clean). The credential lookup imports
``jarvis.core.config`` lazily inside a method, mirroring the OpenRouter STT
plugin. The returned object is a locally defined ``Transcript`` dataclass with
the identical field shape; consumers duck-type on ``text`` / ``language`` /
``confidence`` / ``is_partial`` / ``segments``.

Audio I/O contract (compatible with the Jarvis VAD output):
  * ``transcribe`` consumes chunks exposing ``.pcm`` (int16 little-endian
    bytes), ``.sample_rate`` (Hz) and optionally ``.channels`` (default 1).
  * ``transcribe_pcm`` receives a full VAD-segmented utterance as raw int16 PCM
    at 16 kHz mono (the pipeline default) — the drop-in shim the speech
    pipeline actually calls.
  * All PCM is wrapped in an in-memory WAV container before multipart upload.

Credential resolution reuses ``jarvis.core.config.resolve_provider_endpoint``
(keyring -> ENV -> .env -> local-file fallback), exactly like the OpenAI brain.
A missing / dead (401) / out-of-credit (402) / rate-limited (429) / unreachable
key raises a clear English error so the STT factory degrades to the key-free
local ``faster-whisper`` floor instead of bricking voice input for a single-
provider user (AP-22). Never accept a key from voice/chat input (AP-2).
"""
from __future__ import annotations

import io
import math
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

# Vendor default; the effective base URL may be overridden per install via
# ``[brain.providers.openai].base_url`` (resolved in ``_ensure_endpoint``), and
# a team proxy re-points it transparently.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

# The universally-available OpenAI transcription model. Deliberately NOT a
# newer/fancier id (AP-21): ``whisper-1`` is the default every account can call,
# so a model-less construction never bricks for a downloader whose account has
# not been granted a preview transcription model.
DEFAULT_MODEL = "whisper-1"

# Whisper accepts up to 224 prompt tokens; ~1000 chars is a safe cap that stays
# under that even for token-dense compounds (avg ~4 chars/token). Going over
# makes the API reject the whole turn with HTTP 400 and the user experiences
# total silence — never worth saving a few extra words.
_MAX_PROMPT_CHARS = 1024


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


class OpenAIWhisperAPI:
    """OpenAI-hosted cloud STT (non-streaming, multipart transcription API).

    The provider id is ``openai-api`` (NOT ``openai``) on purpose: the OpenAI
    *brain* already owns the ``openai`` id in the shared model-catalog and
    provider-spec namespaces, so the STT variant takes a distinct id — mirroring
    the repo's own ``openrouter`` (brain) vs ``openrouter-stt`` (STT) split. The
    underlying credential (``openai_api_key``) is still SHARED with the brain, so
    no second key is needed.
    """

    name = "openai-api"
    supports_streaming = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
        timeout_s: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # ``api_key`` / ``base_url`` may be injected (team proxy / tests). When
        # left None they are resolved lazily on the first request via
        # ``resolve_provider_endpoint`` so construction stays cheap and never
        # triggers a config load on the boot critical path (AP-26).
        self._api_key = api_key or None
        self._api_key_is_explicit = bool(api_key)
        self._model = model or DEFAULT_MODEL
        self._base_url = base_url or None
        self._language = language if language and language != "auto" else None
        # Whisper ``prompt`` biases the token distribution toward the words in
        # this string — the standard trick to keep proper nouns and domain
        # vocabulary stable. Strip + cap so a whitespace-only config value
        # behaves like "unset", and an oversized one cannot crash the API with
        # HTTP 400 ("prompt too long").
        cleaned = (prompt or "").strip()
        self._prompt = cleaned[:_MAX_PROMPT_CHARS] if cleaned else None
        self._temperature = temperature
        self._timeout_s = timeout_s
        self._client = http_client
        self._owns_client = http_client is None
        self._endpoint_url: str | None = None
        self._resolved_secret_revision = -1

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
        """OpenAI has no streaming STT here — yield a single final Transcript."""
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
        PCM (mono, 16 kHz by default). We wrap it in a WAV container and POST it
        as a single multipart request.
        """
        if not pcm_bytes:
            return Transcript(text="", language="unknown", confidence=0.0)
        wav_bytes = _wrap_pcm_as_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
        # Optional per-call language override (restore afterwards).
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

    def _ensure_endpoint(self) -> str:
        """Resolve the current credential + transcription URL.

        Lazy so construction never loads config; keeps the boot path clean and
        lets the STT factory build the instance before the key is probed. A
        config-resolved key is refreshed at each transcription boundary so one
        replacement in the API-Keys view applies to brain, TTS, and STT without
        rebuilding this instance. Explicitly injected credentials remain pinned
        (team proxy / test contract). Raises a clear English error when no OpenAI
        credential is configured, so the factory / pipeline can fall back to the
        local floor (AP-22).
        """
        if (
            self._api_key_is_explicit
            and self._endpoint_url is not None
            and self._api_key
        ):
            return self._endpoint_url

        base = self._base_url or DEFAULT_BASE_URL
        if not self._api_key_is_explicit:
            # Import here (not at module top) to keep the plugin ``jarvis.*``-free
            # at import time; the entry-point loader tolerates a lazy internal use.
            from jarvis.core import config as _cfg

            current_revision = _cfg.secret_revision("openai_api_key")
            if (
                self._endpoint_url is not None
                and self._api_key
                and self._resolved_secret_revision == current_revision
            ):
                return self._endpoint_url
            ep = _cfg.resolve_provider_endpoint(
                "openai", vendor_default_base_url=DEFAULT_BASE_URL
            )
            self._api_key = ep.credential or None
            base = ep.base_url or DEFAULT_BASE_URL
            self._resolved_secret_revision = current_revision

        if not self._api_key:
            raise RuntimeError(
                "No OpenAI API key found (openai_api_key / OPENAI_API_KEY). Add "
                "it in the app's API-Keys view; the key is shared with the OpenAI "
                "brain."
            )
        self._endpoint_url = base.rstrip("/") + "/audio/transcriptions"
        return self._endpoint_url

    async def _post_transcription(self, wav_bytes: bytes) -> Transcript:
        url = self._ensure_endpoint()
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
        try:
            response = await client.post(
                url, headers=headers, data=data, files=files
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"OpenAI STT request failed (network/unreachable): {exc}"
            ) from exc

        if response.status_code >= 400:
            raise _http_error_to_runtime(response)
        payload = response.json()
        return _payload_to_transcript(payload)


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------

def _wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int, channels: int) -> bytes:
    """Wrap int16 little-endian PCM in a minimal WAV header (in memory)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(max(1, channels))
        wav.setsampwidth(2)  # int16
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def _http_error_to_runtime(response: httpx.Response) -> RuntimeError:
    """Map an HTTP error status to a clear English RuntimeError.

    401 (bad/dead key), 402 (out of credit), 429 (rate limited) and any other
    4xx/5xx all become a RuntimeError so the caller degrades honestly to the
    local floor rather than bricking the STT tier (AP-22).
    """
    status = response.status_code
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message", "")).strip()
            elif isinstance(err, str):
                detail = err.strip()
    except Exception:  # noqa: BLE001 — body may not be JSON
        detail = (response.text or "").strip()[:200]

    reason = {
        401: "invalid or missing OpenAI API key",
        402: "OpenAI account out of credit",
        429: "OpenAI rate limit / quota exceeded",
    }.get(status, f"OpenAI STT HTTP {status}")
    msg = f"OpenAI STT failed: {reason}"
    if detail:
        msg = f"{msg} ({detail})"
    return RuntimeError(msg)


def _payload_to_transcript(payload: dict[str, Any]) -> Transcript:
    """Parse OpenAI's OpenAI-shaped verbose_json response into a Transcript.

    Shape: ``{"text": ..., "language": ..., "segments": [{"start","end","text",
    "avg_logprob"}, ...]}``. When segments are present the confidence is derived
    from the mean segment ``avg_logprob`` (``exp`` of the average); otherwise it
    is a plain presence signal (1.0 for non-empty text, else 0.0) — the same
    convention the Groq plugin uses.
    """
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


__all__ = ["OpenAIWhisperAPI", "Transcript"]
