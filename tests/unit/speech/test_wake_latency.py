"""Latency contract for the wake path (mission 2026-06-30, "~0.5 s delay").

The Jarvis-Bar is pre-created and merely shown on wake, so the reveal itself is
fast. The controllable latency lives BEFORE the reveal:

* the custom-phrase (``stt_match`` = RollingWhisperWake) detector polls on a
  wall-clock cadence, so a slow poll interval directly delays the reaction to a
  spoken wake. It must be snappy;
* the openWakeWord model must be warm (covered by
  ``test_openwakeword_quiet_mic``) so the first frame is not cold.

This file pins the poll cadence so a future "save GPU" edit cannot silently
regress the custom-wake reaction time back toward half a second.
"""
from __future__ import annotations

from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


def test_rolling_whisper_poll_interval_is_snappy() -> None:
    wake = RollingWhisperWake(FasterWhisperProvider())
    assert wake._poll_interval_s <= 0.2, (  # noqa: SLF001
        "the custom-wake detector must poll at least every 200 ms so a spoken "
        "wake reaches the bar quickly"
    )
