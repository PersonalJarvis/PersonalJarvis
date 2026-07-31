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
    "temperature": <0-1>?}``. Which of the optional fields a given model
    accepts is a per-MODEL question the gateway answers with 400, so the body
    is shaped by :mod:`jarvis.plugins.stt.capabilities` and narrowed on a
    refusal rather than pinned to a lowest common denominator.
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

# OpenRouter exposes vocabulary priming through provider-specific passthrough
# options. Keep the same conservative ceiling as the direct Whisper adapters so
# one dictionary cannot turn a transcription into a request-size failure.
_MAX_PROMPT_CHARS = 1000

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

    ``raw_text`` is additive and optional: it carries what the gateway returned
    BEFORE the cleanup filter ran. Consumers that want the cleaned sentence keep
    reading ``text`` and never notice it; the one caller that must not get a
    cleaned string — the dictation lane, whose whole promise is "these are my
    words" and which owns a user switch for filler removal — reads it through a
    ``getattr`` default, so every other provider stays unchanged.
    """

    text: str
    language: str
    confidence: float
    is_partial: bool = False
    segments: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    raw_text: str = ""


class OpenRouterSTT:
    """OpenRouter-hosted cloud STT (non-streaming, JSON transcription API).

    The provider id is ``openrouter-stt`` (NOT ``openrouter``) on purpose: the
    OpenRouter *brain* already owns the ``openrouter`` id in the shared model-
    catalog and provider-spec namespaces, so the STT variant takes a distinct id
    — mirroring the repo's own ``openai`` (brain) vs ``openai-api`` (STT) split.
    The underlying credential (``openrouter_api_key``) is still SHARED with the
    brain, so no second key is needed.
    """

    name = "openrouter-stt"
    supports_streaming = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        language: str | None = None,
        prompt: str | None = None,
        # 0.0 by DEFAULT, not "omit unless configured". Transcription is a
        # measurement, and the same recording has to come back the same way
        # twice — a gateway default that samples turns an unchanged dictation
        # into a different sentence on every retry, which is exactly the kind of
        # variance a user reads as "it got worse". The field is dropped
        # automatically for a model that refuses it (see ``_post_transcription``),
        # so the reproducibility costs no portability.
        temperature: float | None = 0.0,
        timeout_s: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # ``api_key`` / ``base_url`` may be injected (e.g. team-proxy). When left
        # None they are resolved lazily on the first request via
        # ``resolve_provider_endpoint`` so construction stays cheap and never
        # triggers a config load on the boot critical path (AP-26).
        self._api_key = api_key or None
        self._api_key_is_explicit = bool(api_key)
        self._model = model or DEFAULT_MODEL
        self._last_used_model = ""
        self._last_usage_cost_usd: float | None = None
        self._base_url = base_url or None
        self._language = language if language and language != "auto" else None
        # The gateway exposes bias through provider-specific passthrough rather
        # than a top-level field. Only the matched upstream receives it, and the
        # request-shape downgrade below removes it when that model rejects it.
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

    @property
    def last_used_model(self) -> str:
        """Effective model that produced the latest successful transcript."""
        return self._last_used_model

    @property
    def last_usage_cost_usd(self) -> float | None:
        """Billed cost reported for the latest successful gateway response."""
        return self._last_usage_cost_usd

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
        (team proxy / embedding contract). Raises a clear English error when no
        OpenRouter credential is configured, so the factory / pipeline can fall
        back to the local floor (AP-22).
        """
        if self._api_key_is_explicit and self._endpoint_url is not None and self._api_key:
            return self._endpoint_url

        base = self._base_url or DEFAULT_BASE_URL
        if not self._api_key_is_explicit:
            # Import here (not at module top) to keep the plugin ``jarvis.*``-free
            # at import time; the entry-point loader tolerates a lazy internal use.
            from jarvis.core import config as _cfg

            current_revision = _cfg.secret_revision("openrouter_api_key")
            if (
                self._endpoint_url is not None
                and self._api_key
                and self._resolved_secret_revision == current_revision
            ):
                return self._endpoint_url
            ep = _cfg.resolve_provider_endpoint(
                "openrouter", vendor_default_base_url=DEFAULT_BASE_URL
            )
            self._api_key = ep.credential or None
            base = ep.base_url or DEFAULT_BASE_URL
            self._resolved_secret_revision = current_revision

        if not self._api_key:
            raise RuntimeError(
                "No OpenRouter API key found (openrouter_api_key). Add it in the "
                "app's API-Keys view; the key is shared with the OpenRouter brain."
            )
        self._endpoint_url = base.rstrip("/") + "/audio/transcriptions"
        return self._endpoint_url

    async def _post_transcription(
        self, wav_bytes: bytes, *, language: str | None = None
    ) -> Transcript:
        """POST one upload, adapting the body to what the MODEL accepts.

        The gateway fronts ~10 transcription backends whose contracts differ,
        so the body is built from a per-model capability shape
        (:mod:`jarvis.plugins.stt.capabilities`) and a 400 that names a field
        narrows that shape and retries. That is what lets this plugin send a
        temperature by DEFAULT: the field used to be omitted unless configured,
        purely because one unsupported field would have cost the whole
        utterance — and the cost of omitting it was a transcription that came
        back differently every time the same audio was sent.
        """
        import base64

        # Never let a previous successful request's cost leak into a failed
        # evaluation sample. The harness reads this only after the call returns.
        self._last_usage_cost_usd = None

        url = self._ensure_endpoint()
        encoded = base64.b64encode(wav_bytes).decode("ascii")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **_ATTRIBUTION_HEADERS,
        }
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
            body: dict[str, Any] = {
                "model": model,
                "input_audio": {"data": encoded, "format": "wav"},
            }
            # Omitted entirely when None — that is what asks the model to detect
            # the spoken language instead of decoding it as a pinned one.
            if language and shape.language:
                body["language"] = language
            if self._temperature is not None and shape.temperature:
                body["temperature"] = float(self._temperature)
            if self._prompt and shape.prompt:
                body["provider"] = {
                    "options": {"groq": {"prompt": self._prompt}}
                }

            try:
                response = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"OpenRouter STT request failed (network/unreachable): {exc}"
                ) from exc

            if response.status_code < 400:
                payload = response.json()
                transcript = _payload_to_transcript(payload)
                self._last_used_model = model
                self._last_usage_cost_usd = _payload_cost_usd(payload)
                return transcript
            if response.status_code != 400:
                raise _http_error_to_runtime(response)

            detail = error_text(response)
            narrowed = shape_after_rejection(shape, detail)
            if narrowed is not None:
                remember_shape(self.name, model, narrowed)
                shape = narrowed
                continue
            if model != DEFAULT_MODEL and is_model_rejection(detail, model):
                log_model_fallback("OpenRouter", model, DEFAULT_MODEL, detail)
                model = DEFAULT_MODEL
                shape = resolve_shape(self.name, model)
                continue
            raise _http_error_to_runtime(response)

        raise _http_error_to_runtime(response)


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
    if not isinstance(model, dict):
        attr = getattr(model, "output_modalities", None)
        if isinstance(attr, (list, tuple)):
            return tuple(str(x) for x in attr)
        return None

    # 2) Raw OpenRouter ``/v1/models`` entry dict.
    arch = model.get("architecture")
    if isinstance(arch, dict):
        mods = arch.get("output_modalities")
        if isinstance(mods, (list, tuple)):
            return tuple(str(x) for x in mods)
    flat = model.get("output_modalities")
    if isinstance(flat, (list, tuple)):
        return tuple(str(x) for x in flat)
    return None


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
    """Map an HTTP error status to a clear English, CLASSIFIABLE error.

    401 (bad/dead key), 402 (out of credit), 429 (rate limited) and any other
    4xx/5xx all become an ``STTHTTPError`` — still a ``RuntimeError``, so the
    caller degrades honestly to the local floor exactly as before (AP-22), but
    now carrying the ``status`` and the server's ``Retry-After``. Without those
    the pipeline's transient-error retry ladder was dead code for this plugin
    (it could only read a status off the one provider that raised an
    ``httpx.HTTPStatusError``), so an OpenRouter-key user lost the whole turn to
    the first rate limit. The English wording is unchanged; only the type is.
    """
    # Imported here, not at module top, to keep the plugin ``jarvis.*``-free at
    # import time (the entry-point purity contract) — the same lazy seam the
    # credential lookup uses. This runs only on an already-failed request, so
    # the import costs nothing on the happy path.
    from jarvis.plugins.stt.errors import http_error_from_response

    return http_error_from_response(response, vendor="OpenRouter")


