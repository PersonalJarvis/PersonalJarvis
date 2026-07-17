"""Provider-level TTS fallback wrapper.

Honors the ``[tts].fallback`` config field, which was previously dead: the
factory built only the primary provider and nothing ever read ``fallback``.
That meant a primary-provider failure produced **total silence** instead of
degrading to the configured backup voice — the exact failure mode behind the
2026-05-31 "Jarvis hears + thinks but never answers" incident (a misconfigured
Vertex project raised inside ``GeminiFlashTTS._ensure_client`` on every
sentence, and there was no provider-level fallback for the gemini-flash-tts
primary; only ``GrokVoiceTTS`` had its own internal cross-provider chain).

``FallbackTTS`` wraps a primary provider and transparently switches to a
secondary provider when the primary cannot produce audio. This is the
``zero silent drops`` contract (AD-OE6) applied to the TTS layer.

Switch policy (deliberately conservative to never duplicate audio):
  - primary raises **before** yielding any chunk  -> use fallback
  - primary completes yielding **zero** chunks      -> use fallback
  - primary yields ≥1 chunk then raises             -> re-raise (cannot
    cleanly restart mid-utterance; falling back would replay the start)

The wrapper exposes exactly the surface the speech pipeline touches on a TTS
instance: ``synthesize(...)`` and ``_ensure_client()`` (called during warmup).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger("jarvis.tts.fallback")


class FallbackTTS:
    """Wraps ``primary`` and falls back to ``fallback`` on failure/empty output.

    Structurally compatible with the ``TTSProvider`` protocol — it forwards to
    the wrapped providers and never imports concrete plugin types.
    """

    def __init__(
        self, primary: Any, fallback: Any, fallback_voice: str | None = None
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        # Voice-profile continuity (2026-07-17): the curated voice of the
        # fallback FAMILY that matches the primary's active voice profile
        # (masculine/feminine), computed by the factory. None → the fallback
        # resolves its own default, exactly as before.
        self._fallback_voice = (fallback_voice or "").strip() or None
        # Surface the primary's identity so callers/logs see the active voice.
        self.name = getattr(primary, "name", "fallback-tts")
        self.supports_streaming = bool(getattr(primary, "supports_streaming", True))

    @property
    def primary(self) -> Any:
        return self._primary

    @property
    def fallback(self) -> Any:
        return self._fallback

    def _ensure_client(self) -> None:
        """Pre-warm both providers during pipeline warmup.

        MUST NOT raise: a primary init failure (e.g. a misconfigured Vertex
        project) must not kill warmup — the real switch happens lazily at synth
        time. We best-effort warm both so the fallback is ready the instant the
        primary fails on the first real utterance.
        """
        for tts, label in ((self._primary, "primary"), (self._fallback, "fallback")):
            ensure = getattr(tts, "_ensure_client", None)
            if ensure is None:
                continue
            try:
                ensure()
            except Exception as exc:  # noqa: BLE001 — warmup must never crash
                log.warning(
                    "TTS %s provider %r _ensure_client failed during warmup: %s",
                    label, getattr(tts, "name", "?"), exc,
                )

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[Any]:
        cleaned = (text or "").strip()
        if not cleaned:
            return

        produced = 0
        try:
            async for chunk in self._primary.synthesize(
                text, voice=voice, language_code=language_code
            ):
                produced += 1
                yield chunk
        except Exception as exc:  # noqa: BLE001 — any primary failure -> fallback
            if produced:
                # Audio already reached the speaker; restarting from the
                # fallback would replay the opening words. Surface the error
                # instead of producing garbled double audio.
                log.error(
                    "Primary TTS %r failed mid-stream after %d chunk(s): %s — "
                    "NOT falling back (would duplicate audio).",
                    self.name, produced, exc,
                )
                raise
            log.warning(
                "Primary TTS %r failed before any audio (%s) — falling back to %r.",
                self.name, exc, getattr(self._fallback, "name", "?"),
            )
            async for chunk in self._synthesize_fallback(cleaned, language_code):
                yield chunk
            return

        if produced == 0:
            log.warning(
                "Primary TTS %r produced no audio — falling back to %r.",
                self.name, getattr(self._fallback, "name", "?"),
            )
            async for chunk in self._synthesize_fallback(cleaned, language_code):
                yield chunk

    async def _synthesize_fallback(
        self, text: str, language_code: str | None
    ) -> AsyncIterator[Any]:
        """Stream from the fallback provider.

        The primary's ``voice`` is never forwarded raw: its name (e.g. Gemini
        "Charon") is invalid for a different provider (e.g. Grok expects
        "leo") and would trigger an HTTP 400. Instead the factory pre-computes
        ``fallback_voice`` — the fallback family's curated voice matching the
        primary's voice PROFILE — so the takeover doesn't audibly flip e.g.
        masculine→feminine mid-conversation. Without a match the fallback
        resolves its own default voice, as before.
        """
        try:
            async for chunk in self._fallback.synthesize(
                text, voice=self._fallback_voice, language_code=language_code
            ):
                yield chunk
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Fallback TTS %r also failed (%s) — no audio for this utterance.",
                getattr(self._fallback, "name", "?"), exc,
            )
