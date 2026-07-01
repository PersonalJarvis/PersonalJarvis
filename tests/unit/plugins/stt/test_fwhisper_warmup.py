"""Wake-model warm-up: the first LIVE transcription must not be cold.

Forensic (2026-06-28): after a fresh boot the user had to say "Hey Jarvis"
TWICE — the first wake was swallowed, the second worked. Root cause: the boot
path loaded the wake Whisper model with ``_ensure_model`` (weights into VRAM)
and immediately reported "listening", but never ran a real inference. On a
faster-whisper / CTranslate2 CUDA backend the FIRST ``model.transcribe`` pays a
one-off cold cost (kernel selection/JIT, cuDNN algo search, memory-pool setup)
of several seconds; steady state is ~100 ms. That cold inference landed on the
user's first "Hey Jarvis": the rolling-window wake loop blocked on it long
enough that the wake audio rolled out of the 1.8 s buffer before the transcript
returned, so the first wake was missed.

``warm_up`` primes the engine with one throwaway inference so the cost is paid
off the wake path, before the model goes live (and again after the background
turbo/cuda hot-swap, so the swapped-in model is warm too).
"""
from __future__ import annotations

from jarvis.plugins.stt import fwhisper
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider


class _FakeInfo:
    language = "de"


class _CountingModel:
    """Fake WhisperModel that records how many real inferences ran."""

    def __init__(self) -> None:
        self.transcribe_calls = 0

    def transcribe(self, audio, **kwargs):  # noqa: ANN001, ANN003
        self.transcribe_calls += 1
        return iter(()), _FakeInfo()


def test_warm_up_runs_a_real_inference_not_just_model_load(monkeypatch) -> None:
    model = _CountingModel()
    monkeypatch.setattr(fwhisper, "_new_whisper_model", lambda *a, **k: model)

    p = FasterWhisperProvider(model="base", device="cpu", compute_type="int8")
    p.warm_up()

    # Model loaded …
    assert p._model is model
    # … AND a real inference ran (this is what primes the CUDA/cuDNN kernels so
    # the user's first "Hey Jarvis" hits a warm engine, not a cold one).
    assert model.transcribe_calls >= 1


def test_warm_up_swallows_inference_failure(monkeypatch) -> None:
    class _BoomModel:
        def transcribe(self, audio, **kwargs):  # noqa: ANN001, ANN003
            raise RuntimeError("CUDA kernel boom")

    monkeypatch.setattr(fwhisper, "_new_whisper_model", lambda *a, **k: _BoomModel())

    p = FasterWhisperProvider(model="base", device="cpu", compute_type="int8")
    # A best-effort prime must NEVER break boot: a warm-up failure is swallowed,
    # the model stays loaded and still lazy-works on the first real transcribe.
    p.warm_up()

    assert p._model is not None


def test_warm_up_is_idempotent_on_model_load(monkeypatch) -> None:
    builds: list[_CountingModel] = []

    def factory(name: str, device: str, compute_type: str, cpu_threads: int = 0) -> _CountingModel:
        m = _CountingModel()
        builds.append(m)
        return m

    monkeypatch.setattr(fwhisper, "_new_whisper_model", factory)

    p = FasterWhisperProvider(model="base", device="cpu", compute_type="int8")
    p.warm_up()
    p.warm_up()

    # The model is built once and reused — a second prime never reloads weights.
    assert len(builds) == 1
