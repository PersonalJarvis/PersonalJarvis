"""Process-global bridge from the Tk overlay drop to the app's brain intake.

The floating overlay (``ui/orb/overlay.py``, ``jarvis/ui/whisperbar/overlay.py``)
runs its Tk mainloop in a daemon thread and must stay ignorant of the asyncio
loop, the EventBus and the brain. When a file/text is dropped on it, the overlay
calls :func:`dispatch_drop` from the Tk thread; the desktop bridge
(``jarvis/ui/desktop_app.py``) registers the real handler via
:func:`set_drop_handler` — which marshals onto the asyncio loop and runs
``jarvis.brain.drop_context.ingest_drop``.

This avoids threading a callback through the surface factory / orb / bar
constructors (low-touch wiring into fragile GUI code). The handler is a single
process-global slot — there is one overlay per process.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

#: ``(file_paths, dragged_text)`` — exactly one populated per drop.
OnDrop = Callable[[list[str], str], None]

_HANDLER: OnDrop | None = None


def set_drop_handler(handler: OnDrop | None) -> None:
    """Register (or clear with ``None``) the overlay-drop handler."""
    global _HANDLER
    _HANDLER = handler


def dispatch_drop(paths: list[str], text: str) -> bool:
    """Deliver a drop to the registered handler. Never raises (Tk-thread safe).

    Returns ``True`` if a handler was present (regardless of whether it then
    failed internally — a handler crash is swallowed so it can't wedge the Tk
    mainloop), ``False`` if no handler is registered.
    """
    handler = _HANDLER
    if handler is None:
        return False
    try:
        handler(paths, text)
    except Exception:  # noqa: BLE001 — a Tk-thread callback must never propagate.
        log.debug("overlay drop handler failed", exc_info=True)
    return True


__all__ = ["OnDrop", "set_drop_handler", "dispatch_drop"]
