"""The silence test may skip pauses — it may never skip speech.

Skipping a stretch of audio closes it unread and it is never sent again, so a
wrong "silent" verdict deletes words permanently. A wrong "speech" verdict costs
one request. The thresholds are therefore deliberately lopsided, and these tests
exist to keep them that way: the speech cases here are quiet, awkward, and
close to the line on purpose.
"""

from __future__ import annotations

import numpy as np

from jarvis.dictation.segment import is_silent_segment, segment_energy


def _tone(amplitude: int, seconds: float = 1.0, sample_rate: int = 16_000) -> bytes:
    """A sine at ``amplitude`` — speech-like in that it is not a flat DC block."""
    t = np.arange(int(seconds * sample_rate), dtype=np.float32)
    wave = np.sin(2.0 * np.pi * 220.0 * t / sample_rate) * amplitude
    return wave.astype(np.int16).tobytes()


def _room_tone(seconds: float = 1.0, sample_rate: int = 16_000) -> bytes:
    """Not digital zero: a real room has a fan, a desk, a mains hum."""
    rng = np.random.default_rng(20260729)
    noise = rng.normal(0.0, 40.0, int(seconds * sample_rate))
    return noise.astype(np.int16).tobytes()


def test_digital_silence_is_silent() -> None:
    assert is_silent_segment(np.zeros(16_000, dtype=np.int16).tobytes())


def test_an_empty_buffer_is_silent() -> None:
    assert is_silent_segment(b"")


def test_room_tone_is_silent() -> None:
    """The everyday pause: the user stopped talking, the room did not."""
    assert is_silent_segment(_room_tone(8.0))


def test_normal_speech_is_not_silent() -> None:
    assert not is_silent_segment(_tone(6_000))


def test_quiet_speech_is_not_silent() -> None:
    """A badly gained microphone still records words, and they must survive.

    This is the case where a threshold picked for a loud headset deletes a whole
    dictation on somebody else's hardware.
    """
    assert not is_silent_segment(_tone(900))


def test_quiet_speech_survives_a_loud_session_reference() -> None:
    """The relative test must not turn a quiet sentence into a pause.

    Someone who leans back and finishes their thought more quietly is still
    speaking; only something far below the session's speech level counts as a
    gap.
    """
    assert not is_silent_segment(_tone(1_200), session_peak=12_000.0)


def test_a_pause_inside_a_loud_session_is_silent() -> None:
    assert is_silent_segment(_room_tone(8.0), session_peak=12_000.0)


def test_the_relative_test_stays_locked_without_a_speech_reference() -> None:
    """A session that has never been loud has no calibration to judge by.

    Without this floor a quiet recording measures itself against itself and
    skips everything — every word gone, no error anywhere.
    """
    quiet = _tone(700)
    assert not is_silent_segment(quiet, session_peak=1_500.0)


def test_a_short_word_in_an_otherwise_silent_stretch_is_not_silent() -> None:
    """Peak, not just average: one word in eight seconds of pause is still a
    word, and an RMS-only test would average it away."""
    pause = np.zeros(int(7.5 * 16_000), dtype=np.int16).tobytes()
    assert not is_silent_segment(pause + _tone(5_000, seconds=0.5))


def test_energy_reports_peak_and_rms() -> None:
    peak, rms = segment_energy(_tone(8_000))
    assert 7_900 <= peak <= 8_000
    assert 5_000 <= rms <= 6_000


def test_malformed_audio_reports_no_energy() -> None:
    """An odd byte count is not a valid int16 buffer; it must not raise."""
    assert segment_energy(b"\x01") == (0.0, 0.0)
