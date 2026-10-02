"""Screen Recording access for the capture consumers (just-in-time permissions).

Every screen capture on macOS is gated by the Screen Recording permission, and
the gate fails SILENTLY: without the grant the capture returns the desktop
wallpaper and no error. This module is the one place the capture consumers go
through, with two rules from the permission rebuild (docs/macos-permissions.md, 4.6):

* **Helpers never ask.** A per-frame fast path, a region grab or a status probe
  reads the state through :func:`screen_recording_state` (silent, cheap) and
  degrades. It never makes macOS show a dialog and never opens a card.
* **Only a user gesture asks.** The entry point of a capture that a person
  started (the ``screenshot`` tool body, a screen-context request, the first
  Computer-Use frame of a step) calls :func:`require_screen_recording`. That
  asks macOS once (the service rate-limits it) and only a live GRANTED lets the
  capture proceed; anything else raises :class:`ScreenCaptureRefused` whose text
  names the permission. No path refuses because OUR OWN preflight says "not
  granted" before the OS was asked, and none captures on a state the OS has not
  granted.

The silent-failure trap is checked at the capture site by
:func:`verify_frame_is_real`, AFTER every grab: it re-reads the state fresh (a
revoke that lands during the grab or the stability loop is seen, the entry gate
ran earlier) and refuses unless the state is granted; while the state claims
GRANTED it also looks at the window list (cached for a few seconds), because a
photographic wallpaper is not a "blank" frame. UNVERIFIED on a real Mac: that a
denied capture is wallpaper-only and that window titles of other apps are hidden
then (both community-observed, the title gating is what the permission port's own
oracle relies on).

Nothing here initialises at import time and no framework is imported at module
scope (AP-26): Quartz is imported inside the one function that needs it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

from jarvis.platform.permissions import PermissionId, PermissionState

log = logging.getLogger(__name__)

_READY: frozenset[PermissionState] = frozenset(
    {PermissionState.GRANTED, PermissionState.NOT_REQUIRED}
)
# States where the window-title oracle (the deep read) may still prove a grant
# that the per-process-frozen preflight denies (BUG-161).
_DEEP_RETRY_STATES: frozenset[PermissionState] = frozenset(
    {PermissionState.NOT_GRANTED, PermissionState.NOT_DETERMINED, PermissionState.DENIED}
)
_REASON_OF_STATE: dict[PermissionState, str] = {
    PermissionState.DENIED: "denied",
    PermissionState.RESTRICTED: "restricted",
    PermissionState.UNAVAILABLE: "unavailable",
    PermissionState.NOT_DETERMINED: "not_determined",
}

#: Largest per-channel spread (0..255) of the sampled pixels that still counts
#: as "one flat colour". Real UI content, a photo wallpaper and a dimmed login
#: screen all exceed it; a solid desktop, a black frame and a blank window do not.
_BLANK_CHANNEL_SPREAD = 4
#: How many pixels the blank test samples. A strided byte slice, no Pillow.
_BLANK_SAMPLE_PIXELS = 2048
#: A window smaller than this (input units) says nothing about the grant.
_MIN_EVIDENCE_WINDOW_PX = 100
#: How long a "the grant is usable" window-list verdict is reused (seconds). The
#: window list is read at most this often per process on the normal path.
_EVIDENCE_TTL_S = 5.0
#: A flat frame is the cheap extra trigger: it accepts only a younger verdict.
_BLANK_EVIDENCE_TTL_S = 1.0


class ScreenCaptureRefused(RuntimeError):
    """A screen capture macOS does not allow, with honest permission text.

    ``str(exc)`` is the AGENT sentence (prohibitive, starts with
    ``[permission_needed:screen_recording] ``): it is what a tool error or a
    mission log carries. ``user_detail`` is the people-facing sentence for a spoken
    or rendered refusal. Neither contains exception text, paths or window titles.
    A ``RuntimeError`` subclass so every existing ``except RuntimeError`` around a
    capture keeps working.
    """

    permission = PermissionId.SCREEN_RECORDING.value

    def __init__(self, agent_detail: str, user_detail: str, reason: str = "") -> None:
        super().__init__(agent_detail)
        self.agent_detail = agent_detail
        self.user_detail = user_detail
        self.reason = reason


def permission_gate() -> Any:
    """The ``PermissionGate`` the capture consumers use: the process service.

    A seam: a test monkeypatches this function (or hands a gate to the function
    that takes one) to inject ``FakePermissionService``.
    """
    from jarvis.platform.permission_service import get_permission_service  # noqa: PLC0415

    return get_permission_service()


# ----------------------------------------------------------------------
# Silent reads (helpers, per-frame fast paths, GET routes)
# ----------------------------------------------------------------------


def screen_recording_state(gate: Any | None = None, *, deep: bool = True) -> PermissionState:
    """The live Screen Recording state. Silent: it never asks and never publishes.

    The cheap preflight comes first. The preflight is frozen per process and can
    only go stale NEGATIVE, so a "not granted" answer is re-read once through the
    window-title oracle (``check_deep``) before it is believed: a grant the user
    gave mid-session must not read as missing until a restart (BUG-161). A gate
    without ``check_deep`` (a scripted fake) answers with ``check`` alone.

    ``deep=False`` is for a GET status probe: it never runs the oracle (a window
    enumeration whose side effects on a real Mac are unverified), so it answers
    from the preflight and the remembered proof only.
    """
    gate = gate if gate is not None else permission_gate()
    state = gate.check(PermissionId.SCREEN_RECORDING)
    if not deep or state not in _DEEP_RETRY_STATES:
        return state
    deep = getattr(gate, "check_deep", None)
    if not callable(deep):
        return state
    return deep(PermissionId.SCREEN_RECORDING)


def state_allows_capture(state: PermissionState) -> bool:
    """Whether a state lets a capture proceed: GRANTED, or not required off macOS."""
    return state in _READY


def screen_recording_blocked(gate: Any | None = None) -> bool:
    """``True`` when a capture would not be allowed right now. Silent."""
    return not state_allows_capture(screen_recording_state(gate))


def refusal_for_state(state: PermissionState) -> ScreenCaptureRefused:
    """Honest text for a state read WITHOUT asking (a helper's degradation)."""
    from jarvis.platform.permission_service import (  # noqa: PLC0415
        agent_detail_for,
        user_detail_for,
    )

    reason = _REASON_OF_STATE.get(state, "needs_settings")
    family = PermissionId.SCREEN_RECORDING
    return ScreenCaptureRefused(
        agent_detail_for(family, reason),
        user_detail_for(family, reason),
        reason,
    )


def refusal_pending() -> ScreenCaptureRefused:
    """Honest text for a capture that waits on a macOS dialog (not a denial)."""
    from jarvis.platform.permission_service import (  # noqa: PLC0415
        agent_detail_for,
        user_detail_for,
    )

    family = PermissionId.SCREEN_RECORDING
    # The Screen Recording dialog only offers "Open System Settings" (it has no
    # Allow button), so the wording is the needs_settings one, not not_determined.
    return ScreenCaptureRefused(
        agent_detail_for(family, "needs_settings", asking=True),
        user_detail_for(family, "needs_settings", asking=True),
        "pending",
    )


def _refusal_from_result(result: Any) -> ScreenCaptureRefused:
    return ScreenCaptureRefused(
        str(getattr(result, "agent_detail", "") or ""),
        str(getattr(result, "user_detail", "") or ""),
        str(getattr(result, "reason", "") or ""),
    )


# ----------------------------------------------------------------------
# The gesture entry points (they may ask)
# ----------------------------------------------------------------------


def require_screen_recording(
    feature: str,
    *,
    gate: Any | None = None,
    trace_id: UUID | str | None = None,
) -> None:
    """Gesture entry: make sure a capture is allowed, asking macOS if it is not.

    For a capture a PERSON started. Blocking (the service may call into the OS):
    call it from a worker thread, or use :func:`require_screen_recording_async`.
    The granted fast path is one cached read, so a per-frame caller pays nothing.
    Raises :class:`ScreenCaptureRefused` unless the permission is live GRANTED
    (or not required on this platform). ``wait_s`` is 0 on purpose: the Screen
    Recording dialog only offers "Open System Settings", so waiting for it is
    pointless; the answer reaches the user through the permission episode.
    """
    gate = gate if gate is not None else permission_gate()
    if screen_recording_state(gate) in _READY:
        return
    result = gate.ensure(
        PermissionId.SCREEN_RECORDING,
        feature=feature,
        interactive=True,
        wait_s=0.0,
        trace_id=trace_id,
    )
    if not result.granted:
        raise _refusal_from_result(result)


async def require_screen_recording_async(
    feature: str,
    *,
    gate: Any | None = None,
    trace_id: UUID | str | None = None,
) -> None:
    """:func:`require_screen_recording` for the event loop (never blocks it)."""
    gate = gate if gate is not None else permission_gate()
    # The state read may enumerate windows (the deep fallback): off the loop.
    if await asyncio.to_thread(screen_recording_state, gate) in _READY:
        return
    result = await gate.ensure_async(
        PermissionId.SCREEN_RECORDING,
        feature=feature,
        interactive=True,
        wait_s=0.0,
        trace_id=trace_id,
    )
    if not result.granted:
        raise _refusal_from_result(result)


# ----------------------------------------------------------------------
# Pixel sanity check (the silent-failure trap)
# ----------------------------------------------------------------------

# The features that told the permission service about an unusable grant and have
# not seen a verified-real frame since: the success path only touches the service
# for these, so a healthy per-frame path costs one set lookup.
_failed_use_reported: set[str] = set()


def frame_is_blank(size: tuple[int, int], pixels: Any, *, bytes_per_pixel: int = 3) -> bool:
    """``True`` when a frame is one flat colour (or has no pixels at all).

    Samples about 2048 pixels with a strided byte slice per channel, so it costs
    microseconds and needs no Pillow. ``bytes_per_pixel`` is 3 for RGB and 4 for
    the BGRX frames mss hands out; channel order does not matter for a spread.
    A sparse screen (one small control on an empty window) can sample as flat: a
    flat frame only tightens the window-list verdict in :func:`verify_frame_is_real`,
    it never refuses on its own. The pixel step is coprime with the width, so the
    samples do not line up on a few columns of a power-of-two native size.
    """
    width, height = size
    count = int(width) * int(height)
    stride = max(1, int(bytes_per_pixel))
    if width <= 0 or height <= 0 or len(pixels) < count * stride:
        return True
    step = max(1, count // _BLANK_SAMPLE_PIXELS)
    while step > 1 and math.gcd(step, int(width)) != 1:
        step += 1
    end = count * stride
    for channel in range(min(3, stride)):
        sample = pixels[channel : end : stride * step]
        if not sample:
            return True
        if max(sample) - min(sample) > _BLANK_CHANNEL_SPREAD:
            return False
    return True


def _default_window_evidence() -> tuple[int, int] | None:
    """``(foreign windows, of them with a readable title)`` on screen, else ``None``.

    Another app's window TITLE is Screen-Recording-gated data (the permission
    port's own oracle relies on it), while the window itself is still listed. So
    "other apps' windows exist but none has a readable title" is the signature of
    a missing grant, and "some title is readable" proves the grant works right now.
    UNVERIFIED on a real Mac. ``None`` when the native bridge is missing.
    """
    try:
        import Quartz  # type: ignore[import-not-found]  # noqa: PLC0415

        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
    except Exception:  # noqa: BLE001 - a missing native bridge is "no evidence"
        log.debug("The window list for the capture sanity check is unavailable.", exc_info=True)
        return None
    own_pid = os.getpid()
    foreign = titled = 0
    try:
        for window in list(windows or ()):
            if int(window.get("kCGWindowOwnerPID", own_pid)) == own_pid:
                continue
            if int(window.get("kCGWindowLayer", 0)) != 0:
                continue  # menu bar, Dock, overlays: not an app window
            bounds = window.get("kCGWindowBounds") or {}
            if (
                float(bounds.get("Width", 0)) < _MIN_EVIDENCE_WINDOW_PX
                or float(bounds.get("Height", 0)) < _MIN_EVIDENCE_WINDOW_PX
            ):
                continue
            foreign += 1
            if str(window.get("kCGWindowName") or "").strip():
                titled += 1
    except (AttributeError, TypeError, ValueError):
        log.debug("The window list had an unexpected shape.", exc_info=True)
        return None
    return foreign, titled


# A seam: tests replace it; production resolves the Quartz reader above.
_window_evidence: Callable[[], tuple[int, int] | None] = _default_window_evidence
# A seam for the verdict cache's clock.
_monotonic: Callable[[], float] = time.monotonic
# One slot: the reader that produced the verdict (a replaced seam never reuses a
# stale one), when, and what it saw. Plain tuple assignment is atomic, so two
# worker threads that race only read the window list twice.
_evidence_cache: dict[str, tuple[Callable[[], tuple[int, int] | None], float, object]] = {}


def reset_window_evidence_cache() -> None:
    """Forget the cached window-list verdict (tests, and a deliberate re-check)."""
    _evidence_cache.clear()
    _failed_use_reported.clear()


def _grant_unusable(max_age_s: float) -> bool:
    """``True`` when other apps' windows are on screen and none has a readable title.

    That is the window-list signature of a Screen Recording grant the OS does not
    honour (a stale entry after a re-sign, a revoke the preflight has not
    reported). The ONE window-list read is shared by every capture of the
    process: a "usable" or "no evidence" verdict is reused for ``max_age_s``. An
    unusable verdict is never cached, so a grant the user fixes is believed on the
    next capture. UNVERIFIED on a real Mac. Known false alarm: a desktop whose
    only big windows carry no title (a full-screen game or kiosk window) reads as
    unusable while the grant works; the refusal then names the permission.
    """
    reader = _window_evidence
    now = _monotonic()
    cached = _evidence_cache.get("verdict")
    if cached is not None and cached[0] is reader and 0.0 <= now - cached[1] <= max_age_s:
        evidence = cached[2]
    else:
        evidence = reader()
        _evidence_cache["verdict"] = (reader, now, evidence)
    unusable = (
        isinstance(evidence, tuple) and len(evidence) == 2 and evidence[0] > 0 and evidence[1] == 0
    )
    if unusable:
        _evidence_cache.pop("verdict", None)
    return unusable


def _grant_proven() -> bool:
    """``True`` when the last window-list verdict READ a title of another app's window.

    That is positive proof the grant works right now (a missing grant hides those
    titles); "no windows" or "no evidence" proves nothing. Reads the cached verdict
    :func:`_grant_unusable` just produced, so it costs no second window-list read.
    """
    cached = _evidence_cache.get("verdict")
    if cached is None or cached[0] is not _window_evidence:
        return False
    evidence = cached[2]
    return isinstance(evidence, tuple) and len(evidence) == 2 and evidence[1] > 0


def _unusable_grant_refusal(feature: str, gate: Any, *, interactive: bool) -> ScreenCaptureRefused:
    """Report a REAL failed use to the service and build the honest refusal.

    The state reads GRANTED yet the window list says the grant is not honoured: the
    one case where "quit and reopen" is honest advice (the grant may only apply to
    a new process, community-observed and UNVERIFIED). A user-started capture tells
    the service, which opens a ``restart_hint`` episode (the card names the restart
    and nothing restarts by itself); a background consumer stays quiet, the service
    only opens episodes with the user origin for this call. The frame is refused
    either way. A gate without the call (a scripted stub) gets the plain refusal.
    """
    report = getattr(gate, "report_failed_use", None)
    if interactive and callable(report):
        result = report(PermissionId.SCREEN_RECORDING, feature=feature)
        _failed_use_reported.add(feature)
        if not getattr(result, "granted", True) and getattr(result, "reason", ""):
            return _refusal_from_result(result)
    return refusal_for_state(PermissionState.NOT_GRANTED)


def _clear_failed_use(gate: Any) -> None:
    """A verified-real frame ends the ``restart_hint`` episode a failed use opened.

    The grant is process-wide, so ANY feature's verified-real frame proves it works
    again, whichever feature reported the failure (the episode is per permission, not
    per feature). The service has no hook that says "the attempt that failed works
    now"; the one call that drops a slot's restart hint is ``note_reset`` (it also
    forgets the per-process ask cooldown, harmless while the grant is live). The next
    watcher pass then closes the episode as granted, so the card does not nag a user
    whose capture works again. Does nothing unless some feature reported a failure.
    """
    if not _failed_use_reported:
        return
    _failed_use_reported.clear()
    reset = getattr(gate, "note_reset", None)
    if not callable(reset):
        return
    try:
        reset(PermissionId.SCREEN_RECORDING)
    except Exception:  # noqa: BLE001 - tidying a card never turns a real frame into a failure
        log.debug("Ending the screen-recording restart hint failed.", exc_info=True)


def verify_frame_is_real(
    size: tuple[int, int],
    pixels: Any,
    *,
    feature: str,
    bytes_per_pixel: int = 3,
    interactive: bool = True,
    gate: Any | None = None,
) -> None:
    """Refuse a frame the permission state cannot explain: never a wallpaper success.

    Runs at the capture site AFTER a grab (off macOS the state is NOT_REQUIRED and
    nothing else happens). The entry gate ran BEFORE the grab, and the grant can
    go away between the two (the stability loop runs up to 1.2 s, a native capture
    timeout takes 3 s), so:

    1. the cached grant is dropped (``invalidate``) and the state is read fresh,
       once per frame (one preflight; the window-title oracle only when that says
       "not granted"). A state that is not granted refuses the frame, blank or
       not, through ``ensure`` so the permission episode is opened;
    2. while the state says GRANTED, the window list is consulted (cached for a few
       seconds, younger for a flat frame): other apps' windows on screen with no
       readable title means the grant is not usable, so the frame refuses with
       honest text. A photographic wallpaper is not a flat frame, which is why this
       does not wait for blankness. A readable title, no windows at all or no
       evidence means the frame passes (a flat one is then a genuinely blank
       screen).

    ``interactive`` is False for a background consumer: the episode is then
    recorded with the ``background`` origin (inline status, no card) and no native
    request is made. Blocking: a worker thread, never the event loop.
    """
    gate = gate if gate is not None else permission_gate()
    invalidate = getattr(gate, "invalidate", None)
    if callable(invalidate):
        invalidate(PermissionId.SCREEN_RECORDING)
    state = screen_recording_state(gate)
    if state not in _READY:
        result = gate.ensure(
            PermissionId.SCREEN_RECORDING,
            feature=feature,
            interactive=interactive,
            wait_s=0.0,
        )
        if result.granted:
            # The state flipped back between the two reads: the frame was taken
            # without a usable grant, so it is not trustworthy. The next one will be.
            log.info("A capture finished while the grant was missing; refusing this frame.")
            raise refusal_for_state(PermissionState.NOT_GRANTED)
        raise _refusal_from_result(result)
    if state is not PermissionState.GRANTED:
        return  # NOT_REQUIRED: this platform has no Screen Recording trap

    blank = frame_is_blank(size, pixels, bytes_per_pixel=bytes_per_pixel)
    if _grant_unusable(_BLANK_EVIDENCE_TTL_S if blank else _EVIDENCE_TTL_S):
        log.warning(
            "The Screen Recording grant reads as granted but other apps' windows are "
            "on screen and none has a readable title. The grant is probably not usable "
            "(unverified); refusing the frame."
        )
        raise _unusable_grant_refusal(feature, gate, interactive=interactive)
    if _grant_proven():
        _clear_failed_use(gate)
    if blank:
        log.debug("A blank capture is accepted: the grant works and the screen is blank.")


__all__ = [
    "ScreenCaptureRefused",
    "frame_is_blank",
    "permission_gate",
    "refusal_for_state",
    "refusal_pending",
    "require_screen_recording",
    "require_screen_recording_async",
    "reset_window_evidence_cache",
    "screen_recording_blocked",
    "screen_recording_state",
    "state_allows_capture",
    "verify_frame_is_real",
]
