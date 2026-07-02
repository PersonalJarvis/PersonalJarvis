"""OpenRouter STT plugin — cloud transcription via the OpenRouter gateway.

One OpenRouter API key unlocks a whole family of hosted transcription models
(Whisper, GPT-4o-transcribe, Chirp, Voxtral, Parakeet, Qwen3-ASR, …). This
plugin reuses the SAME ``openrouter_api_key`` slot the OpenRouter *brain*
already uses, so a user who configured OpenRouter for chat gets cloud STT for
free — no second credential.

Endpoint (verified live 2026-07-02):
  * ``POST {base_url}/audio/transcriptions``
  * Headers: ``Authorization: Bearer <key>``, ``Content-Type: application/json``
    (plus the courtesy ``HTTP-Referer`` / ``X-Title`` OpenRouter attribution
    headers the brain adapter also sends).
  * JSON body: ``{"model": "<id>", "input_audio": {"data": "<base64 RAW audio
    bytes, NOT a data-URI>", "format": "wav"}, "language": "<ISO-639-1>"?,
    "temperature": <0-1>?}``.
  * JSON response: ``{"text": "...", "usage": {"seconds": ..., "cost": ...,
    "total_tokens": ...?}}`` with an ``X-Generation-Id`` response header. No
    streaming — a single final ``Transcript`` is returned.

Plugin contract: structurally compatible with
``jarvis.core.protocols.STTProvider`` WITHOUT importing ``jarvis.*`` from the
plugin module (entry-point plugins must stay import-clean). The returned object
is a locally defined ``Transcript`` dataclass with the identical field shape;
consumers duck-type on ``text`` / ``language`` / ``confidence`` / ``is_partial``
/ ``segments``.

Audio I/O contract (compatible with the Jarvis VAD output):
  * ``transcribe`` consumes chunks exposing ``.pcm`` (int16 little-endian
    bytes), ``.sample_rate`` (Hz) and optionally ``.channels`` (default 1).
  * ``transcribe_pcm`` receives a full VAD-segmented utterance as raw int16 PCM
    at 16 kHz mono (the pipeline default) — the drop-in shim the speech
    pipeline actually calls.
  * All PCM is wrapped in an in-memory WAV container before base64 upload.

Credential resolution reuses ``jarvis.core.config.resolve_provider_endpoint``
(keyring → ENV → .env → local-file fallback), exactly like the OpenRouter
brain. A missing / dead (401) / out-of-credit (402) / rate-limited (429) /
unreachable key raises a clear English error so the STT factory can degrade to
the key-free local ``faster-whisper`` floor instead of bricking voice input
for a single-provider user (AP-22). Never accept a key from voice/chat (AP-2).
"""
from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

# Vendor default; the effective base URL may be overridden per install via
# ``[brain.providers.openrouter].base_url`` (resolved in ``_ensure_endpoint``).
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Default transcription model. Chosen deliberately (verified against the live
# ``/api/v1/models?output_modalities=transcription`` catalog 2026-07-02):
#   * multilingual + robust (Jarvis defaults to bilingual DE+EN auto-detect),
#   * mid-priced — NOT the most expensive transcription model on the gateway
#     (``whisper-large-v3-turbo`` is ~25x dearer), so a model-less construction
#     never silently bills a premium engine (§3 / AP-22),
#   * identical to the existing Groq STT default (``whisper-large-v3``), so
#     switching STT providers keeps transcription behaviour consistent.
# A user who wants the cheapest option can pick ``openai/gpt-4o-mini-transcribe``
# in the model dropdown; the picker only offers transcription-capable models.
DEFAULT_MODEL = "openai/whisper-large-v3"

# The OpenRouter attribution headers (same values the brain adapter sends).
_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://github.com/PersonalJarvis",
    "X-Title": "Personal Jarvis",
}


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


class OpenRouterSTT:
    """OpenRouter-hosted cloud STT (non-streaming, JSON transcription API)."""

    name = "openrouter"
    supports_streaming = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float | None = None,
        timeout_s: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # ``api_key`` / ``base_url`` may be injected (e.g. team-proxy). When left
        # None they are resolved lazily on the first request via
        # ``resolve_provider_endpoint`` so construction stays cheap and never
        # triggers a config load on the boot critical path (AP-26).
        self._api_key = api_key or None
        self._model = model or DEFAULT_MODEL
        self._base_url = base_url or None
        self._language = language if language and language != "auto" else None
        # ``prompt`` (bias vocabulary) is accepted for STT-factory kwarg
        # compatibility but NOT forwarded: the OpenRouter transcription endpoint
        # exposes no documented bias-prompt parameter, and sending an
        # unsupported field risks a hard 400 that silences the whole turn. It is
        # stored only so a future API revision could opt in without a signature
        # change.
        self._prompt = (prompt or "").strip() or None
        self._temperature = temperature
        self._timeout_s = timeout_s
        self._client = http_client
        self._owns_client = http_client is None
        self._endpoint_url: str | None = None

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
        """OpenRouter has no streaming STT — yield a single final Transcript."""
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
        as a single JSON request.
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
        """Resolve (and cache) the credential + transcription URL.

        Lazy so construction never loads config; keeps the boot path clean and
        lets the STT factory build the instance before the key is probed. Raises
        a clear English error when no OpenRouter credential is configured, so the
        factory / pipeline can fall back to the local floor (AP-22).
        """
        if self._endpoint_url is not None and self._api_key:
            return self._endpoint_url

        base = self._base_url
        if not self._api_key or not base:
            # Import here (not at module top) to keep the plugin ``jarvis.*``-free
            # at import time; the entry-point loader tolerates a lazy internal use.
            from jarvis.core import config as _cfg

            ep = _cfg.resolve_provider_endpoint(
                "openrouter", vendor_default_base_url=DEFAULT_BASE_URL
            )
            if not self._api_key:
                self._api_key = ep.credential or None
            if not base:
                base = ep.base_url or DEFAULT_BASE_URL

        if not self._api_key:
            raise RuntimeError(
                "No OpenRouter API key found (openrouter_api_key). Add it in the "
                "app's API-Keys view; the key is shared with the OpenRouter brain."
            )
        self._endpoint_url = base.rstrip("/") + "/audio/transcriptions"
        return self._endpoint_url

    async def _post_transcription(self, wav_bytes: bytes) -> Transcript:
        import base64

        url = self._ensure_endpoint()
        body: dict[str, Any] = {
            "model": self._model,
            "input_audio": {
                "data": base64.b64encode(wav_bytes).decode("ascii"),
                "format": "wav",
            },
        }
        if self._language:
            body["language"] = self._language
        # Temperature is omitted unless explicitly configured: keeping the body
        # minimal maximises portability across the ~10 transcription backends the
        # gateway fronts (some reject unexpected fields).
        if self._temperature is not None:
            body["temperature"] = float(self._temperature)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **_ATTRIBUTION_HEADERS,
        }
        client = self._get_client()
        try:
            response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"OpenRouter STT request failed (network/unreachable): {exc}"
            ) from exc

        if response.status_code >= 400:
            raise _http_error_to_runtime(response)
        payload = response.json()
        return _payload_to_transcript(payload)


