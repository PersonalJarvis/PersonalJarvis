"""SpeechPipeline.set_silence_window_ms delegates to the live VAD."""
from __future__ import annotations

from jarvis.speech.pipeline import SpeechPipeline


class _RecordingVad:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def set_silence_window_ms(self, ms: int) -> None:
        self.calls.append(ms)


def test_pipeline_delegates_to_vad() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)  # skip heavy __init__
    vad = _RecordingVad()
    pipe._vad = vad
    pipe.set_silence_window_ms(2500)
    assert vad.calls == [2500]


def test_pipeline_no_vad_is_safe() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._vad = None
    # Must not raise when the pipeline is headless / not yet wired.
    pipe.set_silence_window_ms(2500)
