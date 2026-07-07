"""OpenWakeWordProvider only ever loads an explicit user-trained model.

Design 2026-07-07: the product ships NO named wake model. Without a
``model_path`` the provider is a logged no-op; with one, the word-agnostic
melspec/embedding backbones bundled in-repo are reused so the user's model
loads offline. It must never fall back to openWakeWord built-in keyword
names — that auto-downloads third-party brand models.
"""
from __future__ import annotations

import jarvis.assets
from jarvis.plugins.wake.openwakeword_provider import OpenWakeWordProvider


def test_no_model_path_means_no_model_and_no_upstream_download() -> None:
    provider = OpenWakeWordProvider()  # no model_path
    assert provider._model_kwargs() is None  # sentinel: nothing to load


def test_ensure_model_without_a_model_is_a_logged_noop() -> None:
    provider = OpenWakeWordProvider()
    provider._ensure_model()
    assert provider._model is None
    assert provider._runtime_unavailable is True  # detect() no-ops cleanly


def test_custom_model_reuses_wordless_backbones(tmp_path) -> None:
    onnx = tmp_path / "my_word.onnx"
    onnx.write_bytes(b"stub")
    provider = OpenWakeWordProvider(model_path=str(onnx))
    kw = provider._model_kwargs()
    assert kw["inference_framework"] == "onnx"
    assert kw["wakeword_models"] == [str(onnx)]
    assert kw["melspec_model_path"].endswith("melspectrogram.onnx")
    assert kw["embedding_model_path"].endswith("embedding_model.onnx")


def test_custom_model_without_backbone_bundle_hands_bare_path(
    tmp_path, monkeypatch
) -> None:
    # Partial checkout: the backbone bundle is absent -> hand the bare path to
    # openWakeWord (it resolves backbones from its own package resources).
    monkeypatch.setattr(jarvis.assets, "bundled_wakeword_models", lambda: None)

    onnx = tmp_path / "my_word.onnx"
    onnx.write_bytes(b"stub")
    provider = OpenWakeWordProvider(model_path=str(onnx))
    kw = provider._model_kwargs()
    assert kw["wakeword_models"] == [str(onnx)]
    assert "melspec_model_path" not in kw


def test_bundled_assets_are_only_the_wordless_backbones() -> None:
    bundle = jarvis.assets.bundled_wakeword_models()
    assert bundle is not None
    assert sorted(bundle.keys()) == ["embedding", "melspec"]


def test_canonical_keyword_strips_version_suffix() -> None:
    # A model file "my_word_v0.1.onnx" makes openWakeWord report the score
    # under "my_word_v0.1". We must report the canonical configured keyword.
    provider = OpenWakeWordProvider(keywords=("my_word",))
    assert provider._canonical_keyword("my_word_v0.1") == "my_word"
    assert provider._canonical_keyword("my_word") == "my_word"


def test_canonical_keyword_passthrough_unknown() -> None:
    provider = OpenWakeWordProvider(keywords=("my_word",))
    assert provider._canonical_keyword("other_v0.1") == "other_v0.1"
