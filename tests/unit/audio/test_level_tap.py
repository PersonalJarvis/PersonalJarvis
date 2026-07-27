"""Unit tests for the out-of-band TTS level channel (jarvis.audio.level_tap)."""
from __future__ import annotations

import time

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


def _wait_for(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_delayed_publish_arrives_late_not_immediately():
    # write() returns when PortAudio ACCEPTS a block; the sound is heard one
    # output latency later. A delayed publish must therefore NOT reach sinks
    # synchronously, but must land once the delay has elapsed.
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        level_tap.publish(0.8, delay_s=0.15)
        assert got == []  # not delivered at accept time
        assert _wait_for(lambda: got == [0.8])
    finally:
        level_tap.reset()


def test_delayed_publishes_preserve_order():
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        level_tap.publish(0.2, delay_s=0.05)
        level_tap.publish(0.4, delay_s=0.08)
        level_tap.publish(0.6, delay_s=0.11)
        assert _wait_for(lambda: len(got) == 3)
        assert got == [0.2, 0.4, 0.6]
    finally:
        level_tap.reset()


def test_reset_playing_drops_pending_levels_and_zeroes_the_bars():
    # Barge-in aborts the audible tail; its scheduled levels belong to audio
    # that will never be heard, so they are dropped and one honest zero
    # collapses the equalizer with the sound.
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        level_tap.publish(0.9, delay_s=5.0)  # far future — must never arrive
        level_tap.reset_playing()
        assert _wait_for(lambda: got == [0.0])
        time.sleep(0.05)
        assert got == [0.0]
    finally:
        level_tap.reset()


def test_reset_playing_zeroes_even_with_nothing_pending():
    # The zero must be unconditional: with an empty queue the last DELIVERED
    # level may still be nonzero on the sink, and a barge-in has to collapse
    # the bars immediately — not after the renderer's staleness clamp.
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        level_tap.publish(0.6)  # synchronously delivered, nothing queued
        level_tap.reset_playing()
        assert got == [0.6, 0.0]
    finally:
        level_tap.reset()


def test_no_cancelled_level_ever_lands_after_the_bargein_zero():
    # Ordering contract of the generation guard + delivery lock: once
    # reset_playing() returns, its zero is the LAST word — a level scheduled
    # before the barge-in may at worst land before it, never after.
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        for k in range(5):
            level_tap.publish(0.5 + k * 0.05, delay_s=0.02 + k * 0.02)
        level_tap.reset_playing()
        zero_at = len(got) - 1
        assert got[zero_at] == 0.0
        time.sleep(0.3)  # give any (buggy) survivor time to fire
        assert got[zero_at + 1:] == [], "cancelled level delivered after the zero"
    finally:
        level_tap.reset()


def test_zero_delay_publish_stays_synchronous():
    level_tap.reset()
    got: list[float] = []
    level_tap.subscribe(got.append)
    try:
        level_tap.publish(0.5, delay_s=0.0)
        assert got == [0.5]  # no thread hop for the no-latency path
    finally:
        level_tap.reset()
