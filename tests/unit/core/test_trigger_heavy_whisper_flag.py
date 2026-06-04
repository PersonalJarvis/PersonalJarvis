"""TriggerConfig gates the heavy local faster-whisper path.

Default is the lightweight path: openWakeWord only, no faster-whisper, no GPU,
no multi-GB download. The heavy RollingWhisperWake backstop + VAD-stability
probe become an opt-in power-user extra.
"""
from __future__ import annotations

from jarvis.core.config import TriggerConfig


def test_heavy_local_whisper_defaults_false() -> None:
    cfg = TriggerConfig()
    assert cfg.heavy_local_whisper is False


def test_heavy_local_whisper_can_be_enabled() -> None:
    cfg = TriggerConfig(heavy_local_whisper=True)
    assert cfg.heavy_local_whisper is True
