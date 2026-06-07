"""Unit tests for the out-of-band TTS level channel (jarvis.audio.level_tap)."""
from __future__ import annotations

from jarvis.audio import level_tap


def test_no_subscriber_is_noop_and_reports_empty():
    level_tap.reset()
    assert level_tap.has_subscribers() is False
    level_tap.publish(0.5)  # must not raise


def test_subscriber_receives_clamped_level():
    level_tap.reset()
    got: list[float] = []
    unsub = level_tap.subscribe(got.append)
    assert level_tap.has_subscribers() is True
    level_tap.publish(2.0)   # clamp to 1.0
    level_tap.publish(-1.0)  # clamp to 0.0
    assert got == [1.0, 0.0]
    unsub()
    assert level_tap.has_subscribers() is False
    level_tap.publish(0.7)   # no subscriber → ignored
    assert got == [1.0, 0.0]


def test_failing_subscriber_is_swallowed():
    level_tap.reset()

    def boom(_: float) -> None:
        raise RuntimeError("x")

    level_tap.subscribe(boom)
    level_tap.publish(0.3)  # must not propagate
    level_tap.reset()


def test_feed_normalizes_raw_rms_to_a_reactive_level():
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)

    # Raw TTS speech RMS is small (~0.1). feed() must adaptively boost it so the
    # bars react — publishing the raw value left them stuck near 10%.
    for _ in range(8):
        level_tap.feed(0.0008)  # near silence
    quiet = got[-1]
    for _ in range(12):
        level_tap.feed(0.12)  # speech-level RMS
    loud = got[-1]

    assert loud > quiet
    assert loud > 0.3  # reaches a clearly visible level, not stuck near zero
    level_tap.reset()


def test_note_playing_marks_audio_active_for_its_duration():
    # The player only feeds a level at buffer-write time, then blocks for the
    # whole multi-second playback. note_playing() records the playback window so
    # the UI can show the speaking equalizer for the ENTIRE block, not just the
    # write instant.
    level_tap.reset()
    assert level_tap.playback_active() is False
    level_tap.note_playing(10.0)  # 10 s of audio about to play
    assert level_tap.playback_active() is True
    level_tap.reset()
    assert level_tap.playback_active() is False


def test_note_playing_ignores_nonpositive_and_resets_on_bargein():
    level_tap.reset()
    level_tap.note_playing(0.0)  # nothing to play → no window
    assert level_tap.playback_active() is False
    level_tap.note_playing(10.0)
    assert level_tap.playback_active() is True
    level_tap.reset_playing()  # barge-in discards the tail
    assert level_tap.playback_active() is False
    level_tap.reset()