def _payload_to_transcript(payload: dict[str, Any]) -> Transcript:
    """Parse OpenRouter's transcription JSON into a Transcript.

    Shape (verified): ``{"text": "...", "usage": {...}}``. The endpoint returns
    no per-segment timings or confidence, so confidence is a plain presence
    signal (1.0 when non-empty text, else 0.0) and segments stay empty — the
    same convention the Groq plugin uses when segments are absent.

    The text is run through :func:`jarvis.plugins.stt.transcript_filter.clean_stt_text`
    on the way in, which is the last point where the gateway's own artifacts —
    a decoder repetition loop, a hesitation sound, a stutter, an outer quote
    pair, NFD umlauts — can be removed before every consumer downstream starts
    reading the string. The untouched payload text stays on ``raw_text``.

    Confidence is computed from the RAW text, not the cleaned one. The two only
    diverge when cleanup emptied the string, and that is a cleanup defect, not
    a silent utterance — reporting 0.0 there would hand the pipeline a "nothing
    was said" verdict about audio that contained speech.
    """
    raw = str(payload.get("text", "")).strip()
    language = str(payload.get("language", "") or "unknown") or "unknown"
    # Local import: the module top must stay ``jarvis.*``-free (entry-point
    # purity), the same lazy seam the credential lookup and the error mapper
    # already use.
    from jarvis.plugins.stt.transcript_filter import clean_stt_text

    text = clean_stt_text(raw, language=language)
    return Transcript(
        text=text,
        language=language,
        confidence=1.0 if raw else 0.0,
        is_partial=False,
        segments=(),
        raw_text=raw,
    )


def _payload_cost_usd(payload: dict[str, Any]) -> float | None:
    """Return OpenRouter's billed USD amount when the response carries one."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        value = float(usage.get("cost"))
    except (TypeError, ValueError):  # optional telemetry: malformed means absent
        return None
    return value if value >= 0.0 else None


__all__ = [
    "OpenRouterSTT",
    "Transcript",
    "is_transcription_model",
    "filter_stt_models",
]
