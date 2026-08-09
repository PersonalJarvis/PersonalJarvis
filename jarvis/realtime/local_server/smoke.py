"""End-to-end audio proof for the managed local realtime server."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from jarvis.core.turn_language import validate_output_language

_MIN_AUDIO_BYTES = 2_400  # 50 ms of 24 kHz mono PCM16; rejects empty headers.

_PROBE_INPUTS = {
    "en": "Please answer with one short sentence confirming that the voice test works.",
    "de": (  # i18n-allow: runtime voice probe
        "Bitte antworte mit einem kurzen Satz, der "  # i18n-allow
        "bestätigt, dass der Sprachtest funktioniert."  # i18n-allow
    ),
    "es": "Responde con una frase corta que confirme que la prueba de voz funciona.",
}

_PROBE_INSTRUCTIONS = {
    "en": "Answer briefly in English. Do not call tools and do not use code or lists.",
    "de": (  # i18n-allow: runtime voice probe
        "Antworte kurz auf Deutsch. Verwende keine Tools, "  # i18n-allow
        "keinen Code und keine Listen."  # i18n-allow
    ),
    "es": "Responde brevemente en español. No uses herramientas, código ni listas.",
}


async def probe_voice_roundtrip(
    base_url: str,
    *,
    language: str = "en",
    timeout_s: float = 60.0,
) -> dict[str, object]:
    """Prove handshake -> LLM -> transcript -> TTS audio -> clean boundary.

    A ready ``/v1/pool`` proves model loading only.  This probe deliberately
    traverses the same OpenAI Realtime adapter the desktop call uses, so a
    protocol/model switch that yields text but no audible response cannot be
    persisted as successful.
    """
    expected = language if language in _PROBE_INPUTS else "en"

    # Import through the plugin loader boundary rather than creating another
    # websocket implementation whose event semantics could drift.
    from jarvis.plugins.realtime.openai_realtime import LocalRealtimeProvider
    from jarvis.realtime.protocol import RealtimeSessionConfig

    provider = LocalRealtimeProvider(base_url=base_url)
    started_at = time.monotonic()
    session: Any | None = None
    transcript_parts: list[str] = []
    audio_bytes = 0
    first_audio_ms: int | None = None
    completed = False
    try:
        async with asyncio.timeout(max(1.0, timeout_s)):
            session = await provider.open_session(
                RealtimeSessionConfig(
                    instructions=_PROBE_INSTRUCTIONS[expected],
                    language=expected,
                    input_language=expected,
                    language_is_pinned=True,
                    tools=(),
                )
            )
            await session.send_text(_PROBE_INPUTS[expected])
            async for event in session.receive():
                if event.type == "audio_delta" and event.audio is not None:
                    chunk = bytes(getattr(event.audio, "pcm", b"") or b"")
                    if chunk and first_audio_ms is None:
                        first_audio_ms = int((time.monotonic() - started_at) * 1000)
                    audio_bytes += len(chunk)
                elif event.type == "output_transcript_delta" and event.text:
                    transcript_parts.append(str(event.text))
                elif event.type == "error":
                    raise RuntimeError(event.error or "the realtime server reported an error")
                elif event.type == "turn_complete":
                    completed = True
                    break
    except TimeoutError as exc:
        raise TimeoutError(f"voice test did not complete within {timeout_s:.0f} seconds") from exc
    finally:
        if session is not None:
            await session.close()

    transcript = "".join(transcript_parts).strip()
    if not completed:
        raise RuntimeError("voice test ended without a response boundary")
    if not transcript:
        raise RuntimeError("voice test produced audio without a safety transcript")
    if audio_bytes < _MIN_AUDIO_BYTES:
        raise RuntimeError("voice test produced no usable speech audio")
    verdict = validate_output_language(transcript, resolved_language=expected)
    if verdict.should_block:
        raise RuntimeError(
            "voice test answered in the wrong language "
            f"(expected {expected}, detected {verdict.detected_language})"
        )
    return {
        "ok": True,
        "language": expected,
        "audio_bytes": audio_bytes,
        "first_audio_ms": first_audio_ms,
        "transcript_chars": len(transcript),
    }


def probe_voice_roundtrip_sync(
    base_url: str,
    *,
    language: str = "en",
    timeout_s: float = 60.0,
) -> dict[str, object]:
    """Synchronous installer entry point for :func:`probe_voice_roundtrip`."""
    return asyncio.run(probe_voice_roundtrip(base_url, language=language, timeout_s=timeout_s))
