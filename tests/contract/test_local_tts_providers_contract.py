"""Every keyless on-device TTS provider honours the TTSProvider shape.

Covers the local family (Piper, VOICEVOX, SAPI5): the pipeline calls
``synthesize(text, voice=..., language_code=...)`` and reads ``name`` /
``supports_streaming`` / ``runs_on_device`` on all of them alike.
"""

from __future__ import annotations

import inspect

import pytest

from jarvis.plugins.tts.piper_local import PiperLocalTTS
from jarvis.plugins.tts.sapi5_tts import Sapi5TTS
from jarvis.plugins.tts.voicevox_tts import VoicevoxTTS


@pytest.mark.parametrize("cls", [PiperLocalTTS, VoicevoxTTS, Sapi5TTS])
def test_local_tts_shape(cls) -> None:
    inst = cls()
    assert isinstance(inst.name, str) and inst.name
    assert isinstance(inst.supports_streaming, bool)
    assert inst.runs_on_device is True
    params = inspect.signature(inst.synthesize).parameters
    assert {"text", "voice", "language_code"} <= set(params)
    assert inspect.isasyncgenfunction(cls.synthesize)
    assert isinstance(inst.list_voices(), list)
