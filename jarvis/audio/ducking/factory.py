"""Platform/capability factory for the audio ducker backend."""
from __future__ import annotations

import importlib.util
import logging
import shutil
import sys
from typing import Any

from jarvis.audio.ducking.null import NullDucker
from jarvis.audio.ducking.protocol import AudioDucker

log = logging.getLogger("jarvis.audio.ducking")


def _pycaw_available() -> bool:
    return importlib.util.find_spec("pycaw") is not None


def _osascript_available() -> bool:
    return shutil.which("osascript") is not None


def make_audio_ducker(cfg: Any | None = None) -> AudioDucker:
    """Windows + pycaw → WindowsPycawDucker; macOS + osascript →
    MacOSScriptDucker; otherwise a logged NullDucker.
    """
    if sys.platform == "win32" and _pycaw_available():
        from jarvis.audio.ducking.windows import WindowsPycawDucker

        return WindowsPycawDucker()
    if sys.platform == "darwin" and _osascript_available():
        from jarvis.audio.ducking.macos import MacOSScriptDucker

        return MacOSScriptDucker.from_config(cfg)
    log.info("Audio ducking unavailable (platform=%s) — no-op.", sys.platform)
    return NullDucker()
