"""macOS system consent surfaces that an AI agent must never answer (P9).

A permission prompt, an authorization sheet or a Touch ID request is a decision
that belongs to the person at the keyboard. The computer-use engine therefore
refuses to dispatch ANY action while one of these windows is frontmost, and
dictation never sends a keystroke into one: the mission stops with the stable
reason code ``blocked_permission`` and the person answers the dialog.

This module is data plus one pure matcher. Nothing is imported at module scope
that a headless host lacks, and nothing runs at import time (AP-26). The owner
list is UNVERIFIED: nobody can run macOS in the development sandbox, the names
are the owning process names (``kCGWindowOwnerName``) that community write-ups
and this repo's own bug history report for these dialogs, and macOS versions
move them around. A miss only means the guard stays quiet for that dialog; the
other layers (the prohibitive agent text, the Accessibility grant itself) are
unchanged.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

log = logging.getLogger(__name__)

#: The ``<token>`` in ``[permission_needed:<token>] `` when the block is a system
#: dialog on screen rather than a missing permission.
SYSTEM_DIALOG_BLOCK: Final = "system_dialog"

#: Owner process names of windows that ask the person for a system-level consent,
#: casefolded (UNVERIFIED, see the module docstring). ``UserNotificationCenter``
#: and ``universalaccessd`` show the privacy prompts (Accessibility, Screen
#: Recording, Input Monitoring, ...); ``SecurityAgent``, ``authorizationhost`` and
#: ``coreautha`` show password and Touch ID authorization; ``CoreServicesUIAgent``
#: shows the Gatekeeper "downloaded from the internet" confirmation.
SYSTEM_CONSENT_OWNERS: Final[frozenset[str]] = frozenset(
    {
        "usernotificationcenter",
        "universalaccessd",
        "securityagent",
        "authorizationhost",
        "coreautha",
        "coreservicesuiagent",
    }
)

#: System Settings is only a consent surface while it shows a privacy pane (the
#: pane where the person flips the switch), not for every Settings window: a
#: mission that was asked to change the wallpaper must still work. The match is
#: on the window title, which needs Screen Recording (the engine only dispatches
#: with it), so a title the OS withholds simply does not match (UNVERIFIED).
SYSTEM_SETTINGS_OWNERS: Final[frozenset[str]] = frozenset({"system settings", "system preferences"})
_PRIVACY_PANE_TITLE_TOKENS: Final[tuple[str, ...]] = (
    "privacy",
    "accessibility",
    "screen recording",
    "screen & system audio",
    "input monitoring",
    "microphone",
    "automation",
    "full disk access",
    "datenschutz",  # i18n-allow: macOS pane title
    "bedienungshilfen",  # i18n-allow: macOS pane title
    "bildschirmaufnahme",  # i18n-allow: macOS pane title
    "eingabeüberwachung",  # i18n-allow: macOS pane title
    "mikrofon",  # i18n-allow: macOS pane title
    "automatisierung",  # i18n-allow: macOS pane title
    "privacidad",  # i18n-allow: macOS pane title
    "accesibilidad",  # i18n-allow: macOS pane title
    "grabación de pantalla",  # i18n-allow: macOS pane title
)

#: Windows that sit above everything but are never a consent dialog and must not
#: hide one that is behind them in the front-to-back list: the menu bar, the Dock,
#: status items and notification banners.
_CHROME_OWNERS: Final[frozenset[str]] = frozenset(
    {
        "window server",
        "dock",
        "systemuiserver",
        "control center",
        "notification center",
        "notificationcenter",
        "spotlight",
    }
)

WindowLister = Callable[[], Sequence[Mapping[str, Any]]]


def is_system_consent_window(owner: str, title: str = "") -> bool:
    """Whether a window of this owner (and title) is a system consent surface."""
    name = (owner or "").strip().casefold()
    if not name:
        return False
    if name in SYSTEM_CONSENT_OWNERS:
        return True
    if name in SYSTEM_SETTINGS_OWNERS:
        lowered = (title or "").casefold()
        return any(token in lowered for token in _PRIVACY_PANE_TITLE_TOKENS)
    return False


def _quartz_on_screen_windows() -> Sequence[Mapping[str, Any]]:
    """The on-screen windows, front to back (macOS only; the import is lazy)."""
    import Quartz  # type: ignore[import-not-found]  # noqa: PLC0415

    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return list(Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or [])


def _is_visible_surface(entry: Mapping[str, Any]) -> bool:
    """A window that can actually be seen: not fully transparent, not a 1-px sliver."""
    try:
        if float(entry.get("kCGWindowAlpha", 1.0) or 0.0) <= 0.0:
            return False
        bounds = entry.get("kCGWindowBounds") or {}
        return float(bounds.get("Width", 2) or 0) > 1 and float(bounds.get("Height", 2) or 0) > 1
    except (TypeError, ValueError, AttributeError):
        # A malformed entry is treated as visible: a dialog must never be skipped
        # because its geometry could not be read.
        log.debug("A window entry has unreadable geometry or alpha.", exc_info=True)
        return True


def _is_own_window(entry: Mapping[str, Any], own_pid: int) -> bool:
    """A window of this very process (Jarvis's own overlays, cursor and pill)."""
    try:
        return int(entry.get("kCGWindowOwnerPID", -1)) == own_pid
    except (TypeError, ValueError):
        return False


def _is_normal_window(entry: Mapping[str, Any]) -> bool:
    """A window on the ordinary application layer (0). A missing layer reads as 0."""
    try:
        return int(entry.get("kCGWindowLayer", 0) or 0) == 0
    except (TypeError, ValueError):
        return True


def frontmost_consent_owner(window_lister: WindowLister | None = None) -> str:
    """The owner of a system consent surface that is in front of every normal window, else ``""``.

    macOS only: the caller decides the platform (the engine calls this on darwin
    only). The window list is ordered front to back. Menu-bar, Dock and
    notification windows, invisible slivers and Jarvis's OWN windows (the orb
    overlay, the virtual cursor, the Esc pill: always-on-top, so they would mask a
    dialog behind them) are skipped. The scan then walks the whole prefix of
    floating windows (layer above 0, other apps' overlays included) and reports the
    first consent surface in it; the first normal application window (layer 0) of
    another app ends the scan with ``""``, because a normal window in front means
    the person already moved on. An unreadable list answers ``""`` (fail open, with
    a debug line): a failed probe must not stop every mission, and a missing consent
    dialog is the case the prohibitive agent text and the grant itself still cover.
    UNVERIFIED on a real Mac: the real window levels of the consent dialogs.
    """
    lister = window_lister or _quartz_on_screen_windows
    try:
        entries = lister()
    except Exception:  # noqa: BLE001 - an unreadable window list is "no dialog seen"
        log.debug("The on-screen window list could not be read.", exc_info=True)
        return ""
    own_pid = os.getpid()
    for entry in entries:
        owner = str(entry.get("kCGWindowOwnerName") or "")
        if owner.strip().casefold() in _CHROME_OWNERS or not _is_visible_surface(entry):
            continue
        if _is_own_window(entry, own_pid):
            continue
        title = str(entry.get("kCGWindowName") or "")
        if is_system_consent_window(owner, title):
            return owner
        if _is_normal_window(entry):
            return ""
    return ""


def agent_detail() -> str:
    """The prohibitive sentence for an LLM tool error while a system dialog is open."""
    return (
        f"[permission_needed:{SYSTEM_DIALOG_BLOCK}] This action cannot run: a macOS system "
        "dialog is open on screen and only the user may answer it. You must not try to "
        "answer, click or dismiss any macOS system dialog yourself, you must not retry "
        "this action, and you must not look for a workaround. Tell the user to answer "
        "the dialog and stop."
    )


def user_detail() -> str:
    """The sentence for people: fixed template, no window title, no owner name."""
    return (
        "A macOS system dialog is open. Personal Jarvis never answers system dialogs "
        "for you: answer it yourself, then try the task again."
    )


__all__ = [
    "SYSTEM_CONSENT_OWNERS",
    "SYSTEM_DIALOG_BLOCK",
    "SYSTEM_SETTINGS_OWNERS",
    "WindowLister",
    "agent_detail",
    "frontmost_consent_owner",
    "is_system_consent_window",
    "user_detail",
]
