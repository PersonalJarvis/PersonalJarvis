"""The microphone check must degrade, never abort the wizard.

Regression guard: `sd.query_devices()` raises (e.g. PortAudioError "library not
found") on any host with no audio backend — a headless server, or a Linux
desktop without libportaudio2. If that error propagated, the wizard would die
before step_finalize writes the .setup-complete marker, and the app would re-run
its whole onboarding (the same failure mode as the step-7 Jarvis-Agent crash).
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

from jarvis.setup import wizard


def test_mic_check_survives_missing_audio_backend() -> None:
    """A PortAudio/backend failure in the mic check returns quietly, no raise."""
    fake_sd = types.ModuleType("sounddevice")

    def _boom(*_a: object, **_k: object) -> object:
        raise OSError("PortAudio library not found")

    fake_sd.query_devices = _boom  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        # Must NOT raise — a mic-check failure may never abort setup.
        wizard.step_mic_check()
