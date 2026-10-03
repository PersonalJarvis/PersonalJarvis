"""Event-driven Windows AppShot shortcuts, including short single-key taps.

The shared polling backend is appropriate for held voice chords, but can miss
an entire tap between samples. Reuse the existing listener lifecycle and chord
matcher while keeping Windows character tokens stable across modifier changes.
"""

from __future__ import annotations

from jarvis.trigger.backends.pynput import PynputBackend

_NUMPAD_TOKENS = {
    0x6A: "multiply_key", 0x6B: "add_key", 0x6D: "subtract_key",
    0x6E: "decimal_key", 0x6F: "divide_key",
}


class AppshotKeyEvents(PynputBackend):
    """One keyboard hook; inherited stop() joins it before any re-registration."""

    @property
    def ready(self) -> bool:
        return self._started and self._listener is not None

    def _token_for(self, key) -> str | None:
        vk = getattr(key, "vk", None)
        if vk is None:
            vk = getattr(getattr(key, "value", None), "vk", None)
        if isinstance(vk, int):
            if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
                # Ctrl+C may report '\x03', and Shift changes a character's
                # case. The virtual letter key remains C on both edges.
                return chr(vk).lower()
            if 0x60 <= vk <= 0x69:
                return f"numpad_{vk - 0x60}"
            if vk in _NUMPAD_TOKENS:
                return _NUMPAD_TOKENS[vk]
        return super()._token_for(key)
