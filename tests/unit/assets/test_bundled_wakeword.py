"""The word-agnostic openWakeWord backbones must ship in a clean checkout, and
ONLY those two — no branded hey_* model may ever be smuggled back in."""
from pathlib import Path

import jarvis.assets as assets


def test_backbones_are_bundled_and_resolvable():
    models = assets.bundled_wakeword_models()
    assert models is not None, "backbones must be present in a clean checkout"
    assert models["melspec"].is_file()
    assert models["embedding"].is_file()


def test_wakeword_dir_holds_only_word_agnostic_models():
    d = Path(assets.__file__).resolve().parent / "wakeword"
    onnx = sorted(p.name for p in d.glob("*.onnx"))
    assert onnx == ["embedding_model.onnx", "melspectrogram.onnx"], (
        f"only word-agnostic backbones allowed, found: {onnx}"
    )
