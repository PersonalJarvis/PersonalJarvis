"""QuartzHotkeyBackend contract: TSM-free chord matching + fail-closed gates.

BUG-077: pynput's darwin keyboard listener dies with an uncatchable SIGILL on
modern macOS (HIToolbox TSM calls off the main queue). The Quartz backend
matches physical keycodes + modifier flags and must never import pynput's
listener machinery. These tests drive the chord matcher directly and pin the
degrade paths; the live tap is exercised on macOS hardware/CI only.
"""

from __future__ import annotations

import re
from pathlib import Path

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


def test_keycode_table_covers_every_token_the_picker_can_record() -> None:
    """The Quartz table must know every key the keybind UI lets a user pick.

    The drift this pins is SILENT and macOS-only. The recorder emits a token,
    the validator accepts it, the route saves it and the row renders it as
    bound — but a token missing from ``_KEYCODE_TO_TOKEN`` never enters the
    held-set, so the chord cannot match and the shortcut is simply dead. No
    error is raised anywhere along that path.

    It is not hypothetical: the whole nav cluster, the entire numpad and
    F13-F20 were offered by the picker and unmatchable on macOS while the
    Windows backend registered all of them. Reading the tokens out of the
    frontend source rather than restating them here is the point — adding a
    bindable cap without its keycode fails this test instead of shipping.
    """
    source = (
        Path(__file__).resolve().parents[3]
        / "jarvis/ui/web/frontend/src/hooks/useHotkey.ts"
    ).read_text(encoding="utf-8")

    named = source.split("_NAMED_KEY_TOKENS", 1)[1].split("};", 1)[0]
    # `Insert: "insert",` -> the token on the right-hand side.
    expected = set(re.findall(r':\s*"([a-z0-9_]+)"', named))
    assert "home" in expected, "frontend token map failed to parse"

    # The generated families `codeToKeyToken` derives by pattern rather than
    # from the map: numpad digits, and the F-row up to what a keyboard has.
    expected |= {f"numpad_{n}" for n in range(10)}
    expected |= {f"f{n}" for n in range(1, 21)}

    missing = sorted(expected - set(_KEYCODE_TO_TOKEN.values()))
    assert not missing, (
        "these keys are bindable in the picker but have no macOS keycode, so "
        f"the shortcut would save and never fire: {missing}"
    )


def test_permission_probe_is_not_run_per_keystroke() -> None:
    """The TCC probe must stay OFF the per-event path (AP-9 / AP-26 in spirit).

    A session event tap sees every keystroke on the machine, and the grant
    probe below it is two native ObjC calls that are deliberately uncached.
    Running it per event overran the tap's callback deadline, and macOS
    answers that by DISABLING the tap — the reported "the Mac shortcut works
    sometimes, or not at all".
    """
    calls = 0

    def _probe() -> bool:
        nonlocal calls
        calls += 1
        return True

    backend = QuartzHotkeyBackend()
    backend._permission_check = _probe
    backend.register([["control + j", lambda: None, None]])

    backend._handle_flags(_FLAG_CTRL)
    for _ in range(50):
        backend._handle_key_down(_KC_J)
        backend._handle_key_up(_KC_J)

    # Without the TTL this is well over a hundred native probes.
    assert calls <= 2, f"grant probed {calls}x for 100 key events"


def test_revoked_permission_still_disarms_the_chord() -> None:
    """The TTL is a throttle, not a bypass: a revoked grant still closes."""
    granted = True
    fired: list[str] = []
    backend = QuartzHotkeyBackend()
    backend._permission_check = lambda: granted
    backend.register([["control + j", lambda: fired.append("press"), None]])

    backend._handle_flags(_FLAG_CTRL)
    backend._handle_key_down(_KC_J)
    assert fired == ["press"]

    granted = False
    backend._permission_checked_at = 0.0  # the TTL elapsing, deterministically
    backend._handle_key_up(_KC_J)
    backend._handle_key_down(_KC_J)
    assert fired == ["press"], "a revoked grant must not fire the chord again"
    assert backend._held == set()


def test_stop_clears_the_cached_grant_verdict() -> None:
    """A re-arm re-asks TCC — the user may have just granted it."""
    backend = _backend_with_permission()
    backend.register([["control + j", lambda: None, None]])
    backend._handle_flags(_FLAG_CTRL)
    assert backend._permission_cache is True

    backend.stop()
    assert backend._permission_cache is None


def test_stop_is_idempotent_without_start() -> None:
    backend = _backend_with_permission()
    backend.stop()
    backend.stop()
    assert backend._started is False
