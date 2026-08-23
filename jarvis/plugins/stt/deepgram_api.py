"""Deepgram STT plugin (batch REST, Nova-3).

Cloud speech-to-text through Deepgram's ``/v1/listen`` endpoint. Deepgram is a
useful third cloud lane because its free tier is generous and its Nova-3 model
has a dedicated German recognizer plus a multilingual variant that handles
German/English code-switching in one stream — the two languages this app is
used in most.

Plugin contract: structurally compatible with ``jarvis.core.protocols.STTProvider``
without importing from ``jarvis.*`` at module level. The returned object is a
locally defined ``Transcript`` dataclass with the same field shape; consumers
duck-type on the attributes (``text``, ``language``, ``confidence``,
``is_partial``, ``segments``).

## How this differs from the OpenAI-shaped plugins

Deepgram is NOT OpenAI-compatible, and the three differences all matter:

* **Auth header is ``Token``, not ``Bearer``.** A ``Bearer`` prefix is rejected
  with 401, which reads exactly like a wrong key and sends people to rotate a
  credential that was fine.
* **The audio is the raw request body**, not a multipart field. The container
  format is declared in ``Content-Type``.
* **Options are query parameters**, not form fields, and language detection is
  its own flag (``detect_language=true``) rather than "omit the language".

## Batch, not streaming — on purpose

Deepgram's real advantage is interim results over a WebSocket, and this plugin
does not use them. Nothing in the speech pipeline consumes interim transcripts
today: ``_handle_utterance`` hands over one complete VAD-segmented utterance
and expects one final transcript. Shipping a streaming plugin into a batch-only
pipeline would add a socket and deliver no lower latency. ``stream_transcribe``
therefore yields a single final transcript, exactly like the other cloud
plugins, and the streaming path stays a separate piece of work that has to
change the pipeline to be worth anything.

API key resolution order (same convention as every other plugin here):
  1. constructor argument
  2. ``DEEPGRAM_API_KEY`` env var
  3. OS credential manager via ``keyring`` (service ``personal-jarvis``,
     username ``deepgram_api_key``) — the name the setup wizard already writes.

Never accept a key from voice/chat input (AP-2).
"""
from __future__ import annotations

import io
import os
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_ENDPOINT = "https://api.deepgram.com/v1/listen"
DEFAULT_MODEL = "nova-3"

#: The per-call ``language`` value that REQUESTS detection instead of a pinned
#: language. Spelled out per plugin because plugins may not import ``jarvis.*``.
AUTO_LANGUAGE = "auto"

#: Container extensions Deepgram accepts. Anything else is sent as WAV, which
#: is what the live microphone path always produces.
_CONTENT_TYPES: dict[str, str] = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}


def _detect_or(language: str | None, configured: str | None) -> str | None:
    """The language for ONE call. ``None`` means "let the service detect".

    Mirrors the helper in the other cloud plugins, including the case that
    matters: an explicit ``"auto"`` must CLEAR a configured pin for this call.
    Treating it as "no argument given" is what once let dictation's auto mode
    inherit ``[stt].language`` and transcribe German speech as English.
    """
    if language is None or not str(language).strip():
        return configured
    return None if str(language).strip().lower() == AUTO_LANGUAGE else str(language)


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
    #: callers that must not get an edited string — the dictation lane (which
    #: owns the user's filler switch) and wake verification.
    raw_text: str = ""


