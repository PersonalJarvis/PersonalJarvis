"""Idempotent fullscreen control through the cross-platform pywebview API."""

from __future__ import annotations

import threading
import weakref
from typing import Any

_lock = threading.Lock()
_states: weakref.WeakKeyDictionary[Any, bool] = weakref.WeakKeyDictionary()


def set_window_fullscreen(window: Any, enabled: bool) -> dict[str, Any]:
    """Set fullscreen on a supported live window, restoring its previous frame on exit."""
    toggle = getattr(window, "toggle_fullscreen", None)
    if not callable(toggle):
        return {"ok": False, "reason": "fullscreen_unavailable"}
    with _lock:
        current = _states.get(window, bool(getattr(window, "fullscreen", False)))
        if current != enabled:
            toggle()
            _states[window] = enabled
        return {"ok": True, "fullscreen": enabled}
