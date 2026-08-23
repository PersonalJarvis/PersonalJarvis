"""A non-default instance of the app (``jarvis.core.instance``) never arms the
global hotkeys or the wake word — the microphone and the key combos belong to
the default app. Both arming sites (boot + live keybind change) go through
``_build_hotkey_bindings``; wake activation goes through ``set_wake_activation``.
"""
from __future__ import annotations

import asyncio

from jarvis.core.instance import INSTANCE_ENV_VAR
from jarvis.speech.pipeline import SpeechPipeline


def _pipeline() -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._call_hotkeys = ["f3+f4"]
    pipe._hangup_hotkeys = ["f1+f2"]
    pipe._ptt_hotkeys = []
    pipe._dictate_hotkeys = ["ctrl+alt+d"]
    pipe._dictate_toggle_hotkeys = []
    pipe._paste_last_hotkeys = []
    pipe._dictate_mode = "hold"
    pipe._wake_plan = None
    pipe._wake_reload_event = asyncio.Event()
    return pipe


def test_default_instance_arms_the_configured_hotkeys(monkeypatch) -> None:
    monkeypatch.delenv(INSTANCE_ENV_VAR, raising=False)
    bindings, edges = _pipeline()._build_hotkey_bindings()
    assert bindings["call"] == ["f3+f4"]
    assert bindings["dictate"] == ["ctrl+alt+d"]
    assert "dictate" in edges


def test_dev_instance_arms_no_hotkey_at_all(monkeypatch) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    bindings, edges = _pipeline()._build_hotkey_bindings()
    assert bindings == {"call": [], "hangup": []}
    assert edges == set()


def test_dev_instance_cannot_enable_the_wake_word_live(monkeypatch) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    pipe = _pipeline()
    pipe.set_wake_activation(True)
    assert pipe._wake_word_enabled is False
    assert pipe._openwakeword_enabled is False
    assert pipe._whisper_wake_enabled is False


def test_default_instance_wake_activation_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv(INSTANCE_ENV_VAR, raising=False)
    pipe = _pipeline()
    pipe.set_wake_activation(True)
    # No plan resident → parks the detectors, but the preference is recorded.
    assert pipe._wake_word_enabled is True
