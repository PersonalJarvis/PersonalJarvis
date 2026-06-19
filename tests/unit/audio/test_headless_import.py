"""H4+M2: the audio/speech modules must import on a headless/slim box even when
sounddevice (PortAudio) is unavailable, and the import-cleanliness gate must
forbid an eager module-scope import of the audio/GPU/desktop packages so such a
regression cannot silently land on the boot chain.

Seam-level: sounddevice is forced absent via sys.modules so a fresh import takes
the guarded (try/except → None) path — proven on this Windows host without
actually removing the real PortAudio.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_AUDIO_MODULES = [
    "jarvis.audio.player",
    "jarvis.audio.capture",
    "jarvis.speech.diagnose",
    "jarvis.speech.voice_compare",
]


@pytest.mark.parametrize("modname", _AUDIO_MODULES)
def test_module_imports_without_sounddevice(modname: str) -> None:
    # Run in a FRESH interpreter so forcing sounddevice absent cannot pollute
    # this test process's module cache. With `sys.modules['sounddevice'] = None`
    # an `import sounddevice` raises ImportError; importing the module must then
    # degrade to ``sd = None`` instead of raising at import time (the
    # OSError("PortAudio library not found") trap on a slim Linux box without
    # libportaudio2).
    code = (
        "import sys\n"
        "sys.modules['sounddevice'] = None\n"
        f"import {modname} as m\n"
        "assert m.sd is None, 'expected sd=None when sounddevice is unavailable'\n"
        "print('IMPORT_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"import raised without sounddevice:\n{result.stderr}"
    assert "IMPORT_OK" in result.stdout


def test_player_portaudio_error_sentinel_without_sounddevice() -> None:
    # H4 review HIGH: `except sd.PortAudioError` is a runtime expression — with
    # sd=None it would evaluate None.PortAudioError and raise AttributeError,
    # masking the real failure. The module must resolve the exception type to a
    # safe sentinel at import so the except clause never touches None.
    code = (
        "import sys\n"
        "sys.modules['sounddevice'] = None\n"
        "import jarvis.audio.player as p\n"
        "assert p.sd is None\n"
        "assert isinstance(p._PortAudioError, type)\n"
        "assert issubclass(p._PortAudioError, BaseException)\n"
        "print('SENTINEL_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "SENTINEL_OK" in result.stdout


def test_import_gate_forbids_audio_and_gpu_modules() -> None:
    # M2: the gate's forbidden set must include the eager audio/GPU/desktop
    # packages so a future bare module-scope import can never regress onto the
    # boot chain unnoticed.
    gate_path = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "check_import_clean.py"
    spec = importlib.util.spec_from_file_location("_check_import_clean_test", gate_path)
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    for name in ("sounddevice", "torch", "mss", "pyautogui", "pynput", "ptyprocess"):
        assert name in gate.FORBIDDEN_MODULE_SCOPE, name
