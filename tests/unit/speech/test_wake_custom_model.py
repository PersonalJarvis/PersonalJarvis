"""A trained custom_onnx wake model is calibrated differently from the pretrained
openWakeWord models, so it needs its own threshold band.

Live forensic 2026-07-01: a user-trained neural model fires on normal-volume
speech at ~0.9 and on breath/other words well below, while the pretrained OWW
models fire at ~0.15-0.23. Feeding a custom model the low 0.06-0.30 sensitivity
band false-fires; feeding it the amplify-only wake AGC lifts quiet BREATH to full
scale and false-fires at ~1.0. This pins the calibrated custom-model threshold
band (the AGC-off wiring lives in the pipeline OWW construction). Pure Python, so
it is identical on Windows, Linux and macOS.
"""
from __future__ import annotations

from types import SimpleNamespace


def _cfg(model_path: str, sensitivity: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(
        phrase="Hey Nico",
        engine="custom_onnx",
        custom_model_path=model_path,
        sensitivity=sensitivity,
        fuzzy_match_ratio=0.8,
    )


def test_custom_onnx_uses_calibrated_higher_threshold(tmp_path) -> None:
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "hey_nico.onnx"
    model.write_bytes(b"onnx")  # resolve only checks the path is a file
    plan = resolve_wake_plan(_cfg(str(model)), local_whisper_available=False)
    assert plan.engine == "custom_onnx"
    # A calibrated custom model uses a HIGH threshold (~0.5 at mid sensitivity),
    # NOT the 0.15 pretrained band that false-fires on breath/other words.
    assert plan.threshold >= 0.45, plan.threshold


def test_custom_onnx_threshold_scales_strict_to_sensitive(tmp_path) -> None:
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "m.onnx"
    model.write_bytes(b"onnx")

    def thr(s: float) -> float:
        return resolve_wake_plan(_cfg(str(model), s), local_whisper_available=False).threshold

    strict, sensitive = thr(0.0), thr(1.0)
    assert strict > sensitive, (strict, sensitive)   # 0.0 = least likely to fire
    assert 0.60 <= strict <= 0.75
    assert 0.25 <= sensitive <= 0.35


def test_custom_onnx_no_prefix_verify(tmp_path) -> None:
    # A custom model IS its own discriminator — it must NOT run the German STT
    # prefix re-verify (that path is only for the jarvis-family OWW model).
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "m.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(_cfg(str(model)), local_whisper_available=False)
    assert plan.verify_prefix is False
