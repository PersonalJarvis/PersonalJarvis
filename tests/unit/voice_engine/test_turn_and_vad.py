"""Smart Turn features and the VAD endpointer, without any model runtime."""

from __future__ import annotations

import numpy as np

from jarvis.voice_engine.turn import log_mel, mel_filters, prepare_window
from jarvis.voice_engine.vad import FRAME_MS, Endpointer


def test_window_keeps_the_end_and_pads_the_beginning() -> None:
    short = np.ones(16_000, dtype=np.float32)
    window = prepare_window(short)
    assert window.shape == (128_000,)
    assert window[:-16_000].sum() == 0 and window[-16_000:].sum() == 16_000
    long = np.arange(200_000, dtype=np.float32)
    assert prepare_window(long)[-1] == 199_999


def test_log_mel_shape_and_gain_invariance() -> None:
    rng = np.random.default_rng(1)
    audio = (0.1 * rng.standard_normal(48_000)).astype(np.float32)
    features = log_mel(audio)
    assert features.shape == (80, 800)
    assert features.dtype == np.float32
    # Zero-mean/unit-variance normalisation makes the input level irrelevant,
    # which is what lets one threshold work for quiet and loud microphones.
    assert np.allclose(log_mel(audio * 7.0), features, atol=1e-4)


def test_mel_bank_is_slaney_shaped() -> None:
    bank = mel_filters()
    assert bank.shape == (201, 80)
    assert (bank >= 0).all()
    assert np.count_nonzero(bank.sum(axis=0)) == 80


def _feed(endpointer: Endpointer, probabilities: list[float]) -> list[tuple[str, int]]:
    events = []
    for p in probabilities:
        event = endpointer.feed(p)
        if event is not None:
            events.append((event.kind, event.at_ms))
    return events


def test_endpointer_reports_start_pause_and_resume() -> None:
    endpointer = Endpointer(min_speech_ms=96, silence_ms=192)
    voiced, quiet = [0.9] * 10, [0.05] * 8
    events = _feed(endpointer, voiced + quiet + voiced)
    kinds = [kind for kind, _ in events]
    assert kinds == ["speech_start", "silence_start", "silence_end"]
    start, pause = events[0][1], events[1][1]
    assert start == 3 * FRAME_MS
    assert pause == (10 + 6) * FRAME_MS


def test_endpointer_ignores_short_blips() -> None:
    endpointer = Endpointer(min_speech_ms=160, silence_ms=200)
    assert _feed(endpointer, [0.9, 0.9, 0.1, 0.9, 0.1] * 3) == []
