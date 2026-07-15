"""Self-input suppression for the Escape-to-cancel listener.

The Computer-Use engine can legitimately SEND an Escape keystroke as part
of a mission (close a dialog, leave a menu). OS-level hotkey listeners see
synthetic input exactly like human input, so without suppression Jarvis
would cancel itself the moment it types Esc.

The actuation layer stamps a monotonic timestamp right before it
synthesizes any combo containing Escape; the indicator controller ignores
Escape hotkey events that arrive within ``SUPPRESS_WINDOW_MS`` of the
stamp. Thread-safe (actuation runs off the event loop).
"""

from __future__ import annotations

import threading
import time

SUPPRESS_WINDOW_MS = 400

_ESC_NAMES = frozenset({"esc", "escape"})

_lock = threading.Lock()
_last_synthetic_esc_ns = 0


def stamp_if_escape(keys: list[str] | tuple[str, ...]) -> bool:
    """Record "Jarvis is about to type Esc" when the combo contains it."""
    if not any(str(k).strip().lower() in _ESC_NAMES for k in keys):
        return False
    global _last_synthetic_esc_ns
    with _lock:
        _last_synthetic_esc_ns = time.monotonic_ns()
    return True


def esc_recently_synthesized(
    window_ms: int = SUPPRESS_WINDOW_MS,
) -> bool:
    """True while a Jarvis-typed Esc is fresh enough to explain a hotkey hit."""
    with _lock:
        stamp = _last_synthetic_esc_ns
    if stamp == 0:
        return False
    return (time.monotonic_ns() - stamp) < window_ms * 1_000_000


def reset() -> None:
    """Test hook: forget any previous stamp."""
    global _last_synthetic_esc_ns
    with _lock:
        _last_synthetic_esc_ns = 0


__all__ = [
    "SUPPRESS_WINDOW_MS",
    "esc_recently_synthesized",
    "reset",
    "stamp_if_escape",
]
