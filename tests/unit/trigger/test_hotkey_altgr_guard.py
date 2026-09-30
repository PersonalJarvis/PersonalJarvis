"""AltGr must not fire a bare ``ctrl+alt`` shortcut.

On an AltGr keyboard layout (German, French, Nordic, ...) Windows reports the
right Alt key as Ctrl+Alt. With hold-to-dictate on ``ctrl+alt``, every ``@``
typed and every both-Alt appshot gesture started a dictation and popped up the
Jarvis Bar (live log, 2026-09-29).
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.trigger.backends import global_hotkeys
from jarvis.trigger.hotkey import HotkeyTrigger, _is_bare_ctrl_alt_chord


def _trigger(combo: str) -> HotkeyTrigger:
    trig = HotkeyTrigger({"dictate": [combo]}, push_to_talk=frozenset({"dictate"}))
    trig._loop = asyncio.get_running_loop()
    return trig


async def _fire(trig: HotkeyTrigger, event_name: str) -> list[str]:
    trig._make_handler(event_name)()
    await asyncio.sleep(0)
    out: list[str] = []
    while not trig._queue.empty():
        out.append(trig._queue.get_nowait())
    return out


@pytest.mark.parametrize(
    ("combo", "expected"),
    [
        ("ctrl+alt", True),
        ("ctrl+alt+shift", True),
        ("ctrl+alt+j", False),  # a real key: AltGr+J types nothing
        ("right_ctrl+alt", False),  # the right Ctrl key is never AltGr
        ("alt", False),
        ("f1+f2", False),
    ],
)
def test_only_bare_ctrl_alt_chords_are_guarded(combo: str, expected: bool) -> None:
    assert _is_bare_ctrl_alt_chord(combo) is expected


async def test_altgr_drops_the_press_but_never_the_release(monkeypatch) -> None:
    monkeypatch.setattr(global_hotkeys, "altgr_is_down", lambda: True)
    trig = _trigger("ctrl+alt")
    assert await _fire(trig, "dictate_press") == []
    assert await _fire(trig, "dictate_release") == ["dictate_release"]


async def test_real_ctrl_alt_still_fires(monkeypatch) -> None:
    monkeypatch.setattr(global_hotkeys, "altgr_is_down", lambda: False)
    assert await _fire(_trigger("ctrl+alt"), "dictate_press") == ["dictate_press"]


async def test_a_chord_with_a_real_key_ignores_altgr(monkeypatch) -> None:
    monkeypatch.setattr(global_hotkeys, "altgr_is_down", lambda: True)
    assert await _fire(_trigger("ctrl+alt+j"), "dictate_press") == ["dictate_press"]


def test_probe_is_a_quiet_no_on_hosts_without_win32() -> None:
    # Off Windows GetAsyncKeyState is unavailable; the guard must never drop.
    if global_hotkeys._async_key_is_down(0x10) is None:
        assert global_hotkeys.altgr_is_down() is False
