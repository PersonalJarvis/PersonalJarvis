"""OpenWakeWordProvider must prefer the bundled local ONNX models.

This keeps the wake path offline-on-first-boot and free of any heavy local
Whisper dependency — openWakeWord alone is the lightweight local detector.
"""
from __future__ import annotations

import jarvis.assets
from jarvis.plugins.wake.openwakeword_provider import OpenWakeWordProvider


def test_model_kwargs_uses_bundled_onnx_paths() -> None:
    provider = OpenWakeWordProvider()
    kw = provider._model_kwargs()

    assert kw["inference_framework"] == "onnx"
    assert len(kw["wakeword_models"]) == 1
    # Neutral shipped default is the bundled hey_rhasspy model (no branded/
    # trademarked default wake word). See jarvis.assets.bundled_wakeword_models.
    assert kw["wakeword_models"][0].endswith("hey_rhasspy_v0.1.onnx")
    assert kw["melspec_model_path"].endswith("melspectrogram.onnx")
    assert kw["embedding_model_path"].endswith("embedding_model.onnx")


def test_model_kwargs_falls_back_to_builtin_names(monkeypatch) -> None:
    # Simulate the bundled models being absent → must fall back to built-in
    # names (which triggers openWakeWord's package-cache auto-download).
    monkeypatch.setattr(jarvis.assets, "bundled_wakeword_models", lambda: None)

    provider = OpenWakeWordProvider(keywords=("hey_jarvis",))
    kw = provider._model_kwargs()

    assert kw["wakeword_models"] == ["hey_jarvis"]
    assert "melspec_model_path" not in kw


def test_canonical_keyword_strips_version_suffix() -> None:
    # The bundled ONNX file is "hey_jarvis_v0.1.onnx", so openWakeWord reports
    # the score under "hey_jarvis_v0.1". We must report the canonical keyword
    # so it stays consistent with supported_keywords / wake_keywords.
    provider = OpenWakeWordProvider(keywords=("hey_jarvis",))
    assert provider._canonical_keyword("hey_jarvis_v0.1") == "hey_jarvis"
    assert provider._canonical_keyword("hey_jarvis") == "hey_jarvis"


def test_canonical_keyword_passthrough_unknown() -> None:
    provider = OpenWakeWordProvider(keywords=("hey_jarvis",))
    assert provider._canonical_keyword("alexa_v0.1") == "alexa_v0.1"
