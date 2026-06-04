"""Audible alerts for failures a voice-first user must not miss.

Background — the 2026-05-28 "Hey Jarvis silently dead" incident
---------------------------------------------------------------
When the speech pipeline fails to start, the desktop bootstrap deliberately
degrades to "running without voice" instead of crashing the whole app — the
cloud-first doctrine says a headless / VPS box must stay up without a working
microphone. On a voice-first DESKTOP, however, that degradation used to be
*silent* (a swallowed WARNING), so a fatal pipeline-init crash looked exactly
like "Hey Jarvis suddenly stopped working" with no signal at all.

This module makes the degradation audible: a short descending disconnect tone
says "voice is offline" the instant it happens. Degrading is still allowed —
but never silently (AD-OE6, "zero silent drops").
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


async def play_voice_offline_alert(
    device: int | str | None = None,
    *,
    player_factory: Callable[[], Any] | None = None,
) -> None:
    """Play a best-effort AUDIBLE "voice is offline" tone. NEVER raises.

    Args:
        device: Output device for the standalone player (``cfg.audio.output_device``).
            Ignored when ``player_factory`` is supplied.
        player_factory: Test seam — returns an object exposing
            ``async play_pcm(pcm, sample_rate=...)``. Defaults to a fresh
            :class:`~jarvis.audio.player.AudioPlayer` on ``device``.

    The whole path is wrapped: if the audio subsystem is itself broken (no
    device, stream open failure) the tone may not be heard, but the alert must
    never become a *second* failure on top of the one it is announcing.
    """
    from loguru import logger

    try:
        from jarvis.audio.chime import CHIME_SAMPLE_RATE, DISCONNECT_PCM

        if player_factory is None:
            from jarvis.audio.player import AudioPlayer

            def player_factory() -> Any:
                return AudioPlayer(device=device)

        player = player_factory()
        await player.play_pcm(DISCONNECT_PCM, sample_rate=CHIME_SAMPLE_RATE)
    except Exception:  # noqa: BLE001 — an alert must never raise
        logger.debug("voice-offline alert failed (non-fatal)", exc_info=True)
