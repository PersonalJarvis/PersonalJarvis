"""Shortcut policy: any key combination, mouse buttons, honest collisions.

Three separate guarantees live here, all landed together on 2026-07-28:

1. **Any combination is selectable.** ``validate_hotkey`` hard-refuses only a
   combo with no keys in it; everything it used to refuse is reported through
   a non-blocking caution channel instead (``HotkeyVerdict.cautions``).
2. **Mouse buttons are real shortcut keys** on every OS that can watch them,
   with one shared vocabulary and an honest message where a backend cannot.
3. **Collisions are compared NORMALIZED.** ``ctrl+left_alt+j`` and
   ``ctrl+right_alt+j`` are one registration on Windows; the raw-token check
   they used to face accepted both and left the second silently dead.
"""

from __future__ import annotations

import sys
import types

import pytest

from jarvis.trigger.hotkey import (
    MOUSE_BUTTON_TOKENS,
    HotkeyVerdict,
    combos_collide,
    mouse_hotkeys_available,
    normalized_combo_tokens,
    validate_hotkey,
)

# ----------------------------------------------------------------------
# 1. The caution channel
# ----------------------------------------------------------------------


def test_ctrl_win_is_accepted_and_says_it_is_a_prefix_trigger() -> None:
    """The headline case. Ctrl+Win used to die at the modifier-only gate before
    the Windows gate was even reached. It now saves — with the one thing the
    user cannot know by looking at it: a modifiers-only chord fires on every
    superset, so Ctrl+Win+Left (switch virtual desktop) triggers it too."""
    verdict = validate_hotkey("ctrl+win", platform="win32")

    assert verdict.ok is True
    assert verdict.reason == ""
    assert "Ctrl+Win+Left" in verdict.caution


def test_a_verdict_is_truthy_exactly_when_it_is_ok() -> None:
    assert bool(validate_hotkey("ctrl+alt+j")) is True
    assert bool(validate_hotkey("")) is False


def test_cautions_accumulate_rather_than_shadow_each_other() -> None:
    """Two independent problems must both reach the user."""
    verdict = validate_hotkey("cmd+q", platform="win32")

    assert verdict.ok is True
    assert len(verdict.cautions) == 2
    assert "macOS" in verdict.caution
    assert "no Command key" in verdict.caution


def test_verdict_indexes_like_the_old_tuple() -> None:
    verdict = HotkeyVerdict(False, "Hotkey is empty.")
    assert verdict[0] is False
    assert verdict[1] == "Hotkey is empty."
    assert len(verdict) == 2


# ----------------------------------------------------------------------
# 2. Mouse buttons
# ----------------------------------------------------------------------


@pytest.mark.parametrize("token", sorted(MOUSE_BUTTON_TOKENS))
def test_a_solo_mouse_button_is_a_valid_shortcut(token: str) -> None:
    verdict = validate_hotkey(token, platform="win32")

    assert verdict.ok is True
    # It must NOT inherit the "fires while you type" caution — a mouse button
    # is not a typing key.
    assert "while you type" not in verdict.caution
    assert "does not swallow the click" in verdict.caution


def test_a_mouse_button_combines_with_modifiers() -> None:
    verdict = validate_hotkey("ctrl+mouse_x2", platform="win32")

    assert verdict.ok is True
    assert "does not swallow the click" in verdict.caution


def test_mouse_aliases_fold_onto_one_canonical_token() -> None:
    """``mouse_back`` and ``mouse_x1`` are the same physical button; two
    spellings must never become two registrations."""
    assert normalized_combo_tokens("mouse_back") == {"mouse_x1"}
    assert normalized_combo_tokens("mouse_forward") == {"mouse_x2"}
    assert normalized_combo_tokens("middle_mouse") == {"mouse_middle"}
    assert combos_collide("mouse_back", "mouse_x1") is True


def test_the_primary_buttons_are_deliberately_not_bindable() -> None:
    """Left/right follow the system "swap mouse buttons" setting, so a shortcut
    recorded as 'left' would fire on the physical right button for a left-handed
    user — and binding the primary click globally makes the machine unusable."""
    assert "mouse_left" not in MOUSE_BUTTON_TOKENS
    assert "mouse_right" not in MOUSE_BUTTON_TOKENS


def test_every_mouse_token_is_known_to_all_three_backends() -> None:
    """AP-4 parity: one vocabulary, three translations. A token that reaches
    only one backend is a shortcut that works on one OS and silently does
    nothing on the other two."""
    from jarvis.trigger.backends.global_hotkeys import _MOUSE_TOKEN_TO_VK
    from jarvis.trigger.backends.pynput import _MOUSE_NAME_TO_TOKEN
    from jarvis.trigger.backends.quartz import _MOUSE_BUTTON_TO_TOKEN

    assert set(_MOUSE_TOKEN_TO_VK) == set(MOUSE_BUTTON_TOKENS)
    assert set(_MOUSE_NAME_TO_TOKEN.values()) == set(MOUSE_BUTTON_TOKENS)
    assert set(_MOUSE_BUTTON_TO_TOKEN.values()) == set(MOUSE_BUTTON_TOKENS)


def test_windows_registers_a_mouse_button_as_its_virtual_key() -> None:
    """The library matches on ``GetAsyncKeyState``, which reports mouse buttons
    — but only for a numeric VK token. The friendly token stays readable
    everywhere above the library boundary."""
    from jarvis.trigger.backends.global_hotkeys import _normalize_combo, _to_library_combo

    normalized = _normalize_combo("ctrl+mouse_x2")
    assert normalized == "control + mouse_x2"
    assert _to_library_combo(normalized) == "control + 0x06"


