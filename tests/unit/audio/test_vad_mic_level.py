"""Integration: the VAD frame loop feeds jarvis.audio.mic_level from the audio
already captured for STT (the link that was missing — bars never reacted)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from jarvis.audio import mic_level
from jarvis.audio.vad import SileroEndpointer


async def _chunks(pcms):
    for p in pcms:
        yield SimpleNamespace(pcm=p)


async def test_vad_feeds_mic_level_per_frame(monkeypatch):
    mic_level.reset_for_tests()
    got: list[float] = []
    mic_level.subscribe(got.append)

    vad = SileroEndpointer()
    vad._model = object()  # skip the lazy Silero load
    monkeypatch.setattr(vad, "_prob", lambda frame: 0.9)  # "speech", no torch

    loud = np.full(512 * 6, 8000, dtype=np.int16).tobytes()
    quiet = np.full(512 * 6, 40, dtype=np.int16).tobytes()
    async for _ in vad.utterances(_chunks([loud, loud, quiet, quiet])):
        pass

    fed = list(got)
    mic_level.reset_for_tests()
    assert fed, "VAD did not feed mic_level — the bars would never react"
    assert max(fed) > 0.0  # loud frames produced a non-zero level


async def test_vad_skips_feed_when_no_subscriber(monkeypatch):
    mic_level.reset_for_tests()  # no subscriber
    vad = SileroEndpointer()
    vad._model = object()
    monkeypatch.setattr(vad, "_prob", lambda frame: 0.1)
    loud = np.full(512 * 4, 8000, dtype=np.int16).tobytes()
    # must run without error and without publishing anywhere
    async for _ in vad.utterances(_chunks([loud])):
        pass
    assert mic_level.has_subscribers() is False
