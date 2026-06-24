"""The local wake-match Whisper must be small + CPU, independent of the
utterance STT model.

Measured on the maintainer's Blackwell GPU (RTX 5070 Ti): loading the utterance
model on CUDA cost ~71 s (CTranslate2 kernel JIT for the new arch), while the
SAME model on CPU loads in 3.45 s and a `base` model on CPU in 0.45 s. The local
Whisper only powers wake-phrase transcript matching + the live-preview probe
(both latency-tolerant; utterance STT is separate — cloud or `stt.model`), so it
loads a small model on CPU by default. This collapses Phase-A warm-up from ~71 s
to a few seconds.
"""
from __future__ import annotations

from jarvis.core.config import STTConfig
from jarvis.plugins.stt import build_wake_whisper
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider


def test_stt_config_wake_defaults_small_and_cpu() -> None:
    cfg = STTConfig()
    assert cfg.wake_model == "base"
    assert cfg.wake_device == "cpu"
    assert cfg.wake_compute_type == "int8"


def test_build_wake_whisper_uses_wake_fields_not_utterance_model() -> None:
    cfg = STTConfig(
        model="large-v3-turbo",
        device="cuda",
        compute_type="int8_float16",
        wake_model="base",
        wake_device="cpu",
        wake_compute_type="int8",
    )
    p = build_wake_whisper(cfg)

    assert isinstance(p, FasterWhisperProvider)
    # The wake instance must NOT inherit the heavy utterance model / cuda.
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._compute_type == "int8"


def test_build_wake_whisper_passes_language() -> None:
    p = build_wake_whisper(STTConfig(), language="de")
    assert p._language == "de"


def test_build_wake_whisper_tolerates_missing_wake_fields() -> None:
    # A config object predating the wake_* fields falls back to small/cpu/int8.
    class _Bare:
        pass

    p = build_wake_whisper(_Bare())
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._compute_type == "int8"


def test_build_wake_whisper_biases_prompt_with_custom_phrase() -> None:
    # A custom wake word ("Hey Ruben") routes to the stt_match path, where the
    # small base/cpu model otherwise mis-hears the proper noun. Empirical
    # 2026-06-23 on the user's real wake WAVs: WITHOUT the bias the live model
    # heard "Hey Ruben" as "Space"/"Ego"/"Herum" -> 2-13% recall (effectively a
    # dead wake word); WITH the spoken phrase as initial_prompt -> 83% recall.
    # The earlier hallucination concern is held off by the strict ["hey","ruben"]
    # matcher (a stray "Ruben" in speech is not an adjacent "hey ruben") plus the
    # no_speech_prob/RMS gates: false-wake stayed ~0% on real speech. So the bias
    # is re-enabled on this path. It is scoped to the custom phrase only -- the
    # OWW/"Hey Jarvis" paths pass no phrase and stay unbiased (test below).
    p = build_wake_whisper(STTConfig(), language="de", wake_phrase="Hey Ruben")
    assert p._initial_prompt == "Hey Ruben"


def test_build_wake_whisper_default_has_no_prompt_bias() -> None:
    p = build_wake_whisper(STTConfig(), language="de")
    assert p._initial_prompt is None

    p_blank = build_wake_whisper(STTConfig(), language="de", wake_phrase="   ")
    assert p_blank._initial_prompt is None
