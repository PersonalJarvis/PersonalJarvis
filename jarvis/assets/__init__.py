"""Bundled binary assets shipped with the Jarvis package.

Currently:
- ``wakeword/``: ONNX models for openWakeWord (hey_jarvis_v0.1, melspectrogram,
  embedding). Loaded via :func:`bundled_wakeword_models` from the openWakeWord
  provider so first-boot stays offline.

Future bundles (e.g. local VAD profiles, packaged voice clips) live under this
package.
"""
from __future__ import annotations

from pathlib import Path

_WAKEWORD_DIR = Path(__file__).resolve().parent / "wakeword"
_WAKEWORD_FILES = {
    "wakeword": "hey_jarvis_v0.1.onnx",
    "melspec": "melspectrogram.onnx",
    "embedding": "embedding_model.onnx",
}


def bundled_wakeword_models() -> dict[str, Path] | None:
    """Return absolute paths to the bundled openWakeWord ONNX assets.

    Returns ``None`` when any of the three required files is missing (partial
    checkout, slim install, or a forthcoming opt-in extras-only layout). The
    caller (``openwakeword_provider``) then falls back to openWakeWord's
    built-in keyword names + auto-download.

    Keys: ``wakeword`` (the hey_jarvis_v0.1 detector), ``melspec``
    (preprocessing), ``embedding`` (shared backbone).
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


__all__ = ["bundled_wakeword_models"]