# ----------------------------------------------------------------------
# Transcription-model filter (VERIFIED predicate, isolated + unit-testable)
# ----------------------------------------------------------------------
#
# The STT model picker must offer ONLY transcription-capable models — never a
# chat, embedding, or TTS model. Verified against the live OpenRouter catalog
# (2026-07-02): EVERY dedicated transcription model, and ONLY those, declares
#
#     architecture.modality           == "audio->transcription"
#     architecture.input_modalities   == ["audio"]
#     architecture.output_modalities  == ["transcription"]
#
# The reliable, single-field predicate is therefore
# ``"transcription" in architecture.output_modalities``. This cleanly excludes:
#   * plain chat models (output ``["text"]``),
#   * audio-IN chat models like ``google/gemini-2.5-pro`` or ``openai/gpt-audio``
#     (they accept audio but output ``["text"]`` / ``["text","audio"]``, never
#     ``"transcription"``),
#   * image/audio GENERATION models (``["image"]`` / ``["audio"]``).
# Equivalent server-side filter: ``GET /api/v1/models?output_modalities=transcription``.

_TRANSCRIPTION_MODALITY = "transcription"


def _model_output_modalities(model: Any) -> tuple[str, ...] | None:
    """Extract declared output modalities from either shape.

    Accepts BOTH a parsed ``ModelInfo``-like object (an ``.output_modalities``
    attribute) AND a raw OpenRouter ``/v1/models`` entry dict (nested under
    ``architecture.output_modalities``, or a flat ``output_modalities``). Returns
    ``None`` when the field is absent/unusable (→ treated as not-transcription).
    """
    # 1) Object with an ``output_modalities`` attribute (e.g. ModelInfo).
    attr = getattr(model, "output_modalities", None)
    if attr is not None and not isinstance(model, dict):
        return tuple(str(x) for x in attr) if _is_seq(attr) else None

    if isinstance(model, dict):
        arch = model.get("architecture")
        if isinstance(arch, dict):
            mods = arch.get("output_modalities")
            if _is_seq(mods):
                return tuple(str(x) for x in mods)
        flat = model.get("output_modalities")
        if _is_seq(flat):
            return tuple(str(x) for x in flat)
    return None


def _is_seq(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def is_transcription_model(model: Any) -> bool:
    """True iff ``model`` is a dedicated transcription (STT) model.

    ``model`` may be a parsed ``ModelInfo`` (``.output_modalities``) or a raw
    OpenRouter ``/v1/models`` entry dict. The predicate is the single verified
    marker ``"transcription" in output_modalities`` (see the module comment).
    """
    mods = _model_output_modalities(model)
    return mods is not None and _TRANSCRIPTION_MODALITY in mods


def filter_stt_models(models: list[Any]) -> list[Any]:
    """Keep only transcription-capable models, preserving input order.

    Used by the STT model picker so the dropdown can never offer a chat /
    embedding / TTS model. Capability-based (never provider-name / id-substring
    based), so it stays correct as the gateway's model roster changes (AP-21).
    """
    return [m for m in models if is_transcription_model(m)]


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
        401: "invalid or missing OpenRouter API key",
        402: "OpenRouter account out of credit",
        429: "OpenRouter rate limit / quota exceeded",
    }.get(status, f"OpenRouter STT HTTP {status}")
    msg = f"OpenRouter STT failed: {reason}"
    if detail:
        msg = f"{msg} ({detail})"
    return RuntimeError(msg)


def _payload_to_transcript(payload: dict[str, Any]) -> Transcript:
    """Parse OpenRouter's transcription JSON into a Transcript.

    Shape (verified): ``{"text": "...", "usage": {...}}``. The endpoint returns
    no per-segment timings or confidence, so confidence is a plain presence
    signal (1.0 when non-empty text, else 0.0) and segments stay empty — the
    same convention the Groq plugin uses when segments are absent.
    """
    text = str(payload.get("text", "")).strip()
    language = str(payload.get("language", "") or "unknown") or "unknown"
    return Transcript(
        text=text,
        language=language,
        confidence=1.0 if text else 0.0,
        is_partial=False,
        segments=(),
    )


__all__ = [
    "OpenRouterSTT",
    "Transcript",
    "is_transcription_model",
    "filter_stt_models",
]
