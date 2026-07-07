from __future__ import annotations

from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from jarvis.speech.wake_phrase import compile_wake_matcher


def test_final_stt_has_no_example_prompt_that_can_be_hallucinated() -> None:
    stt = FasterWhisperProvider()

    assert stt._initial_prompt is None  # noqa: SLF001


def test_rolling_wake_ignores_very_low_rms_hallucination_windows() -> None:
    wake = RollingWhisperWake(
        FasterWhisperProvider(), pattern=compile_wake_matcher("Hey Nova")
    )

    assert wake._min_rms >= 0.003  # noqa: SLF001
