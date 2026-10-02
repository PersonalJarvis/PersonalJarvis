"""One status for the global shortcut listener (just-in-time permissions, AP-4).

``GET /api/settings/keybinds`` carries ``shortcuts_status: {state, detail}`` so
the Shortcuts surfaces can say why a shortcut that worked on Windows does
nothing on a fresh Mac, in one place instead of per row.

Five-layer vocabulary (AP-4): this module holds the source of truth
(:data:`SHORTCUTS_STATES`) and the response schema (:class:`ShortcutsStatus`);
the route serves it; the TypeScript twin is the ``shortcuts_status`` field of
``KeybindsConfig`` in ``frontend/src/hooks/useHotkey.ts``. ``detail`` is a fixed
English sentence per case, not an i18n key: nothing renders it yet, and a surface
that does must translate it by ``state`` (the frontend owner's follow-up).
``tests/unit/ui/web/test_shortcuts_status_parity.py`` regex-reads the TS twin and
fails on drift.

The answer is read silently: it never prompts, never starts or stops a listener
and never touches a lock the tap callback uses.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

from pydantic import BaseModel

log = logging.getLogger(__name__)

#: The three states of the one global shortcut tap. ``ready``: shortcuts can fire
#: (or this host needs no permission for them). ``needs_input_monitoring``: macOS
#: has not allowed Input Monitoring, so the tap is not created. ``unavailable_in_
#: this_mode``: this host or run mode cannot arm global shortcuts at all.
SHORTCUTS_STATES: Final = (
    "ready",
    "needs_input_monitoring",
    "unavailable_in_this_mode",
)

# Fixed English sentences, never built from exception text, paths or titles.
_DETAIL_READY_RESTART = (
    "Input Monitoring is allowed, but macOS delivers no key events to the running "
    "app yet. Quit and reopen Personal Jarvis to finish enabling global shortcuts."
)
_DETAIL_NOT_ASKED = (
    "Global shortcuts need Input Monitoring, which has not been allowed for "
    "Personal Jarvis yet. Buttons and voice keep working."
)
_DETAIL_OFF = (
    "Input Monitoring is off for Personal Jarvis, so global shortcuts do nothing. "
    "Buttons and voice keep working."
)
_DETAIL_RESTRICTED = (
    "A device policy on this Mac blocks Input Monitoring, so global shortcuts are unavailable."
)
_DETAIL_UNREADABLE = (
    "macOS did not report whether Input Monitoring is allowed, so global shortcuts are unavailable."
)
_DETAIL_NO_BACKEND = "Global shortcuts are not available on this desktop."


class ShortcutsStatus(BaseModel):
    """The ``shortcuts_status`` payload (layer 2 of the AP-4 pattern)."""

    state: Literal["ready", "needs_input_monitoring", "unavailable_in_this_mode"]
    detail: str = ""


def shortcuts_status(trigger: Any | None = None) -> ShortcutsStatus:
    """The status of the global shortcut tap right now. Silent, never raises.

    ``trigger`` is the running pipeline's ``HotkeyTrigger`` when there is one;
    it is only asked whether the tap looks deaf (a restart hint inside
    ``detail``), never to start or stop anything.

    Off macOS the permission service answers ``not_required`` and the state is
    what the hotkey capability probe says (the same probe the backend factory
    uses), exactly the pre-existing behaviour: ``ready`` or
    ``unavailable_in_this_mode``.
    """
    from jarvis.platform.permission_service import get_permission_service  # noqa: PLC0415
    from jarvis.platform.permissions import PermissionId, PermissionState  # noqa: PLC0415
    from jarvis.platform.probes import has_hotkey  # noqa: PLC0415

    try:
        capable = has_hotkey()
    except Exception:  # noqa: BLE001 - a failing capability probe reads "unavailable"
        log.debug("The hotkey capability probe failed.", exc_info=True)
        capable = False
    if not capable:
        return ShortcutsStatus(state="unavailable_in_this_mode", detail=_DETAIL_NO_BACKEND)

    state = get_permission_service().check(PermissionId.INPUT_MONITORING)
    if state in (PermissionState.GRANTED, PermissionState.NOT_REQUIRED):
        return ShortcutsStatus(state="ready", detail=_deaf_tap_detail(trigger))
    if state is PermissionState.RESTRICTED:
        return ShortcutsStatus(state="unavailable_in_this_mode", detail=_DETAIL_RESTRICTED)
    if state is PermissionState.UNAVAILABLE:
        return ShortcutsStatus(state="unavailable_in_this_mode", detail=_DETAIL_UNREADABLE)
    detail = _DETAIL_OFF if state is PermissionState.DENIED else _DETAIL_NOT_ASKED
    return ShortcutsStatus(state="needs_input_monitoring", detail=detail)


def _deaf_tap_detail(trigger: Any | None) -> str:
    """The restart hint when the tap is up but hears nothing, else ``""``."""
    probe = getattr(trigger, "deaf_tap_suspected", None)
    if not callable(probe):
        return ""
    try:
        return _DETAIL_READY_RESTART if probe() is True else ""
    except Exception:  # noqa: BLE001 - a failed liveness probe makes no claim
        log.debug("The deaf-tap probe failed.", exc_info=True)
        return ""


__all__ = ["SHORTCUTS_STATES", "ShortcutsStatus", "shortcuts_status"]
