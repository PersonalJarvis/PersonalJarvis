"""Unit tests for the global "Sound effects" earcon master switch.

The Settings → Behavior toggle persists ``[ui] sound_effects`` and the speech
pipeline reads it fresh before every synthesized earcon (wake chime, hang-up
tone, boot-ready cue, "still listening" earcon). When off, those tones are
silenced; the spoken TTS voice is never affected. Default on — a missing field
must never silence tones.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

from jarvis.audio.chime import CHIME_PCM, READY_PCM
from jarvis.speech.pipeline import SpeechPipeline

_MISSING = object()


class _FakeTTS:
    name = "fake-tts"
    supports_streaming = False

    async def synthesize(  # type: ignore[no-untyped-def]
        self, text, language_code=None
    ) -> AsyncIterator[bytes]:  # pragma: no cover
        if False:
            yield b""


class _RecordingPlayer:
    def __init__(self) -> None:
        self.plays: list[bytes] = []

    async def play_pcm(self, pcm, sample_rate=None):  # noqa: ANN001
        self.plays.append(pcm)


def _make_pipeline(sound_effects: object) -> SpeechPipeline:
    pipe = SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)
    pipe._player = _RecordingPlayer()  # type: ignore[assignment]
    ui = SimpleNamespace() if sound_effects is _MISSING else SimpleNamespace(
        sound_effects=sound_effects
    )
    pipe._config = SimpleNamespace(ui=ui)
    return pipe


async def test_earcon_plays_when_enabled():
    pipe = _make_pipeline(True)
    await pipe._play_earcon(CHIME_PCM)
    assert pipe._player.plays == [CHIME_PCM]  # type: ignore[attr-defined]


async def test_earcon_muted_when_disabled():
    pipe = _make_pipeline(False)
    await pipe._play_earcon(CHIME_PCM)
    assert pipe._player.plays == []  # type: ignore[attr-defined]


async def test_earcon_default_on_when_field_missing():
    pipe = _make_pipeline(_MISSING)
    await pipe._play_earcon(CHIME_PCM)
    assert pipe._player.plays == [CHIME_PCM]  # type: ignore[attr-defined]


async def test_ready_cue_respects_switch():
    on = _make_pipeline(True)
    await on._play_ready_cue()
    assert on._player.plays == [READY_PCM]  # type: ignore[attr-defined]

    off = _make_pipeline(False)
    await off._play_ready_cue()
    assert off._player.plays == []  # type: ignore[attr-defined]


async def test_wake_chime_muted_but_spoken_ack_still_plays(monkeypatch):
    """A muted wake path plays no chime, but the spoken ACK that follows is NOT
    an earcon and must still reach the player — only effect tones are gated."""
    pipe = _make_pipeline(False)
    pipe._ack_pcm = b"\x00\x00" * 100  # type: ignore[attr-defined]

    async def _fast_sleep(_secs):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr("jarvis.speech.pipeline.asyncio.sleep", _fast_sleep)
    await pipe._play_ack(ptt=False)
    assert pipe._player.plays == [pipe._ack_pcm]  # type: ignore[attr-defined]
