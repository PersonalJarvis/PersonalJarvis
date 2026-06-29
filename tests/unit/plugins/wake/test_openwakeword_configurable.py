"""OpenWakeWordProvider can load a configurable model (pretrained or custom).

The custom-wake-word feature needs the provider to load any ONNX model — a
pretrained alexa/mycroft/rhasspy from the package, or a user-supplied custom
model — while reusing the bundled melspec/embedding backbones so the wake path
stays offline. The default (no model_path) must keep the bundled hey_jarvis
behaviour untouched.
"""
from __future__ import annotations

from jarvis.plugins.wake.openwakeword_provider import OpenWakeWordProvider


def test_explicit_model_path_is_used_with_bundled_backbones() -> None:
    fake = "/models/alexa_v0.1.onnx"
    provider = OpenWakeWordProvider(keywords=("alexa",), model_path=fake)
    kw = provider._model_kwargs()

    assert kw["wakeword_models"] == [fake]
    assert kw["inference_framework"] == "onnx"
    # Bundled melspec + embedding are reused so any model loads offline.
    assert kw["melspec_model_path"].endswith("melspectrogram.onnx")
    assert kw["embedding_model_path"].endswith("embedding_model.onnx")


def test_explicit_model_path_canonicalises_keyword() -> None:
    provider = OpenWakeWordProvider(keywords=("alexa",), model_path="/x/alexa_v0.1.onnx")
    assert provider._canonical_keyword("alexa_v0.1") == "alexa"


def test_default_still_uses_bundled_neutral_model() -> None:
    # Regression: the zero-arg construction path loads the neutral bundled
    # default (hey_rhasspy — no branded/trademarked default wake word).
    provider = OpenWakeWordProvider()
    kw = provider._model_kwargs()
    assert kw["wakeword_models"][0].endswith("hey_rhasspy_v0.1.onnx")
