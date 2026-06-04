"""Warm-up audio robustness: device stabilization + boot-ready cue.

Permanent fix for the 2026-05-25 post-reboot drift (BUG-014 class): on warm-up
the pipeline must (1) wait for the audio device table to settle and re-resolve
the output device against the fresh enumeration, and (2) play an audible
"ready" cue so the user knows when listening starts. Both must be fully
guarded — audio robustness must never break boot.
"""
from __future__ import annotations

import pytest

import jarvis.speech.pipeline as pl
from jarvis.audio.chime import CHIME_SAMPLE_RATE, READY_PCM
from jarvis.speech.pipeline import SpeechPipeline


class FakePlayer:
    def __init__(self, *, fail_play: bool = False, fail_set: bool = False) -> None:
        self.set_device_calls: list = []
        self.play_pcm_calls: list = []
        self._fail_play = fail_play
        self._fail_set = fail_set

    def set_device(self, device) -> None:
        if self._fail_set:
            raise RuntimeError("device resolve boom")
        self.set_device_calls.append(device)

    async def play_pcm(self, pcm: bytes, sample_rate: int | None = None) -> None:
        if self._fail_play:
            raise RuntimeError("no audio device")
        self.play_pcm_calls.append((pcm, sample_rate))


def _stub_stabilize(monkeypatch, info: dict) -> None:
    monkeypatch.setattr(pl, "wait_for_stable_audio_devices", lambda **kw: info)


@pytest.mark.asyncio
async def test_stabilize_reresolves_output_device(monkeypatch) -> None:
    """After the device table settles, the player must re-resolve its output
    device against the fresh PortAudio enumeration (idx-drift cure)."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._output_device = "auto-headset"
    pipe._player = FakePlayer()
    _stub_stabilize(
        monkeypatch,
        {"available": True, "device_count": 7, "stable": True,
         "waited_s": 1.5, "reinits": 4, "polls": 4},
    )

    await pipe._stabilize_audio_devices()

    assert pipe._player.set_device_calls == ["auto-headset"]


@pytest.mark.asyncio
async def test_stabilize_never_raises_when_player_fails(monkeypatch) -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._output_device = "auto-headset"
    pipe._player = FakePlayer(fail_set=True)
    _stub_stabilize(
        monkeypatch,
        {"available": True, "device_count": 0, "stable": False,
         "waited_s": 0.0, "reinits": 0, "polls": 0},
    )

    await pipe._stabilize_audio_devices()  # must not raise


@pytest.mark.asyncio
async def test_play_ready_cue_plays_ready_pcm() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._player = FakePlayer()

    await pipe._play_ready_cue()

    assert pipe._player.play_pcm_calls == [(READY_PCM, CHIME_SAMPLE_RATE)]


@pytest.mark.asyncio
async def test_play_ready_cue_never_raises_on_player_failure() -> None:
    """Headless / no-output-device: the cue is a silent no-op, never a crash."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._player = FakePlayer(fail_play=True)

    await pipe._play_ready_cue()  # must not raise
