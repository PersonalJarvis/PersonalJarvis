"""OpenWakeWordProvider loads a configurable user-trained model.

The custom-wake-word feature needs the provider to load any ONNX model the
user supplies, while reusing the bundled word-agnostic melspec/embedding
backbones so the wake path stays offline. Without a model_path there is
nothing to load — the product ships no named model (design 2026-07-07).
"""
from __future__ import annotations

from jarvis.plugins.wake.openwakeword_provider import OpenWakeWordProvider


def test_explicit_model_path_is_used_with_bundled_backbones() -> None:
    fake = "/models/my_word_v0.1.onnx"
    provider = OpenWakeWordProvider(keywords=("my_word",), model_path=fake)
    kw = provider._model_kwargs()

    assert kw["wakeword_models"] == [fake]
    assert kw["inference_framework"] == "onnx"
    # Bundled melspec + embedding are reused so any model loads offline.
    assert kw["melspec_model_path"].endswith("melspectrogram.onnx")
    assert kw["embedding_model_path"].endswith("embedding_model.onnx")


def test_explicit_model_path_canonicalises_keyword() -> None:
    provider = OpenWakeWordProvider(
        keywords=("my_word",), model_path="/x/my_word_v0.1.onnx"
    )
    assert provider._canonical_keyword("my_word_v0.1") == "my_word"


def test_default_has_no_model_to_load() -> None:
    # Design 2026-07-07: the zero-arg construction path has NOTHING to load —
    # no bundled model, no built-in names, no auto-download.
    provider = OpenWakeWordProvider()
    assert provider._model_kwargs() is None
