"""X11 reports Shift+digit as its symbol; the pynput backend maps it back to the key."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from jarvis.trigger.backends.pynput import PynputBackend

#: keysym -> [(keycode, level)] as the X server lists them. "(" also sits on
#: the keypad's base level (keycode 187), which must not count.
_US = {0x28: [(187, 0), (18, 1)], 0x21: [(10, 1)]}  # "(" on the 9 key, "!" on 1
_DE = {0x29: [(188, 0), (18, 1)], 0x21: [(10, 1)]}  # ")" on the 9 key (German)
_LEVEL0 = {18: 0x39, 10: 0x31, 187: 0x28, 188: 0x29}  # "9", "1", keypad "(" ")"


class FakeDisplay:
    def __init__(self, keysyms: dict[int, list[tuple[int, int]]]) -> None:
        self.keysyms = keysyms

    def keysym_to_keycodes(self, keysym: int):
        return iter(self.keysyms.get(keysym, []))

    def keycode_to_keysym(self, keycode: int, index: int) -> int:
        assert index == 0
        return _LEVEL0.get(keycode, 0)


def _key(char: str) -> SimpleNamespace:
    return SimpleNamespace(char=char, vk=ord(char))


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def make(layout: dict[int, int]) -> PynputBackend:
        instance = PynputBackend()
        instance._x_display = FakeDisplay(layout)  # noqa: SLF001
        return instance

    return make


@pytest.mark.parametrize(("layout", "symbol"), [(_US, "("), (_DE, ")")])
def test_shift_9_is_the_9_key_on_every_layout(backend, layout, symbol: str) -> None:
    assert backend(layout)._token_for(_key(symbol)) == "9"  # noqa: SLF001


def test_ctrl_shift_9_fires_and_releases(backend) -> None:
    fired: list[str] = []
    instance = backend(_US)
    instance.register([("ctrl+shift+9", lambda: fired.append("down"))])

    instance._on_press_key(SimpleNamespace(name="ctrl"))  # noqa: SLF001
    instance._on_press_key(SimpleNamespace(name="shift"))  # noqa: SLF001
    instance._on_press_key(_key("("))  # noqa: SLF001
    # Shift let go first: the 9 key now reports "9" — the same token, nothing stuck.
    instance._on_release_key(SimpleNamespace(name="shift"))  # noqa: SLF001
    instance._on_release_key(_key("9"))  # noqa: SLF001

    assert fired == ["down"]
    assert "9" not in instance._held and "(" not in instance._held  # noqa: SLF001


def test_letters_and_unknown_symbols_keep_their_character(backend) -> None:
    instance = backend(_US)
    assert instance._token_for(_key("A")) == "a"  # noqa: SLF001
    assert instance._token_for(_key("€")) == "€"  # noqa: SLF001 - no keycode: as reported


def test_an_unshifted_symbol_stays_itself(backend) -> None:
    """A symbol only on a base level (keypad "(" here) is its own key."""
    instance = backend({0x28: [(187, 0)]})
    assert instance._token_for(_key("(")) == "("  # noqa: SLF001


def test_without_an_x_display_the_character_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    instance = PynputBackend()
    instance._x_display = False  # noqa: SLF001 - opening the display failed before
    assert instance._token_for(_key("(")) == "("  # noqa: SLF001