def test_windows_backend_registers_and_removes_the_same_mouse_string() -> None:
    """Registration and removal must be byte-identical, or a stale registration
    survives teardown and bricks the next re-arm (the F1+F2-went-dead class)."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend
    from tests.fakes.fake_global_hotkeys import FakeGlobalHotkeys

    fake = FakeGlobalHotkeys()
    saved = sys.modules.get("global_hotkeys")
    sys.modules["global_hotkeys"] = fake
    try:
        backend = GlobalHotkeysBackend()
        backend.register([["control + mouse_x1", None, lambda: None]])
        assert "control+0x05" in fake.registered
        backend.unregister()
        assert not fake.registered
    finally:
        if saved is not None:
            sys.modules["global_hotkeys"] = saved
        else:
            sys.modules.pop("global_hotkeys", None)


def test_linux_side_button_drives_one_edge_pair() -> None:
    """X11 numbers the side buttons 8 and 9; a chord of modifier + side button
    must fire exactly one down edge and one up edge."""
    from jarvis.trigger.backends.pynput import PynputBackend

    edges: list[str] = []
    backend = PynputBackend()
    backend.register(
        [["control + mouse_x2", lambda: edges.append("down"), lambda: edges.append("up")]]
    )
    assert backend._needs_mouse is True

    backend._on_press_key(types.SimpleNamespace(char=None, name="ctrl_l"))
    backend._on_click(0, 0, types.SimpleNamespace(name="button9"), True)
    backend._on_click(0, 0, types.SimpleNamespace(name="button9"), False)
    backend._on_press_key(types.SimpleNamespace(char=None, name="ctrl_l"))

    assert edges == ["down", "up"]


def test_linux_ignores_an_unmapped_mouse_button() -> None:
    """A button no token names must be inert, never fire the nearest chord."""
    from jarvis.trigger.backends.pynput import PynputBackend

    edges: list[str] = []
    backend = PynputBackend()
    backend.register([["mouse_middle", None, lambda: edges.append("fired")]])

    backend._on_click(0, 0, types.SimpleNamespace(name="scroll_up"), True)

    assert edges == []


def test_linux_keyboard_only_bindings_start_no_mouse_hook() -> None:
    """An idle desktop must not pay for a hook nothing asked for."""
    from jarvis.trigger.backends.pynput import PynputBackend

    backend = PynputBackend()
    backend.register([["control + alt + j", None, lambda: None]])

    assert backend._needs_mouse is False


def test_macos_side_button_drives_one_edge_pair() -> None:
    from jarvis.trigger.backends.quartz import QuartzHotkeyBackend

    edges: list[str] = []
    backend = QuartzHotkeyBackend()
    backend._permission_check = lambda: True
    backend.register(
        [["alt + mouse_x1", lambda: edges.append("down"), lambda: edges.append("up")]]
    )
    assert backend._needs_mouse is True

    backend._handle_flags(1 << 19)  # Option/Alt down
    backend._handle_mouse_down(3)   # side button 1
    backend._handle_mouse_up(3)
    backend._handle_flags(0)

    assert edges == ["down", "up"]


def test_macos_ignores_the_primary_buttons() -> None:
    """Button 0/1 are left/right — the tap never asks for them, and even if one
    arrived it must not touch a chord."""
    from jarvis.trigger.backends.quartz import QuartzHotkeyBackend

    edges: list[str] = []
    backend = QuartzHotkeyBackend()
    backend._permission_check = lambda: True
    backend.register([["mouse_middle", None, lambda: edges.append("fired")]])

    backend._handle_mouse_down(0)
    backend._handle_mouse_down(1)

    assert edges == []


def test_mouse_support_is_reported_per_host_with_an_honest_reason() -> None:
    ok, reason = mouse_hotkeys_available(platform="win32")
    assert ok is True
    assert reason == ""

    ok, reason = mouse_hotkeys_available(platform="haiku")
    assert ok is False
    assert reason
    assert "key combination" in reason


def test_wayland_reports_no_mouse_shortcuts_instead_of_pretending(monkeypatch) -> None:
    import jarvis.platform.probes as probes

    monkeypatch.setattr(probes, "is_wayland", lambda: True)
    ok, reason = mouse_hotkeys_available(platform="linux")

    assert ok is False
    assert "Wayland" in reason


# ----------------------------------------------------------------------
# 3. Collision detection on the NORMALIZED combo
# ----------------------------------------------------------------------


def test_left_and_right_alt_collide_because_windows_cannot_tell_them_apart() -> None:
    """THE bug: both were accepted as different shortcuts, both were shown as
    bound, and the second one was dead the moment it was registered."""
    assert combos_collide("ctrl+left_alt+j", "ctrl+right_alt+j") is True
    assert normalized_combo_tokens("ctrl+left_alt+j") == normalized_combo_tokens(
        "ctrl+right_alt+j"
    )


def test_super_and_win_spellings_collide() -> None:
    assert combos_collide("ctrl+super+space", "ctrl+win+space") is True


def test_a_subset_still_collides() -> None:
    """The polling backends match a chord as soon as its keys are down."""
    assert combos_collide("f1", "f1+f2") is True
    assert combos_collide("f1+f2", "f1") is True


def test_unrelated_combos_do_not_collide() -> None:
    assert combos_collide("ctrl+alt+j", "ctrl+alt+k") is False
    assert combos_collide("f3+f4", "f1+f2") is False


def test_an_unbound_action_never_collides() -> None:
    """An empty key set is a subset of everything — without this guard, every
    save would be refused the moment one shortcut is cleared."""
    assert combos_collide("", "ctrl+alt+j") is False
    assert combos_collide("ctrl+alt+j", "   ") is False