class DeepgramSTT:
    """Deepgram-hosted Nova-3 STT (cloud, batch)."""

    name = "deepgram-api"
    supports_streaming = False
    #: Concurrent ``transcribe_pcm`` calls are safe: each is one HTTP request
    #: on a shared async client with no native engine behind it, so AP-24's
    #: single-caller rule for in-process inference engines does not apply.
    supports_concurrent_requests = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        language: str | None = None,
        smart_format: bool = True,
        timeout_s: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = (
            api_key
            or os.environ.get("DEEPGRAM_API_KEY", "")
            or _read_keyring_secret("personal-jarvis", "deepgram_api_key")
        )
        self._model = model
        self._last_used_model = ""
        self._endpoint = endpoint
        self._language = language if language and language != AUTO_LANGUAGE else None
        # Punctuation, casing and number formatting. On by default because
        # every consumer downstream reads the text as prose, and un-formatted
        # Deepgram output is a lowercase unpunctuated run-on.
        self._smart_format = smart_format
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
        """One final Transcript.

        Deepgram does stream, over a WebSocket; see the module docstring for
        why this plugin deliberately does not, and what would have to change
        first for it to be worth doing.
        """
        final = await self.transcribe(audio)
        yield final

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> Transcript:
        """The path the live microphone actually takes.

        ``jarvis.speech.pipeline._handle_utterance`` delivers one complete
        VAD-segmented utterance as raw int16 PCM. ``language="auto"`` forces
        per-utterance detection for THIS call even when a language is
        configured — see :func:`_detect_or`.
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
        """Transcribe an ENCODED audio file (m4a, opus, mp3, mp4, wav, …).

        The optional capability the UltraWiki enrichment stage looks for. The
        live path delivers raw PCM, which everything else here wraps in a WAV
        container; an imported voice note is already encoded, so it is passed
        through untouched — re-wrapping it would corrupt it — with the real
        container announced in ``Content-Type``.
        """
        if not data:
            return Transcript(text="", language="unknown", confidence=0.0)
        return await self._post_transcription(
            data,
            content_type=_content_type_for(filename),
            language=_detect_or(language, self._language),
        )

    def _ensure_model(self) -> None:
        """No-op compat shim — cloud STT has nothing to warm up.

        ``jarvis.speech.pipeline._warmup`` calls this to pre-download local
        Whisper weights. Here the first request is the warm-up.
        """
        return None

    async def aclose(self) -> None:
        """Close the owned HTTP client (no-op when one was injected)."""
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

    def _query_params(self, language: str | None) -> dict[str, str]:
        """Deepgram takes its options as query parameters, not form fields."""
        params: dict[str, str] = {"model": self._model}
        if self._smart_format:
            params["smart_format"] = "true"
        if language:
            params["language"] = language
        else:
            # Deepgram needs an explicit request to detect. Omitting the
            # language does NOT mean "detect" the way it does for Whisper — it
            # means "use the model default", which is English.
            params["detect_language"] = "true"
        return params

    async def _post_transcription(
        self,
        audio_bytes: bytes,
        *,
        content_type: str = "audio/wav",
        language: str | None = None,
    ) -> Transcript:
        if not self._api_key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY missing; provide api_key=… , set the env var, "
                "or store it in the credential manager via the setup wizard."
            )

        headers = {
            # Deepgram's scheme keyword is "Token". A "Bearer" prefix is
            # rejected with 401, which looks exactly like a bad key and sends
            # people off to rotate a credential that was never the problem.
            "Authorization": f"Token {self._api_key}",
            "Content-Type": content_type,
        }
        response = await self._get_client().post(
            self._endpoint,
            headers=headers,
            params=self._query_params(language),
            content=audio_bytes,
        )
        # Raise the httpx error rather than flattening it: it carries the
        # status on ``.response.status_code`` and ``Retry-After`` on
        # ``.response.headers``, which is what lets the pipeline's transient
        # retry ladder tell a rate limit apart from a bad key.
        response.raise_for_status()
        transcript = _payload_to_transcript(response.json(), fallback_language=language)
        self._last_used_model = self._model
        return transcript


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------

def _read_keyring_secret(service: str, username: str) -> str:
    """Best-effort credential-manager lookup. Returns ``""`` on any failure."""
    # No jarvis.* import here (plugin purity contract): the host's plugin
    # loader installs the process-wide keyring backend — on macOS the
    # single-vault-item wrapper (BUG-103) — before this module is imported, so
    # this direct read is served from the bundled vault rather than a per-item
    # Keychain entry with its own permission dialog. Standalone use degrades to
    # the plain OS keyring.
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        # keyring is a soft dependency; a base install without it simply has no
        # vault to read, which is "no key" and is already reported honestly by
        # the caller. Logging it on every lookup would be noise, not a finding.
        return ""
    try:
        return keyring.get_password(service, username) or ""
    except Exception:  # noqa: BLE001 — a locked or absent vault is "no key",
        # which the caller already reports honestly; re-raising here would turn
        # a missing optional credential into a crash.
        return ""


def _content_type_for(filename: str) -> str:
    """Announce the real container, defaulting to WAV for anything unknown."""
    from pathlib import PurePosixPath  # noqa: PLC0415 — tiny, local

    suffix = PurePosixPath(str(filename or "")).suffix.lower()
    return _CONTENT_TYPES.get(suffix, "audio/wav")


def _wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int, channels: int) -> bytes:
    """Wrap int16 little-endian PCM in a minimal WAV header (in memory)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(max(1, channels))
        wav.setsampwidth(2)  # int16
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def _payload_to_transcript(
    payload: dict[str, Any], *, fallback_language: str | None = None
) -> Transcript:
    """Parse Deepgram's ``/v1/listen`` response into a Transcript.

    The response nests the result under
    ``results.channels[0].alternatives[0]``. Every level is read defensively
    because a request that produced no speech returns the same envelope with an
    empty alternative rather than a distinct shape.

    The text is filtered on the way in (``clean_stt_text``), which is the last
    point where recognizer artifacts can be removed before every consumer
    downstream reads the string. The untouched text stays on ``raw_text``.
    """
    results = payload.get("results") or {}
    channels = results.get("channels") or []
    channel = channels[0] if channels else {}
    alternatives = channel.get("alternatives") or []
    alternative = alternatives[0] if alternatives else {}

    raw = str(alternative.get("transcript", "")).strip()
    # Deepgram reports a real 0..1 confidence per alternative, so unlike the
    # Whisper plugins there is no log-probability to exponentiate. A response
    # with text but no confidence field is treated as certain rather than as
    # silence.
    confidence = float(alternative.get("confidence", 1.0 if raw else 0.0))

    language = str(
        channel.get("detected_language") or fallback_language or "unknown"
    )

    # Utterances are the closest thing to Whisper's segments and carry the
    # timings the flight recorder reads. They are only present when the
    # request asked for them, so an empty tuple is the normal case here.
    seg_tuple: tuple[dict[str, Any], ...] = tuple(
        {
            "start": float(u.get("start", 0.0)),
            "end": float(u.get("end", 0.0)),
            "text": str(u.get("transcript", "")),
            "avg_logprob": 0.0,
        }
        for u in (results.get("utterances") or ())
    )

    # Local import, like the credential lookup: the module top stays
    # ``jarvis.*``-free (entry-point purity).
    from jarvis.plugins.stt.transcript_filter import clean_stt_text

    return Transcript(
        text=clean_stt_text(raw, language=language),
        language=language,
        confidence=min(1.0, max(0.0, confidence)),
        is_partial=False,
        segments=seg_tuple,
        raw_text=raw,
    )


__all__ = ["DeepgramSTT", "Transcript"]
