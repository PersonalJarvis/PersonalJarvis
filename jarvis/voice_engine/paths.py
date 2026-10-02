"""Where the engine keeps its environments, models and bench results.

The home lives in the per-user application-data folder of each OS, never next
to the code: a checkout can sit in a cloud-synced folder, and multi-gigabyte
model stores must not be uploaded. The app passes its own location through
``JARVIS_VOICE_ENGINE_HOME`` when it spawns the worker.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ENV_HOME = "JARVIS_VOICE_ENGINE_HOME"
SETUP_STATE_FILE = "setup.json"


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


def venv_python(home: Path | None = None) -> Path:
    """The interpreter of the engine's own environment (created by the app's setup)."""
    venv = (home or engine_home()) / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def read_setup_state(home: Path | None = None) -> dict[str, Any]:
    """What the last finished setup recorded (chosen model, voice, versions).

    An absent or unreadable file is an empty record: "never set up", never an
    error, because a status read must not fail on a half-written home.
    """
    path = (home or engine_home()) / SETUP_STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
