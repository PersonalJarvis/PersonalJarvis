"""Where the engine keeps its environments, models and bench results.

The home lives in the per-user application-data folder of each OS, never next
to the code: a checkout can sit in a cloud-synced folder, and multi-gigabyte
model stores must not be uploaded. The app passes its own location through
``JARVIS_VOICE_ENGINE_HOME`` when it spawns the worker.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_HOME = "JARVIS_VOICE_ENGINE_HOME"


def engine_home() -> Path:
    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "JarvisVoiceEngine"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "JarvisVoiceEngine"
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "jarvis-voice-engine"


def models_dir() -> Path:
    return engine_home() / "models"


def results_dir() -> Path:
    return engine_home() / "bench-results"
