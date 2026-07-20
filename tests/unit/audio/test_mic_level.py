"""mic_level: normalize per-frame RMS into a reactive 0..1 level + pub/sub."""

from __future__ import annotations

from jarvis.audio import mic_level
from jarvis.audio.mic_level import LevelNormalizer


def _push_frames(meter: LevelNormalizer, rms: float, frames: int) -> float:
    level = 0.0
    for _ in range(frames):
        level = meter.push(rms)
    return level


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
    assert loud > 0.3  # a clearly audible level, not stuck near zero

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


def test_quiet_laptop_mic_speech_is_clearly_visible():
    """Fresh-machine forensics Bug 17: on a quiet laptop input path (hiss
    ~0.0005, speech ~0.004 rms) STT/wake work fine but the bars sat at zero —
    the meter's absolute floors (_MIN_NOISE_FLOOR/_MIN_PEAK) were calibrated
    on a louder mic. After floor adaptation, quiet-mic speech must render a
    clearly visible level, not a dead meter."""
    mic_level.reset_for_tests()
    got: list[float] = []
    mic_level.subscribe(got.append)

    for _ in range(80):
        mic_level.feed(0.0005)  # quiet laptop hiss — floor settles here
    for _ in range(15):
        mic_level.feed(0.004)  # normal speech on that mic

    assert got[-1] > 0.25, f"speech on a quiet mic reads near-dead: {got[-1]:.3f}"
    mic_level.reset_for_tests()


def test_meter_preserves_quiet_normal_and_loud_differences():
    """The display must follow volume instead of auto-gaining every new peak.

    The old adaptive-peak ratio made soft speech, normal speech, and loud speech
    converge to 1.0. That produced false full swings and left no extra movement
    when the user actually raised their voice.
    """
    meter = LevelNormalizer()
    _push_frames(meter, 0.0008, 60)

    soft = _push_frames(meter, 0.02, 12)
    normal = _push_frames(meter, 0.06, 12)
    loud = _push_frames(meter, 0.20, 12)

    assert 0.3 < soft < 0.7
    assert normal > soft + 0.12
    assert loud > normal + 0.12
    assert loud > 0.9


def test_one_impulse_does_not_suppress_following_voice():
    """A click or clipped frame must not poison the next seconds of metering."""
    meter = LevelNormalizer()
    _push_frames(meter, 0.0008, 60)

    meter.push(0.5)
    recovered = _push_frames(meter, 0.06, 6)

    assert recovered > 0.65


def test_muted_mic_stays_dark():
    """Runaway-gain guard: digital near-silence (muted mic) must NOT produce
    dancing bars even with the lowered floors."""
    mic_level.reset_for_tests()
    got: list[float] = []
    mic_level.subscribe(got.append)
    for _ in range(120):
        mic_level.feed(0.00003)
    assert got[-1] < 0.05, f"muted mic shows a level: {got[-1]:.3f}"
    mic_level.reset_for_tests()


def test_failing_subscriber_is_swallowed():
    mic_level.reset_for_tests()

    def boom(_: float) -> None:
        raise RuntimeError("x")

    mic_level.subscribe(boom)
    mic_level.feed(0.1)  # must not propagate
    mic_level.reset_for_tests()
