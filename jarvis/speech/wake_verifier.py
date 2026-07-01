"""Post-detection prefix verifier for the OpenWakeWord wake path.

The neural ``hey_jarvis_v0.1`` model generalises onto bare "Jarvis" because
its features overlap with the full "Hey Jarvis" utterance. Pendulum-style
threshold edits have been forbidden by ``tests/unit/speech/test_wake_threshold``
since BUG-009 episode 5. The clean fix is a second-stage check: when OWW
fires, transcribe the few seconds preceding the hit with the cloud STT that
the rest of the voice path already uses (Groq by default) and require a
strict "hey/hi/hallo + jarv" pattern in the transcript before activating.

The regex is the *same* pattern that the heavy ``RollingWhisperWake`` backstop
uses — see ``rolling_whisper_wake.DEFAULT_PATTERN``. We deliberately import
that constant instead of duplicating the literal, so the two wake paths can
never drift apart (BUG-008 territory).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from jarvis.speech.rolling_whisper_wake import DEFAULT_PATTERN

log = logging.getLogger("jarvis.wake.verifier")

WAKE_PREFIX_PATTERN = DEFAULT_PATTERN

# A genuine "Hey Jarvis" must not be silently dropped just because the cloud
# STT was momentarily rate-limited (Groq 429) mid-session — that is the exact
# silent-drop the user reported as "Hey Jarvis stopped working". One retry with
# a short backoff mirrors the final-transcription path's retry contract
# (see jarvis/speech/pipeline.py::_transcribe_final). A *persistently* failing
# STT or a clear non-matching transcript still suppresses the hit (fail-closed)
# so a Groq outage cannot turn every ambient OWW false-positive into an
# activation.
_WAKE_VERIFY_RETRIES = 1
_WAKE_VERIFY_BACKOFF_S = 0.3
# Hard cap on a single wake-verify STT round-trip. The cloud STT client itself
# carries a 30 s request timeout meant for full utterance transcription — far
# too long for a wake gate that sits IN FRONT of the orb spawn. A verify that
# takes more than a couple of seconds is useless: the user already said
# "Hey Jarvis" and is waiting for a reaction NOW. A hung/overloaded Groq must
# therefore be cut off here and treated like a transient error (retry, then
# fail-closed suppress) instead of freezing the wake path for the full client
# timeout — that freeze was the "~10 second delay after the wake word" forensic.
_WAKE_VERIFY_TIMEOUT_S = 2.5


class _SupportsTranscribePCM(Protocol):
    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = ...,
        language: str | None = ...,
    ) -> Any: ...


def transcript_has_hey_prefix(text: str, matcher: Any | None = None) -> bool:
    """Return True if ``text`` satisfies the wake phrase.

    Pure function — no I/O, no STT. The caller transcribes the audio buffer
    however it likes and passes the resulting text in here.

    ``matcher`` is an optional :class:`~jarvis.speech.wake_phrase.WakeMatcher`
    (or any object exposing ``.search(text)``). When ``None`` the strict legacy
    "hey/hi/hallo + jarv" pattern is used so the default "Hey Jarvis" behaviour
    is byte-identical. A custom wake phrase passes its own matcher here so the
    OpenWakeWord prefix gate confirms the *configured* word, not "jarvis".
    """
    if not text:
        return False
    pattern = matcher if matcher is not None else WAKE_PREFIX_PATTERN
    return pattern.search(text) is not None


async def verify_wake_with_stt(
    stt: _SupportsTranscribePCM,
    pcm_bytes: bytes,
    sample_rate: int = 16_000,
    language: str | None = "de",
    matcher: Any | None = None,
) -> tuple[bool, str | None]:
    """Transcribe ``pcm_bytes`` and check the strict wake prefix.

    Returns ``(matched, transcript_text)``. ``matched`` is True only when the
    transcript contains the full "hey/hi/hallo + jarv" pattern.

    The transcript distinguishes two non-matching cases the caller must treat
    differently: ``""`` means the STT WORKED and heard no speech (evidence of a
    breath/noise-triggered false fire on a weak custom model → suppress), while
    ``None`` means the STT itself failed after retries (a provider outage →
    the caller may degrade open so a dead provider never bricks the wake,
    AP-22).

    Robustness contract (AD-OE6): never raise. A transient STT failure (Groq
    429 / 5xx / timeout) is retried once with a short backoff so a real
    "Hey Jarvis" is not silently dropped when the cloud STT is momentarily
    rate-limited mid-session. Empty PCM and a clear non-matching transcript
    collapse to ``(False, "")``; a persistently failing STT to ``(False,
    None)`` — the wake loop simply re-arms in every case, it is always better
    to ignore one borderline wake than to crash the listener with a 503.
    """
    if not pcm_bytes:
        return False, ""
    transcript: Any = None
    last_exc: Exception | None = None
    for attempt in range(_WAKE_VERIFY_RETRIES + 1):
        try:
            transcript = await asyncio.wait_for(
                stt.transcribe_pcm(
                    pcm_bytes, sample_rate=sample_rate, language=language
                ),
                timeout=_WAKE_VERIFY_TIMEOUT_S,
            )
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 — wake loop must keep running
            last_exc = exc
            if attempt < _WAKE_VERIFY_RETRIES:
                log.warning(
                    "wake-verify STT failed (%s) — retrying (attempt %d/%d)",
                    exc, attempt + 1, _WAKE_VERIFY_RETRIES + 1,
                )
                if _WAKE_VERIFY_BACKOFF_S > 0:
                    await asyncio.sleep(_WAKE_VERIFY_BACKOFF_S)
    if last_exc is not None:
        log.warning(
            "wake-verify STT failed after %d attempts (%s) — suppressing this OWW hit",
            _WAKE_VERIFY_RETRIES + 1, last_exc,
        )
        # None (not "") = STT OUTAGE: lets the caller degrade open on a dead
        # provider (AP-22) while a genuine empty transcription stays "".
        return False, None
    text = (getattr(transcript, "text", "") or "").strip()
    matched = transcript_has_hey_prefix(text, matcher)
    log.info(
        "wake-verify transcript=%r matched=%s",
        text[:120],
        matched,
    )
    return matched, text
