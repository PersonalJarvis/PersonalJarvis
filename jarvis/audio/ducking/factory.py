"""Platform/capability factory for the audio ducker backend."""
from __future__ import annotations

import importlib.util
import logging
import sys

from jarvis.audio.ducking.null import NullDucker
from jarvis.audio.ducking.protocol import AudioDucker

log = logging.getLogger("jarvis.audio.ducking")


def _pycaw_available() -> bool:
    return importlib.util.find_spec("pycaw") is not None


def make_audio_ducker() -> AudioDucker:
    """Windows + pycaw → WindowsPycawDucker; otherwise a logged NullDucker."""
    if sys.platform == "win32" and _pycaw_available():
        from jarvis.audio.ducking.windows import WindowsPycawDucker

        return WindowsPycawDucker()
    log.info("Audio ducking unavailable (platform=%s) — no-op.", sys.platform)
    return NullDucker()
