"""mic_level: normalize per-frame RMS into a reactive 0..1 level + pub/sub."""
from __future__ import annotations

from jarvis.audio import mic_level


def test_no_subscriber_is_noop_and_reports_empty():
    mic_level.reset_for_tests()
    assert mic_level.has_subscribers() is False
    mic_level.feed(0.1)  # must not raise


def test_feed_publishes_and_reacts_to_loudness():
    mic_level.reset_for_tests()
    got: list[float] = []
    unsub = mic_level.subscribe(got.append)
    assert mic_level.has_subscribers() is True

    # Near-silence frames keep the level low.
    for _ in range(8):
        mic_level.feed(0.0008)
    quiet = got[-1]

    # Loud speech frames drive the level up.
    for _ in range(12):
        mic_level.feed(0.25)
    loud = got[-1]

    assert 0.0 <= quiet <= 1.0
    assert 0.0 <= loud <= 1.0
    assert loud > quiet  # the bars must react to voice loudness
    assert loud > 0.3    # a clearly audible level, not stuck near zero

    unsub()
    assert mic_level.has_subscribers() is False
    mic_level.reset_for_tests()


def test_release_smoothing_decays_after_speech_stops():
    mic_level.reset_for_tests()
    got: list[float] = []
    mic_level.subscribe(got.append)
    for _ in range(12):
        mic_level.feed(0.25)  # loud
    peak = got[-1]
    for _ in range(30):
        mic_level.feed(0.0008)  # back to silence
    settled = got[-1]
    assert settled < peak  # level releases when you stop talking
    mic_level.reset_for_tests()


def test_failing_subscriber_is_swallowed():
    mic_level.reset_for_tests()

    def boom(_: float) -> None:
        raise RuntimeError("x")

    mic_level.subscribe(boom)
    mic_level.feed(0.1)  # must not propagate
    mic_level.reset_for_tests()
