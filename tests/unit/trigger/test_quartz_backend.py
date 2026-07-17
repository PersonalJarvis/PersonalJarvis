"""QuartzHotkeyBackend contract: TSM-free chord matching + fail-closed gates.

BUG-065: pynput's darwin keyboard listener dies with an uncatchable SIGILL on
modern macOS (HIToolbox TSM calls off the main queue). The Quartz backend
matches physical keycodes + modifier flags and must never import pynput's
listener machinery. These tests drive the chord matcher directly and pin the
degrade paths; the live tap is exercised on macOS hardware/CI only.
"""

from __future__ import annotations

from jarvis.trigger.backends.quartz import (
    _FLAG_MASK_TO_TOKEN,
    _KEYCODE_TO_TOKEN,
    QuartzHotkeyBackend,
)

_KC_J = 0x26
_KC_SPACE = 0x31
_FLAG_CTRL = 1 << 18
_FLAG_ALT = 1 << 19


def _backend_with_permission(granted: bool = True) -> QuartzHotkeyBackend:
    backend = QuartzHotkeyBackend()
    backend._permission_check = lambda: granted
    return backend


def test_chord_fires_on_down_edge_only_once() -> None:
    fired: list[str] = []
    backend = _backend_with_permission()
    backend.register([["control + alt + j", lambda: fired.append("press"), None]])

    backend._handle_flags(_FLAG_CTRL | _FLAG_ALT)
    assert fired == []
    backend._handle_key_down(_KC_J)
    assert fired == ["press"]
    # Holding the chord does not re-fire.
    backend._handle_key_down(_KC_J)
    assert fired == ["press"]
    assert backend.received_any_event() is True


def test_push_to_talk_fires_both_edges() -> None:
    events: list[str] = []
    backend = _backend_with_permission()
    backend.register(
        [["control + space", lambda: events.append("down"), lambda: events.append("up")]]
    )

    backend._handle_flags(_FLAG_CTRL)
    backend._handle_key_down(_KC_SPACE)
    assert events == ["down"]
    backend._handle_key_up(_KC_SPACE)
    assert events == ["down", "up"]


def test_modifier_release_breaks_the_chord() -> None:
    events: list[str] = []
    backend = _backend_with_permission()
    backend.register(
        [["control + j", lambda: events.append("down"), lambda: events.append("up")]]
    )

    backend._handle_flags(_FLAG_CTRL)
    backend._handle_key_down(_KC_J)
    backend._handle_flags(0)  # ctrl released while j still held
    assert events == ["down", "up"]


def test_right_control_folds_to_ctrl() -> None:
    fired: list[str] = []
    backend = _backend_with_permission()
    backend.register([["right_control + j", lambda: fired.append("press"), None]])

    backend._handle_flags(_FLAG_CTRL)
    backend._handle_key_down(_KC_J)
    assert fired == ["press"]


def test_permission_revocation_clears_chords_and_blocks_handlers() -> None:
    fired: list[str] = []
    backend = _backend_with_permission()
    backend.register([["control + j", lambda: fired.append("press"), None]])

    backend._permission_check = lambda: False
    backend._handle_flags(_FLAG_CTRL)
    backend._handle_key_down(_KC_J)
    assert fired == []
    assert backend._held == set()


def test_start_without_permission_is_a_noop(caplog) -> None:
    backend = _backend_with_permission(granted=False)
    backend.start()
    assert backend._started is False
    assert backend._tap is None
    assert any("hotkeys disabled" in r.message.lower() for r in caplog.records)


def test_start_without_quartz_degrades(monkeypatch, caplog) -> None:
    import sys

    backend = _backend_with_permission(granted=True)
    monkeypatch.setitem(sys.modules, "Quartz", None)  # import -> ImportError
    backend.start()
    assert backend._started is False
    assert backend._tap is None


def test_unknown_keycode_is_ignored() -> None:
    backend = _backend_with_permission()
    backend.register([["control + j", lambda: None, None]])
    backend._handle_key_down(0xFF)  # not in the table
    assert backend._held == set()


def test_keycode_table_covers_the_combo_vocabulary() -> None:
    """Every letter, digit, and F-key token has a physical-key mapping."""
    tokens = set(_KEYCODE_TO_TOKEN.values())
    for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
        assert ch in tokens
    for n in range(1, 13):
        assert f"f{n}" in tokens
    assert {"space", "enter", "esc", "tab"} <= tokens
    # Modifier flags cover the four canonical modifier tokens.
    assert {t for _, t in _FLAG_MASK_TO_TOKEN} == {"shift", "ctrl", "alt", "cmd"}


def test_stop_is_idempotent_without_start() -> None:
    backend = _backend_with_permission()
    backend.stop()
    backend.stop()
    assert backend._started is False
