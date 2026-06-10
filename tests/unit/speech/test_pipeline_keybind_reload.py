"""Live keybind re-arm wiring on the SpeechPipeline.

These lock the pipeline half of "a keybind change takes effect without an app
restart" (the user report: "I set a key but pressing it does nothing"):

* ``set_keybinds`` updates only the actions passed and flips
  ``_hotkey_reload_event`` (the live-apply signal, mirroring ``set_wake_plan``).
* ``_hotkey_reload_loop`` waits on that event and re-arms the live
  ``HotkeyTrigger`` with the CURRENT combos (Call/Hangup/Talk-PTT).

The trigger is faked — ``HotkeyTrigger.rearm`` itself is covered in
``tests/unit/trigger/test_hotkey.py``; here we only prove the pipeline drives it
with the right bindings.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from jarvis.speech.pipeline import SpeechPipeline


class _FakeTTS:
    name = "fake-tts"
    supports_streaming = False

    async def synthesize(  # type: ignore[no-untyped-def]
        self, text: str, language_code=None
    ) -> AsyncIterator[bytes]:  # pragma: no cover
        if False:
            yield b""


def _pipe() -> SpeechPipeline:
    return SpeechPipeline(tts=_FakeTTS(), bus=None, enable_whisper_wake=False)


def test_set_keybinds_updates_only_passed_actions_and_flips_event():
    pipe = _pipe()
    assert not pipe._hotkey_reload_event.is_set()
    # Only PTT is changed; Call/Hangup keep their construction defaults.
    pipe.set_keybinds(ptt=["ctrl+right_alt+j"])
    assert pipe._ptt_hotkeys == ["ctrl+right_alt+j"]
    assert pipe._call_hotkeys == ("ctrl+right_alt+j", "f3+f4")  # untouched
    assert pipe._hangup_hotkeys == ("f1+f2",)  # untouched
    assert pipe._hotkey_reload_event.is_set()


class _FakeTrigger:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, set]] = []

    async def rearm(self, bindings, push_to_talk=frozenset()):  # noqa: ANN001
        self.calls.append((bindings, set(push_to_talk)))


async def _run_reload_once(pipe: SpeechPipeline, trigger: _FakeTrigger) -> None:
    task = asyncio.create_task(pipe._hotkey_reload_loop(trigger))
    await asyncio.sleep(0.02)  # let the loop consume the set event + call rearm
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_reload_loop_rearms_with_current_combos_including_ptt():
    pipe = _pipe()
    pipe._call_hotkeys = ["f7+f8"]
    pipe._hangup_hotkeys = ["f1+f2"]
    pipe._ptt_hotkeys = ["ctrl+right_alt+j"]
    trigger = _FakeTrigger()
    pipe._hotkey_reload_event.set()

    await _run_reload_once(pipe, trigger)

    assert trigger.calls, "the reload loop must call rearm once the event is set"
    bindings, ptt = trigger.calls[0]
    assert bindings == {
        "call": ["f7+f8"],
        "hangup": ["f1+f2"],
        "ptt": ["ctrl+right_alt+j"],
    }
    assert ptt == {"ptt"}
    assert not pipe._hotkey_reload_event.is_set()  # cleared after handling


async def test_reload_loop_omits_ptt_binding_when_unset():
    """No PTT combo configured → no 'ptt' binding and no push_to_talk event."""
    pipe = _pipe()
    pipe._call_hotkeys = ["f3+f4"]
    pipe._hangup_hotkeys = ["f1+f2"]
    pipe._ptt_hotkeys = []
    trigger = _FakeTrigger()
    pipe._hotkey_reload_event.set()

    await _run_reload_once(pipe, trigger)

    bindings, ptt = trigger.calls[0]
    assert "ptt" not in bindings
    assert ptt == set()
