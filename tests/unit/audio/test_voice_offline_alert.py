"""Regression guard for the 2026-05-28 "Hey Jarvis silently dead" incident.

When ``SpeechPipeline.__init__`` crashed at boot (a ``subscribe`` call
referenced an unimported event name), the desktop bootstrap swallowed the
exception into a WARNING and kept running "without voice" — totally silent.
A voice-first user just experienced "Hey Jarvis stopped working" with zero
signal. ``play_voice_offline_alert`` is the audible counter-measure: it plays
the descending disconnect tone so the degradation is impossible to miss.

The alert MUST be best-effort: if the audio path itself is broken (no device,
stream open failure), the alert may not be heard, but it must NEVER raise — an
alert that throws would become a second failure on top of the first.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.audio.alerts import play_voice_offline_alert
from jarvis.audio.chime import CHIME_SAMPLE_RATE, DISCONNECT_PCM


class _FakePlayer:
    """Records play_pcm calls instead of touching real audio hardware."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, int | None]] = []

    async def play_pcm(self, pcm: bytes, sample_rate: int | None = None) -> None:
        self.calls.append((pcm, sample_rate))


@pytest.mark.asyncio
async def test_plays_disconnect_tone_through_injected_player() -> None:
    player = _FakePlayer()

    await play_voice_offline_alert(player_factory=lambda: player)

    assert player.calls == [(DISCONNECT_PCM, CHIME_SAMPLE_RATE)]


@pytest.mark.asyncio
async def test_never_raises_when_player_construction_fails() -> None:
    def _boom() -> Any:
        raise RuntimeError("no usable output device")

    # Must complete normally — the alert must never become a second failure.
    await play_voice_offline_alert(player_factory=_boom)


@pytest.mark.asyncio
async def test_never_raises_when_playback_fails() -> None:
    class _BoomPlayer:
        async def play_pcm(self, pcm: bytes, sample_rate: int | None = None) -> None:
            raise RuntimeError("OutputStream open failed")

    await play_voice_offline_alert(player_factory=lambda: _BoomPlayer())
