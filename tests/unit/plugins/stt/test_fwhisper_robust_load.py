"""Robust local-Whisper model load: cross-platform compute_type + model-name
normalization.

Two boot landmines this guards against:
  1. A drifted `[stt].model = "whisper-large-v3"` is NOT a valid faster-whisper
     id (the bare id is "large-v3"); loading it raises. A clean boot reading the
     TOML would crash the "Hey Josef" wake path.
  2. `compute_type="int8_float16"` is CUDA-only. On a CPU / headless VPS the load
     raises — violating the cloud-first "must boot on python:3.11-slim" rule.

The provider now normalizes known-bad aliases and self-heals to a CPU-safe combo
when the configured device/compute_type fails to load.
"""
from __future__ import annotations

from jarvis.plugins.stt import fwhisper
from jarvis.plugins.stt.fwhisper import (
    FasterWhisperProvider,
    _cpu_safe_compute_type,
    _normalize_model_name,
)


def test_normalize_strips_invalid_whisper_prefix() -> None:
    assert _normalize_model_name("whisper-large-v3") == "large-v3"
    assert _normalize_model_name("whisper-tiny") == "tiny"


def test_normalize_passes_valid_ids_through() -> None:
    for m in ("large-v3", "large-v3-turbo", "distil-large-v3", "base", "small"):
        assert _normalize_model_name(m) == m


def test_normalize_leaves_hf_repo_ids_untouched() -> None:
    # An org/name HF repo id is valid and may legitimately contain 'whisper-'.
    assert (
        _normalize_model_name("Systran/faster-whisper-large-v3")
        == "Systran/faster-whisper-large-v3"
    )


def test_cpu_safe_compute_type_downgrades_cuda_only() -> None:
    assert _cpu_safe_compute_type("int8_float16") == "int8"
    assert _cpu_safe_compute_type("float16") == "int8"


def test_cpu_safe_compute_type_keeps_cpu_compatible() -> None:
    for ct in ("int8", "float32", "int16"):
        assert _cpu_safe_compute_type(ct) == ct


def test_ensure_model_falls_back_to_cpu_safe_on_failure(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_factory(model_name: str, device: str, compute_type: str):
        calls.append((model_name, device, compute_type))
        # Simulate a no-CUDA host: cuda / *_float16 unsupported.
        if device == "cuda" or compute_type in ("float16", "int8_float16"):
            raise ValueError("CUDA driver not found")
        return object()

    monkeypatch.setattr(fwhisper, "_new_whisper_model", fake_factory)

    p = FasterWhisperProvider(
        model="whisper-large-v3", device="cuda", compute_type="int8_float16"
    )
    p._ensure_model()

    assert p._model is not None
    # First attempt: normalized name + the configured cuda/int8_float16 combo.
    assert calls[0] == ("large-v3", "cuda", "int8_float16")
    # Self-healed fallback: cpu + a cpu-safe compute type.
    assert calls[-1] == ("large-v3", "cpu", "int8")


def test_ensure_model_uses_configured_combo_when_it_works(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_factory(model_name: str, device: str, compute_type: str):
        calls.append((model_name, device, compute_type))
        return object()

    monkeypatch.setattr(fwhisper, "_new_whisper_model", fake_factory)

    p = FasterWhisperProvider(
        model="large-v3-turbo", device="cuda", compute_type="int8_float16"
    )
    p._ensure_model()

    assert len(calls) == 1  # worked first try → no fallback
    assert calls[0] == ("large-v3-turbo", "cuda", "int8_float16")


def test_ensure_model_does_not_retry_when_already_cpu_safe(monkeypatch) -> None:
    # A genuinely bad model name on an already-cpu-safe combo must raise once,
    # not pointlessly retry the identical combo (which would re-hit the network).
    calls: list[tuple[str, str, str]] = []

    def fake_factory(model_name: str, device: str, compute_type: str):
        calls.append((model_name, device, compute_type))
        raise ValueError("model not found")

    monkeypatch.setattr(fwhisper, "_new_whisper_model", fake_factory)

    p = FasterWhisperProvider(
        model="nonexistent-model", device="cpu", compute_type="int8"
    )
    try:
        p._ensure_model()
        raised = False
    except ValueError:
        raised = True

    assert raised
    assert len(calls) == 1  # no second attempt — the combo was already cpu-safe
