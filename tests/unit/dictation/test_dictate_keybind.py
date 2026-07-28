"""The dictate keybind: validator rules, binding table, and hotkey edges.

Two of these tests pin defects that were found by CALLING the validator rather
than reading it: ``cmd`` was not in the modifier vocabulary, so every
macOS-critical chord (Cmd+Q, Cmd+W, Cmd+C, Cmd+Space) passed validation while
the Windows equivalents were refused; and F12, which the OS reserves for the
debugger, was accepted.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.core.config import DictationConfig, TriggerConfig
from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY
from jarvis.speech.pipeline import PipelineState, SpeechPipeline
from jarvis.trigger.hotkey import validate_hotkey

# --------------------------------------------------------------------------
# Registry wiring
# --------------------------------------------------------------------------


def test_dictate_is_a_first_class_keybind_action() -> None:
    assert "dictate" in KEYBIND_ACTIONS
    assert KEYBIND_TOML_KEY["dictate"] == "hotkey_dictate"
    assert hasattr(TriggerConfig(), "hotkey_dictate")


def test_dictation_ships_unbound() -> None:
    """No combination is free on every machine — the user picks one."""
    assert TriggerConfig().hotkey_dictate == ""


# --------------------------------------------------------------------------
# Validator — the macOS gap
# --------------------------------------------------------------------------


@pytest.mark.parametrize("combo", ["cmd+c", "cmd+v", "cmd+q", "cmd+w", "cmd+space"])
def test_macos_system_shortcuts_are_refused(combo: str) -> None:
    ok, reason = validate_hotkey(combo, platform="darwin")
    assert ok is False
    assert "macOS" in reason


@pytest.mark.parametrize("combo", ["cmd+d", "cmd+shift+d", "cmd+alt+space"])
def test_usable_command_chords_are_accepted_on_macos(combo: str) -> None:
    ok, reason = validate_hotkey(combo, platform="darwin")
    assert ok is True, reason


def test_command_chord_is_refused_where_there_is_no_command_key() -> None:
    ok, reason = validate_hotkey("cmd+d", platform="win32")
    assert ok is False
    assert "Command key" in reason


# --------------------------------------------------------------------------
# Validator — reserved keys and the pre-existing rules
# --------------------------------------------------------------------------


def test_f12_is_refused_as_reserved() -> None:
    ok, reason = validate_hotkey("f12", platform="win32")
    assert ok is False
    assert "reserved" in reason.lower()


@pytest.mark.parametrize(
    ("combo", "expected"),
    [
        ("ctrl+alt+d", True),
        ("f5", True),
        ("f3+f4", True),
        ("win+d", False),
        ("alt+f4", False),
        ("ctrl+c", False),
        ("j", False),
        ("ctrl", False),
        ("", False),
    ],
)
def test_existing_rules_are_unchanged(combo: str, expected: bool) -> None:
    ok, _reason = validate_hotkey(combo, platform="win32")
    assert ok is expected


# --------------------------------------------------------------------------
# Binding table
# --------------------------------------------------------------------------


def _pipeline(*, dictate: list[str], mode: str = "hold") -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._call_hotkeys = ["f3+f4"]
    pipe._hangup_hotkeys = ["f1+f2"]
    pipe._ptt_hotkeys = []
    pipe._dictate_hotkeys = dictate
    pipe._dictate_mode = mode
    return pipe


def test_hold_mode_asks_for_both_key_edges() -> None:
    bindings, edges = _pipeline(dictate=["ctrl+alt+d"])._build_hotkey_bindings()
    assert bindings["dictate"] == ["ctrl+alt+d"]
    assert "dictate" in edges


def test_toggle_mode_fires_once_on_release() -> None:
    bindings, edges = _pipeline(
        dictate=["ctrl+alt+d"], mode="toggle"
    )._build_hotkey_bindings()
    assert bindings["dictate"] == ["ctrl+alt+d"]
    assert "dictate" not in edges


def test_unbound_dictation_arms_nothing_but_leaves_voice_intact() -> None:
    bindings, edges = _pipeline(dictate=[])._build_hotkey_bindings()
    assert "dictate" not in bindings
    assert edges == set()
    # The existing voice shortcuts must be untouched by this feature.
    assert bindings["call"] == ["f3+f4"]
    assert bindings["hangup"] == ["f1+f2"]


# --------------------------------------------------------------------------
# Hotkey edges
# --------------------------------------------------------------------------


class _RecordingPipeline(SpeechPipeline):
    """Counts start/stop calls without touching a microphone."""

    def __init__(self) -> None:  # noqa: D107 — deliberately bypasses the real ctor
        self.started: list[str] = []
        self.stopped = 0
        self._dictate_key_down = False
        self._dictation_task = None

    def start_dictation(self, *, target: str = "chat") -> bool:  # type: ignore[override]
        self.started.append(target)
        return True

    def stop_dictation(self) -> bool:  # type: ignore[override]
        self.stopped += 1
        return True


def test_press_starts_once_even_though_the_backend_polls() -> None:
    """The Windows backend re-fires on_press while the chord is held."""
    pipe = _RecordingPipeline()
    pipe._on_dictate_press()
    pipe._on_dictate_press()
    pipe._on_dictate_press()
    # "auto" is the shipped [dictation].target; it is resolved against the live
    # foreground window when the recording ENDS, not here.
    assert pipe.started == ["auto"]


def test_the_key_follows_the_configured_target() -> None:
    pipe = _RecordingPipeline()
    pipe._dictation_cfg = DictationConfig(target="chat")
    pipe._on_dictate_press()
    assert pipe.started == ["chat"]


def test_release_submits_once() -> None:
    pipe = _RecordingPipeline()
    pipe._on_dictate_press()
    pipe._on_dictate_release()
    pipe._on_dictate_release()  # stray second edge
    assert pipe.stopped == 1


def test_a_refused_start_does_not_swallow_the_next_press() -> None:
    pipe = _RecordingPipeline()

    def _refuse(*, target: str = "chat") -> bool:
        return False

    pipe.start_dictation = _refuse  # type: ignore[assignment]
    pipe._on_dictate_press()
    assert pipe._dictate_key_down is False

    pipe.start_dictation = lambda *, target="chat": pipe.started.append(target) or True  # type: ignore[assignment]
    pipe._on_dictate_press()
    assert pipe.started == ["auto"]


def test_toggle_starts_then_stops() -> None:
    pipe = _RecordingPipeline()
    pipe._on_dictate_toggle()
    assert pipe.started == ["auto"]

    class _RunningTask:
        def done(self) -> bool:
            return False

    pipe._dictation_task = _RunningTask()  # type: ignore[assignment]
    pipe._on_dictate_toggle()
    assert pipe.stopped == 1


# --------------------------------------------------------------------------
# start_dictation target
# --------------------------------------------------------------------------


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover
        raise AssertionError("no transcription in this unit test")


@pytest.mark.asyncio
async def test_start_dictation_records_the_requested_target() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = _StubSTT()
    pipe._dictation_task = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._ptt_mode = False
    pipe._state = PipelineState.IDLE
    pipe._input_device = "default"
    pipe._hangup_event = asyncio.Event()
    pipe._dictation_cfg = DictationConfig()

    assert pipe.start_dictation(target="insert") is True
    assert pipe._dictation_target == "insert"
    assert pipe.dictation_active() is True

    task = pipe._dictation_task
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass  # teardown only — the session body never runs in this test
