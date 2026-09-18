"""Double-clap detector: shape + spectrum + pairing, never loudness alone."""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.speech.clap_detector import SAMPLE_RATE, ClapDetector

_RNG = np.random.default_rng(7)


def _silence(seconds: float, level: float = 0.002) -> np.ndarray:
    return (_RNG.standard_normal(int(seconds * SAMPLE_RATE)) * level).astype(np.float32)


def _clap(amp: float = 0.6) -> np.ndarray:
    n = int(0.06 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    return (_RNG.standard_normal(n) * amp * np.exp(-t / 0.02)).astype(np.float32)


def _vowel(seconds: float = 0.4, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    sig = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 8))
    env = np.minimum(1.0, t / 0.01)
    return (amp * sig / 3 * env).astype(np.float32)


def _knock(amp: float = 0.8) -> np.ndarray:
    n = int(0.08 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 180 * t) * np.exp(-t / 0.015)).astype(np.float32)


def _run(*parts: np.ndarray) -> int:
    audio = np.concatenate([_silence(1.0), *parts, _silence(1.0)])
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
    det = ClapDetector()
    fired = 0
    step = 1024  # 512-sample chunks, like the wake mic
    for i in range(0, len(pcm), step):
        fired += det.feed(pcm[i : i + step])
    return fired


def test_a_double_clap_fires_once() -> None:
    assert _run(_clap(), _silence(0.4), _clap()) == 1


@pytest.mark.parametrize("gap", [0.25, 0.5, 0.7])
def test_gaps_inside_the_window_fire(gap: float) -> None:
    assert _run(_clap(), _silence(gap), _clap()) == 1


def test_a_single_clap_does_not_fire() -> None:
    assert _run(_clap()) == 0


@pytest.mark.parametrize("gap", [0.05, 1.2])
def test_gaps_outside_the_window_do_not_fire(gap: float) -> None:
    assert _run(_clap(), _silence(gap), _clap()) == 0


def test_applause_like_series_does_not_fire() -> None:
    series = (_clap(), _silence(0.3), _clap(), _silence(0.2), _clap(), _silence(0.25), _clap())
    assert _run(*series) == 0


def test_loud_speech_does_not_fire() -> None:
    assert _run(_vowel(), _silence(0.3), _vowel()) == 0


def test_knocks_are_too_dark_to_be_claps() -> None:
    assert _run(_knock(), _silence(0.4), _knock()) == 0


def test_very_unequal_pair_does_not_fire() -> None:
    assert _run(_clap(0.9), _silence(0.4), _clap(0.08)) == 0


def test_cooldown_blocks_an_immediate_second_double() -> None:
    two_doubles = (_clap(), _silence(0.4), _clap(), _silence(0.6), _clap(), _silence(0.4), _clap())
    assert _run(*two_doubles) == 1


def _keystroke(amp: float = 0.6) -> np.ndarray:
    n = int(0.03 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    return (_RNG.standard_normal(n) * amp * np.exp(-t / 0.002)).astype(np.float32)


def test_two_keystrokes_are_too_short_to_be_claps() -> None:
    assert _run(_keystroke(), _silence(0.4), _keystroke()) == 0


def test_a_quiet_clap_across_the_room_still_fires() -> None:
    assert _run(_clap(0.03), _silence(0.4), _clap(0.03)) == 1


@pytest.mark.asyncio
async def test_opt_in_wake_ack_drops_the_input_it_overlaps() -> None:
    import time

    from jarvis.speech.pipeline import SpeechPipeline

    played: list[str] = []
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._ack_pcm = b"\x00\x00" * 24_000  # one second
    pipe._ack_rate = 24_000
    pipe._input_suppressed_until_ns = 0

    async def _ack(*, ptt: bool = False) -> None:
        played.append("ack")

    pipe._play_ack = _ack  # type: ignore[method-assign]
    before = time.time_ns()
    await pipe._play_opt_in_wake_ack()
    assert played == ["ack"]
    # Chime + one second of phrase + margin are suppressed.
    assert pipe._input_suppressed_until_ns - before > 1_300_000_000
