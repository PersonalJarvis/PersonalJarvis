"""Process-global bridge from the Tk overlay drop to the app's brain intake.

The floating overlay (``ui/orb/overlay.py``, ``jarvis/ui/jarvisbar/overlay.py``)
runs its Tk mainloop in a daemon thread and must stay ignorant of the asyncio
loop, the EventBus and the brain. When a file/text is dropped on it, the overlay
calls :func:`dispatch_drop` from the Tk thread; the desktop bridge
(``jarvis/ui/desktop_app.py``) registers the real handler via
:func:`set_drop_handler` — which marshals onto the asyncio loop and runs
``jarvis.brain.drop_context.ingest_drop``.

The bridge is bidirectional. The return leg (:func:`set_drop_result_sink` /
:func:`report_drop_result`) carries the intake's verdict back to the overlay so
it can confirm the drop visually — the outbound leg alone cannot, because the
intake runs later on the asyncio loop.

This avoids threading a callback through the surface factory / orb / bar
constructors (low-touch wiring into fragile GUI code). Both slots are single
process-globals — there is one overlay per process.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

#: ``(file_paths, dragged_text)`` — exactly one populated per drop.
OnDrop = Callable[[list[str], str], None]

#: ``on_result(accepted)`` — the RETURN leg of the same bridge. The intake runs
#: asynchronously on the backend loop, so the overlay cannot learn from
#: ``dispatch_drop`` whether the content actually became context; without this
#: leg the bar had nothing to confirm with and stayed silent after every drop.
#: ``True`` = captured, ``False`` = nothing usable was in it.
OnDropResult = Callable[[bool], None]

_HANDLER: OnDrop | None = None
_RESULT_SINK: OnDropResult | None = None


def set_drop_handler(handler: OnDrop | None) -> None:
    """Register (or clear with ``None``) the overlay-drop handler."""
    global _HANDLER
    _HANDLER = handler


def set_drop_result_sink(sink: OnDropResult | None) -> None:
    """Register (or clear with ``None``) the overlay's drop-result receiver.

    The overlay surface registers this when it wires its drop target; the
    backend calls :func:`report_drop_result` once the intake has run. One
    process-global slot, for the same reason the handler is one: there is a
    single overlay per process.
    """
    global _RESULT_SINK
    _RESULT_SINK = sink


def report_drop_result(accepted: bool) -> bool:
    """Tell the overlay how the drop it delivered ended. Never raises.

    Returns ``True`` if a sink was present. A sink that fails internally is
    swallowed and still counts as delivered — a broken confirmation animation
    must never propagate into the backend turn that produced it.
    """
    sink = _RESULT_SINK
    if sink is None:
        return False
    try:
        sink(bool(accepted))
    except Exception:  # noqa: BLE001 — cosmetic feedback, never fatal.
        log.debug("overlay drop-result sink failed", exc_info=True)
    return True


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


__all__ = [
    "OnDrop",
    "OnDropResult",
    "dispatch_drop",
    "report_drop_result",
    "set_drop_handler",
    "set_drop_result_sink",
]
