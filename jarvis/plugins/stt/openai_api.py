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
  * multipart fields: ``file`` (an in-memory WAV), ``model``, ``response_format``,
    optional ``language`` / ``prompt`` / ``temperature``.

The wire contract is NOT the same for every model, which is why the request is
built from a per-model capability shape (:mod:`jarvis.plugins.stt.capabilities`)
rather than from constants: ``whisper-1`` answers ``verbose_json`` with segment
timings and log probabilities, while ``gpt-4o-transcribe`` rejects that value
with HTTP 400 and transcribes nothing. A 400 that names an optional field
narrows the shape and retries; a 400 that names the MODEL falls back to the
default model once, loudly.

Model default: ``whisper-1`` — the universally-available transcription model
every OpenAI account can call. Gate on the capability, never a fancier model id
(AP-21); a user who wants a newer transcription model sets it in the STT model
field and the request adapts itself to that model's contract.

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
    #: What the vendor returned BEFORE the cleanup filter ran. Read by the two
    #: callers that must not get an edited string - the dictation lane (which
    #: owns the user's filler switch) and wake verification.
    raw_text: str = ""


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
        self._last_used_model = ""
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

        ``language="auto"`` forces per-utterance detection for THIS call even
        when a language is configured — see :func:`_detect_or`.
        """
        if not pcm_bytes:
            return Transcript(text="", language="unknown", confidence=0.0)
        wav_bytes = _wrap_pcm_as_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
        return await self._post_transcription(
            wav_bytes, language=_detect_or(language, self._language)
        )

    async def transcribe_container(
        self, data: bytes, *, filename: str = "recording", language: str | None = None
    ) -> Transcript:
        """Transcribe an ENCODED audio file (m4a, opus, mp3, mp4, wav, ...).

        The optional capability the UltraWiki enrichment stage looks for. The
        live microphone path delivers raw PCM, which is why everything else
        here wraps PCM in a WAV container — but an imported voice note arrives
        already encoded, and this endpoint accepts those formats directly.
        Decoding them locally would mean shipping a media stack to every
        install, including headless servers that have no other use for one.

        The container is passed through untouched: the service identifies the
        format itself, and re-wrapping it would be the one way to corrupt it.
        """
        if not data:
            return Transcript(text="", language="unknown", confidence=0.0)
        return await self._post_transcription(
            data, filename=filename, language=_detect_or(language, self._language)
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

    async def _post_transcription(
        self,
        wav_bytes: bytes,
        *,
        filename: str = "audio.wav",
        language: str | None = None,
    ) -> Transcript:
        """POST one upload, adapting the request to what the MODEL accepts.

        ``whisper-1`` answers ``verbose_json``; ``gpt-4o-transcribe`` — the
        newer multilingual model — rejects that value with HTTP 400 and
        transcribes nothing. The request is therefore built from a per-model
        capability shape, and a 400 that names an optional field narrows that
        shape and retries instead of losing the utterance
        (:mod:`jarvis.plugins.stt.capabilities`).
        """
        url = self._ensure_endpoint()
        # The NAME is how the service identifies the container, so an imported
        # `.opus` must not be announced as `audio.wav`. The content type stays
        # generic: the extension is the signal these APIs actually read.
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
        # One attempt per optional field that could still be dropped, plus one
        # for the model substitution below. Bounded so a service answering 400
        # to everything ends in an honest error rather than a retry loop.
        for _attempt in range(MAX_REQUEST_DOWNGRADES):
            # A fresh file tuple per attempt: httpx consumes the buffer it is
            # handed, so a retry with the same object uploads zero bytes.
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

            try:
                response = await client.post(
                    url, headers=headers, data=data, files=files
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"OpenAI STT request failed (network/unreachable): {exc}"
                ) from exc

            if response.status_code < 400:
                transcript = _payload_to_transcript(response.json())
                self._last_used_model = model
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
                # The pinned model is not one this account can call. Falling
                # back to the universally-available one keeps the user
                # dictating; the warning is what tells them to fix the pin.
                log_model_fallback("OpenAI", model, DEFAULT_MODEL, detail)
                model = DEFAULT_MODEL
                shape = resolve_shape(self.name, model)
                continue
            raise _http_error_to_runtime(response)

        raise _http_error_to_runtime(response)


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------

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


def _http_error_to_runtime(response: httpx.Response) -> RuntimeError:
    """Map an HTTP error status to a clear English, CLASSIFIABLE error.

    401 (bad/dead key), 402 (out of credit), 429 (rate limited) and any other
    4xx/5xx all become an ``STTHTTPError`` — still a ``RuntimeError``, so the
    caller degrades honestly to the local floor exactly as before (AP-22), but
    now carrying the ``status`` and the server's ``Retry-After``. Without those
    the pipeline's transient-error retry ladder was dead code for this plugin
    (it could only read a status off the one provider that raised an
    ``httpx.HTTPStatusError``), so an OpenAI-key user lost the whole turn to the
    first rate limit. The English wording is unchanged; only the type is.
    """
    # Imported here, not at module top, to keep the plugin ``jarvis.*``-free at
    # import time (the entry-point purity contract) — the same lazy seam the
    # credential lookup uses. This runs only on an already-failed request, so
    # the import costs nothing on the happy path.
    from jarvis.plugins.stt.errors import http_error_from_response

    return http_error_from_response(response, vendor="OpenAI")


def _payload_to_transcript(payload: dict[str, Any]) -> Transcript:
    """Parse OpenAI's OpenAI-shaped verbose_json response into a Transcript.

    Shape: ``{"text": ..., "language": ..., "segments": [{"start","end","text",
    "avg_logprob"}, ...]}``. When segments are present the confidence is derived
    from the mean segment ``avg_logprob`` (``exp`` of the average); otherwise it
    is a plain presence signal (1.0 for non-empty text, else 0.0) — the same
    convention the Groq plugin uses.

    The text is filtered on the way in (see
    :func:`jarvis.plugins.stt.transcript_filter.clean_stt_text`) and the
    untouched payload text stays on ``raw_text``. Per-segment texts are left as
    delivered: they carry timings, and nothing reads them as prose.
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
        avg = sum(s["avg_logprob"] for s in seg_tuple) / len(seg_tuple)
        try:
            confidence = float(math.exp(avg))
        except OverflowError:
            confidence = 0.0
    else:
        # Presence is judged on the RAW text: a cleanup that emptied the string
        # is a filter defect, and reporting 0.0 would call it silence instead.
        confidence = 1.0 if raw else 0.0

    # Local import (entry-point purity), same seam as the error mapper.
    from jarvis.plugins.stt.transcript_filter import clean_stt_text

    return Transcript(
        text=clean_stt_text(raw, language=language),
        language=language,
        confidence=min(1.0, max(0.0, confidence)),
        is_partial=False,
        segments=seg_tuple,
        raw_text=raw,
    )


__all__ = ["OpenAIWhisperAPI", "Transcript"]
