"""Bundled binary assets shipped with the Jarvis package.

Currently:
- ``wakeword/``: ONNX models for openWakeWord (hey_rhasspy_v0.1 bundled as the
  neutral shipped default, hey_jarvis_v0.1 kept for users who type "Jarvis",
  melspectrogram, embedding). Loaded via :func:`bundled_wakeword_models` from
  the openWakeWord provider so first-boot stays offline.
- ``vad/``: the Silero VAD ONNX model (MIT-licensed, ~2.2 MB) that powers
  end-of-speech detection (:mod:`jarvis.audio.vad`). Bundled so the core voice
  loop closes a turn on a base install too: the ``silero-vad`` pip package (which
  drags torch) lives only in the ``[local-voice]`` extra, but the model file
  itself is torch-free and run via base ``onnxruntime``. Loaded via
  :func:`bundled_silero_vad_model`.

Future bundles (e.g. packaged voice clips) live under this package.
"""
from __future__ import annotations

from pathlib import Path

_WAKEWORD_DIR = Path(__file__).resolve().parent / "wakeword"
_WAKEWORD_FILES = {
    "wakeword": "hey_rhasspy_v0.1.onnx",
    "melspec": "melspectrogram.onnx",
    "embedding": "embedding_model.onnx",
}

_VAD_DIR = Path(__file__).resolve().parent / "vad"
_SILERO_VAD_FILE = "silero_vad.onnx"


def bundled_wakeword_models() -> dict[str, Path] | None:
    """Return absolute paths to the bundled openWakeWord ONNX assets.

    Returns ``None`` when any of the three required files is missing (partial
    checkout, slim install, or a forthcoming opt-in extras-only layout). The
    caller (``openwakeword_provider``) then falls back to openWakeWord's
    built-in keyword names + auto-download.

    Keys: ``wakeword`` (the hey_rhasspy_v0.1 detector — neutral shipped default),
    ``melspec`` (preprocessing), ``embedding`` (shared backbone).
    """
    if not _WAKEWORD_DIR.is_dir():
        return None
    resolved: dict[str, Path] = {}
    for key, filename in _WAKEWORD_FILES.items():
        path = _WAKEWORD_DIR / filename
        if not path.is_file():
            return None
        resolved[key] = path
    return resolved


def bundled_silero_vad_model() -> Path | None:
    """Return the absolute path to the bundled Silero VAD ONNX model, or ``None``.

    ``None`` when the file is missing (partial checkout / slim install); the
    caller (:meth:`jarvis.audio.vad.SileroEndpointer._ensure_model`) then falls
    back to locating the model inside the installed ``silero-vad`` pip package.

    Bundling this ~2.2 MB MIT model in-repo is what makes end-of-speech detection
    work out-of-the-box on a fresh base install — without it the voice loop can
    hear a wake word but never close the utterance, because ``silero-vad`` is an
    opt-in extra. The bundled file is byte-identical to the pip package's model,
    so the torch-free onnxruntime inference in ``vad.py`` is unchanged.
    """
    path = _VAD_DIR / _SILERO_VAD_FILE
    return path if path.is_file() else None


__all__ = ["bundled_wakeword_models", "bundled_silero_vad_model"]
