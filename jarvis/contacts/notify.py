"""Contact-change notification seam — the single choke point for contact writes.

The contacts package must not import its consumers (wiki mirror today, anything
else tomorrow) — the dependency points the other way (lateral integration goes
through the EventBus). A consumer registers a sink at bootstrap; the
``ContactStore`` calls :func:`notify_contact_changed` after every successful
write. With no sink registered (unit tests, wiki disabled, headless minimal
boot) the call is a zero-overhead no-op, and a sink error must never fail the
contact write itself.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

#: Single source of truth for the ``ContactChanged.action`` vocabulary.
#: Python-only wire format today; apply the five-layer anti-drift pattern
#: (docs/anti-drift-three-layer.md) before this ever crosses SQL/TS/UI.
CONTACT_CHANGE_ACTIONS: tuple[str, ...] = ("created", "updated", "deleted")

#: ``(action, slug, name)`` — action is one of :data:`CONTACT_CHANGE_ACTIONS`.
ContactChangeSink = Callable[[str, str, str], None]

_sink: ContactChangeSink | None = None


def set_contact_change_sink(sink: ContactChangeSink) -> None:
    """Register the process-wide sink (last registration wins)."""
    global _sink
    _sink = sink


def clear_contact_change_sink() -> None:
    global _sink
    _sink = None


def notify_contact_changed(action: str, slug: str, name: str) -> None:
    """Best-effort fan-out after a successful contact write."""
    if action not in CONTACT_CHANGE_ACTIONS:
        log.warning("contacts.notify: unknown action %r dropped (slug=%r)", action, slug)
        return
    sink = _sink
    if sink is None:
        return
    try:
        sink(action, slug, name)
    except Exception:  # noqa: BLE001 — a consumer error must never fail the write
        log.warning(
            "contacts.notify: sink failed for %s %r", action, slug, exc_info=True
        )
