"""Jarvis's own keystrokes must not fire Jarvis's own shortcuts.

A listener sees synthetic input exactly like human input, and on macOS the
collision is the DEFAULT configuration rather than a corner case: the platform
paste chord is ``cmd+v`` and the offered hold-to-dictate key is a bare ``cmd``.
So every dictation ended by pasting its own transcript, the session event tap
saw the Cmd flag rise, and the dictation shortcut fired a fresh press into a
lane that was still finishing — refused as ``already_running``, which put the
Jarvis Bar's failure mark on a dictation that had just pasted perfectly (live
log, 2026-08-09 22:05:57).

These tests pin the two halves of the repair that can regress independently:
the actuation layer marking its own synthesis, and the trigger dropping only
the edges it is allowed to drop.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.platform import self_input
from jarvis.trigger.hotkey import SELF_INPUT_SUPPRESSED_EVENTS, HotkeyTrigger


@pytest.fixture(autouse=True)
def _clean_window():
    self_input.reset()
    yield
    self_input.reset()


async def _armed_trigger() -> HotkeyTrigger:
    """A trigger whose handlers are the real ones, bound to this test's loop.

    No backend and no OS registration: the handlers ``_build_bindings`` hands a
    backend are ordinary callables, so a test can invoke exactly what a real
    key edge would.
    """
    trig = HotkeyTrigger(
        {"dictate": ["cmd"], "hangup": ["f1+f2"]},
        push_to_talk=frozenset({"dictate"}),
    )
    trig._loop = asyncio.get_running_loop()
    return trig


async def _pushed(trigger: HotkeyTrigger) -> list[str]:
    """Drain what the trigger queued, after the loop hop has run."""
    await asyncio.sleep(0)
    out: list[str] = []
    while not trigger._queue.empty():
        out.append(trigger._queue.get_nowait())
    return out


# --------------------------------------------------------------------------
# The stamp
# --------------------------------------------------------------------------
def test_the_window_is_open_inside_the_block_and_shuts_after_it() -> None:
    assert self_input.synthetic_input_recent() is False
    with self_input.synthetic_input():
        assert self_input.synthetic_input_recent() is True
    # Still ours for the delivery lag ...
    assert self_input.synthetic_input_recent() is True
    # ... and not a millisecond of it is claimed retroactively.
    assert self_input.synthetic_input_recent(window_ms=0) is False


def test_a_long_typing_burst_never_expires_mid_way() -> None:
    """A ``type_text`` runs for seconds. A timestamp taken once at the start
    would go stale inside it, which is why the depth counter exists."""
    with self_input.synthetic_input():
        assert self_input.synthetic_input_recent(window_ms=0) is True


def test_nesting_holds_the_window_until_the_outermost_block_ends() -> None:
    with self_input.synthetic_input():
        with self_input.synthetic_input():
            pass
        assert self_input.synthetic_input_recent(window_ms=0) is True
    assert self_input.synthetic_input_recent(window_ms=0) is False


def test_the_window_closes_even_when_the_synthesis_raises() -> None:
    with pytest.raises(ValueError):
        with self_input.synthetic_input():
            raise ValueError("unknown key")
    assert self_input.synthetic_input_recent(window_ms=0) is False


# --------------------------------------------------------------------------
# The filter
# --------------------------------------------------------------------------
async def test_a_self_typed_paste_does_not_start_a_dictation() -> None:
    """The reported bug, at the trigger's own seam."""
    trig = await _armed_trigger()
    press = trig._make_handler("dictate_press")

    with self_input.synthetic_input():
        press()  # the Cmd of our own cmd+v

    assert await _pushed(trig) == []


async def test_a_real_press_after_the_window_still_starts_a_dictation() -> None:
    trig = await _armed_trigger()
    press = trig._make_handler("dictate_press")

    with self_input.synthetic_input():
        pass
    self_input.reset()  # the window has closed
    press()

    assert await _pushed(trig) == ["dictate_press"]


async def test_a_release_edge_is_never_suppressed() -> None:
    """A swallowed release strands the latch it was meant to clear — the mic
    stays open with no key held. Worse than any phantom press."""
    trig = await _armed_trigger()
    release = trig._make_handler("dictate_release")

    with self_input.synthetic_input():
        release()

    assert await _pushed(trig) == ["dictate_release"]


async def test_no_stop_gesture_can_be_suppressed() -> None:
    """The kill switch, the mission cancel and the hangup key must fire while
    Jarvis is typing — that is exactly when a user reaches for them."""
    for event in ("kill", "cu_cancel", "hangup"):
        assert event not in SELF_INPUT_SUPPRESSED_EVENTS

    trig = await _armed_trigger()
    hangup = trig._make_handler("hangup")
    with self_input.synthetic_input():
        hangup()

    assert await _pushed(trig) == ["hangup"]


def test_the_deny_list_only_names_events_that_start_something() -> None:
    """Fail-open by construction: an unnamed (future) event keeps firing."""
    assert SELF_INPUT_SUPPRESSED_EVENTS == {
        "ptt_press",
        "dictate_press",
        "dictate",
        "dictate_toggle",
        "paste_last",
    }
    assert not any(name.endswith("_release") for name in SELF_INPUT_SUPPRESSED_EVENTS)


# --------------------------------------------------------------------------
# The seam: actuation has to OPEN the window, or the filter above guards
# nothing. Both backends, because the collision exists on both (cmd+v next to
# a bare-cmd dictation key on macOS; ctrl+v next to a ctrl one on Windows).
# --------------------------------------------------------------------------
def test_the_posix_actuator_marks_its_own_chord() -> None:
    import types

    from jarvis.cu.actuate.posix import PosixActuator

    seen: list[bool] = []

    class _Keyboard:
        def press(self, _key) -> None:
            seen.append(self_input.synthetic_input_recent(window_ms=0))

        def release(self, _key) -> None:
            pass

    actuator = object.__new__(PosixActuator)
    actuator._keyboard = _Keyboard()
    actuator._keys = types.MappingProxyType({"cmd": "cmd"})

    actuator.key_combo(["cmd", "v"])

    assert seen == [True, True], "the paste chord was posted outside the window"
    assert self_input.synthetic_input_recent(window_ms=0) is False


def test_the_posix_actuator_marks_its_own_typing() -> None:
    from jarvis.cu.actuate.posix import PosixActuator

    seen: list[bool] = []

    class _Keyboard:
        def type(self, _text) -> None:
            seen.append(self_input.synthetic_input_recent(window_ms=0))

    actuator = object.__new__(PosixActuator)
    actuator._keyboard = _Keyboard()

    actuator.type_text("hello", delay_s=0)

    assert seen == [True]


def test_an_unknown_key_never_leaves_the_window_open() -> None:
    """The resolution raise happens before anything is posted; a window that
    outlived it would deafen the shortcuts for no keystroke at all."""
    import types

    from jarvis.cu.actuate.posix import PosixActuator

    actuator = object.__new__(PosixActuator)
    actuator._keyboard = object()
    actuator._keys = types.MappingProxyType({})

    with pytest.raises(ValueError):
        actuator.key_combo(["nonexistent_key"])

    assert self_input.synthetic_input_recent(window_ms=0) is False
