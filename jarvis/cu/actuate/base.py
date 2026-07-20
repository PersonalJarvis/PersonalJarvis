"""Actuator protocol, landed-position verification, backend selection.

Design contract (CU v2): a silent miss is worse than a loud failure. Every
pointer action therefore goes through :func:`verified_move` — position the
cursor, read it back, and refuse to press the button when the cursor did not
land within :data:`LANDING_TOLERANCE` of the target. A DPI mis-mapping or a
clamped multi-monitor move then surfaces as a diagnosable error string
instead of a click on the wrong control.

Backend selection is a runtime capability probe (never an OS allowlist in
callers): Windows -> native SendInput; macOS / Linux-X11 -> pynput (pyautogui
fallback); Wayland / headless -> :class:`ActuationUnavailable` with an
actionable message.
"""
from __future__ import annotations

import abc
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Max |cursor - target| in input units after a move before we refuse to act.
#: 2 units absorbs sub-pixel rounding; a real mapping bug is off by 10s-100s.
LANDING_TOLERANCE = 2

# Platform-neutral vocabulary accepted at the tool boundary. Backends may map
# aliases differently (``cmd`` is Command on macOS and Super on X11), but the
# shared validation must not reject a key before the selected backend sees it.
_NAMED_KEYS = frozenset({
    "ctrl", "control", "shift", "alt", "option", "menu",
    "win", "windows", "lwin", "rwin", "cmd", "command", "meta", "super",
    "esc", "escape", "enter", "return", "tab", "space", "spacebar",
    "backspace", "back", "delete", "del", "insert", "ins", "home", "end",
    "pageup", "pgup", "pagedown", "pgdn", "left", "up", "right", "down",
    "capslock",
    *{f"f{i}" for i in range(1, 13)},
    *{f"numpad{i}" for i in range(10)},
    "multiply", "add", "subtract", "decimal", "divide",
})


def is_known_key_name(key: str) -> bool:
    """Whether ``key`` belongs to the cross-platform Computer-Use vocabulary."""
    normalized = str(key).strip().lower()
    return bool(normalized) and (
        normalized in _NAMED_KEYS
        or (len(normalized) == 1 and normalized.isprintable())
    )


class ActuationUnavailable(RuntimeError):
    """No input backend can act on this host (headless, Wayland, deps absent).

    The message is user-actionable and English (artifact rule); the spoken
    readback localizes separately.
    """


@dataclass(frozen=True)
class ActResult:
    """Outcome of one actuation primitive."""

    ok: bool
    detail: str = ""
    landed: tuple[int, int] | None = None


class Actuator(abc.ABC):
    """Platform input backend. Coordinates are input units on the virtual
    desktop (physical px on Windows/X11, points on macOS) — exactly the space
    :class:`jarvis.cu.geometry.CoordinateMapper` maps into."""

    name: str = "abstract"

    @abc.abstractmethod
    def cursor_pos(self) -> tuple[int, int] | None:
        """Current cursor position, or ``None`` when unreadable."""

    @abc.abstractmethod
    def move(self, x: int, y: int) -> None:
        """Position the cursor at ``(x, y)`` (no button activity)."""

    @abc.abstractmethod
    def click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
    ) -> None:
        """Press+release ``button`` at ``(x, y)``."""

    @abc.abstractmethod
    def drag(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        """Press at start, move to end, release."""

    @abc.abstractmethod
    def scroll(
        self, direction: str, notches: int,
        *, x: int | None = None, y: int | None = None,
    ) -> None:
        """Wheel-scroll ``up/down/left/right``; optionally target ``(x, y)``."""

    @abc.abstractmethod
    def key_combo(self, keys: list[str]) -> None:
        """Press a key combination (modifiers first), release in reverse."""

    @abc.abstractmethod
    def type_text(self, text: str, *, delay_s: float = 0.02) -> None:
        """Type Unicode text into the focused control."""


def verified_move(
    actuator: Actuator, x: int, y: int, *, tolerance: int = LANDING_TOLERANCE,
) -> ActResult:
    """Move the cursor to ``(x, y)`` and verify it landed.

    One retry: transient focus/animation can eat the first positioning. When
    an unreadable cursor is retried once and then refused: without read-back,
    Computer-Use cannot prove where the following button event would land.

    The tiny sleep between inject and read is load-bearing: injected input
    passes through the OS input queue, so an immediate ``cursor_pos`` read
    can still see the PRE-move position under desktop load (live rig run
    2026-07-02) — a false "coordinate-space mismatch".
    """
    import time  # noqa: PLC0415

    # A virtual desktop is a bounding rectangle, but real displays can form an
    # L-shape with dead gaps. Never move/click into such a gap when live display
    # geometry is available; an empty list means headless/probe-unavailable and
    # remains the backend capability check's responsibility.
    try:
        from jarvis.cu.geometry import (  # noqa: PLC0415
            list_monitors,
            point_on_physical_display,
        )

        monitors = list_monitors()
        if monitors and not point_on_physical_display(x, y, monitors):
            return ActResult(
                ok=False,
                detail=(
                    f"target ({x},{y}) is outside every physical display — "
                    "refusing to act in a virtual-desktop gap"
                ),
                landed=None,
            )
    except Exception:  # noqa: BLE001 — actuation backend remains authoritative
        logger.debug("physical-display target validation failed", exc_info=True)

    last: tuple[int, int] | None = None
    for attempt in (1, 2):
        actuator.move(int(x), int(y))
        time.sleep(0.02)
        last = actuator.cursor_pos()
        if last is None:
            logger.debug(
                "[cu] cursor unreadable after move to (%d,%d) (attempt %d)",
                x,
                y,
                attempt,
            )
            continue
        if abs(last[0] - int(x)) <= tolerance and abs(last[1] - int(y)) <= tolerance:
            return ActResult(ok=True, detail=f"landed at {last}", landed=last)
        logger.debug(
            "[cu] move to (%d,%d) landed at %s (attempt %d)", x, y, last, attempt,
        )
    if last is None:
        return ActResult(
            ok=False,
            detail=(
                "cursor position remained unreadable after two move attempts; "
                "refusing to emit a pointer event"
            ),
            landed=None,
        )
    return ActResult(
        ok=False,
        detail=(
            f"cursor was positioned to ({x},{y}) but landed at {last} — "
            "coordinate-space mismatch (DPI/monitor mapping); refusing to act"
        ),
        landed=last,
    )


def verified_click(
    actuator: Actuator,
    x: int,
    y: int,
    *,
    button: str = "left",
    double: bool = False,
    pre_action_check: Callable[[], bool] | None = None,
) -> ActResult:
    """Verify pointer landing before emitting any button-down event."""
    landing = verified_move(actuator, x, y)
    if not landing.ok:
        return landing
    click_at_cursor = getattr(actuator, "click_at_cursor", None)
    if not callable(click_at_cursor):
        return ActResult(
            ok=False,
            detail=(
                "input backend lacks a safe at-cursor click primitive; "
                "refusing an unverified second move"
            ),
            landed=landing.landed,
        )
    if pre_action_check is not None and not pre_action_check():
        return ActResult(
            ok=False,
            detail=(
                "foreground window changed during cursor movement; refusing "
                "to press a mouse button"
            ),
            landed=landing.landed,
        )
    click_at_cursor(
        button=button,
        double=double,
        expected=(int(x), int(y)),
    )
    return ActResult(ok=True, detail=landing.detail, landed=landing.landed)


def verified_drag(
    actuator: Actuator,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    duration_s: float = 0.4,
    tolerance: int = LANDING_TOLERANCE,
    pre_action_check: Callable[[], bool] | None = None,
) -> ActResult:
    """Refuse a drag unless its start lands, then verify its released endpoint."""
    # Validate the destination before button-down. A virtual desktop's bounding
    # rectangle can contain dead gaps between L-shaped displays; discovering
    # that only after dragging would already have acted at an unsafe point.
    try:
        from jarvis.cu.geometry import (  # noqa: PLC0415
            list_monitors,
            point_on_physical_display,
            segment_on_physical_displays,
        )

        monitors = list_monitors()
        if monitors and not point_on_physical_display(x2, y2, monitors):
            return ActResult(
                ok=False,
                detail=(
                    f"drag destination ({x2},{y2}) is outside every physical "
                    "display — refusing to drag into a virtual-desktop gap"
                ),
                landed=None,
            )
        if monitors and not segment_on_physical_displays(
            x1, y1, x2, y2, monitors,
        ):
            return ActResult(
                ok=False,
                detail=(
                    "drag path crosses a virtual-desktop gap; refusing to "
                    "hold a mouse button across disconnected displays"
                ),
                landed=None,
            )
    except Exception:  # noqa: BLE001 — backend validation remains authoritative
        logger.debug("drag destination display validation failed", exc_info=True)

    start = verified_move(actuator, x1, y1, tolerance=tolerance)
    if not start.ok:
        return start
    drag_from_cursor = getattr(actuator, "drag_from_cursor", None)
    if not callable(drag_from_cursor):
        return ActResult(
            ok=False,
            detail=(
                "input backend lacks a safe at-cursor drag primitive; "
                "refusing an unverified second start move"
            ),
            landed=start.landed,
        )
    if pre_action_check is not None and not pre_action_check():
        return ActResult(
            ok=False,
            detail=(
                "foreground window changed during drag positioning; refusing "
                "to press a mouse button"
            ),
            landed=start.landed,
        )
    drag_from_cursor(
        x1, y1, x2, y2, duration_s=max(0.0, duration_s),
    )
    import time  # noqa: PLC0415

    end: tuple[int, int] | None = None
    for _attempt in (1, 2):
        # Quartz/SendInput posting is queued just like a plain move. Give the
        # release endpoint a bounded chance to become visible before judging.
        time.sleep(0.02)
        end = actuator.cursor_pos()
        if end is not None and (
            abs(end[0] - int(x2)) <= tolerance
            and abs(end[1] - int(y2)) <= tolerance
        ):
            return ActResult(ok=True, detail=f"drag ended at {end}", landed=end)
    if end is None:
        return ActResult(
            ok=False,
            detail="drag endpoint is unreadable, so its landing cannot be verified",
            landed=None,
        )
    return ActResult(
        ok=False,
        detail=(
            f"drag to ({x2},{y2}) landed at {end} — coordinate-space mismatch "
            "(DPI/monitor mapping)"
        ),
        landed=end,
    )


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_WAYLAND_MSG = (
    "Cannot control mouse/keyboard on a Wayland session: Wayland blocks "
    "synthetic input for security. Log into an X11 session, or run Jarvis on "
    "a host with X11/Windows/macOS."
)
_HEADLESS_MSG = (
    "Cannot control mouse/keyboard: no display is present on this host "
    "(headless). Computer-Use needs a desktop session."
)
_NO_BACKEND_MSG = (
    "Cannot control mouse/keyboard: no input backend is installed. "
    "Install the desktop extras (pip install 'personal-jarvis[desktop]') "
    "to get pynput/pyautogui."
)


def _require_macos_input_permissions() -> None:
    """Fail closed unless macOS currently permits synthetic input.

    TCC grants can be revoked while Jarvis is running, so this deliberately
    creates a fresh permission port and probes both grants for every action.
    No result is cached in the actuator layer.
    """
    if sys.platform != "darwin":
        return

    from jarvis.platform.permissions import (  # noqa: PLC0415
        PermissionId,
        PermissionState,
        get_system_permission_port,
    )

    port = get_system_permission_port()
    requirements = (
        (PermissionId.ACCESSIBILITY, "Accessibility"),
        (PermissionId.EVENT_POSTING, "Input Control"),
    )
    missing: list[str] = []
    for permission_id, label in requirements:
        if not port.runtime_access_granted(permission_id):
            state = port.state(permission_id)
            detail = (
                state.value
                if state is not PermissionState.GRANTED
                else "grant belongs to an unstable app identity or needs restart"
            )
            missing.append(f"{label} ({detail})")
    if missing:
        joined = ", ".join(missing)
        raise ActuationUnavailable(
            "Cannot control the mouse or keyboard on macOS because these "
            f"permissions are not granted: {joined}. Open Personal Jarvis "
            "> Settings > Permissions (or System Settings > Privacy & "
            "Security), grant the listed access, then retry."
        )


#: Process-wide backend instance. Building a PosixActuator constructs pynput
#: Controllers (keycode tables, layout snapshot) — doing that once per ACTION
#: was pure per-click overhead. The desktop lock serializes missions, and the
#: backends hold no per-action state, so one instance serves the process.
_ACTUATOR_CACHE: Actuator | None = None


def get_actuator() -> Actuator:
    """Resolve the platform input backend by runtime capability.

    Raises :class:`ActuationUnavailable` with an actionable English message
    when the host cannot receive synthetic input. Never returns a backend
    that is known-broken for the session type. The permission and session
    probes run on EVERY call (revocation must fail closed); only the backend
    construction is reused.
    """
    global _ACTUATOR_CACHE
    _require_macos_input_permissions()

    if os.name == "nt":
        from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

        if not isinstance(_ACTUATOR_CACHE, WindowsActuator):
            _ACTUATOR_CACHE = WindowsActuator()
        return _ACTUATOR_CACHE

    from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

    if sys.platform != "darwin":
        if is_wayland():
            raise ActuationUnavailable(_WAYLAND_MSG)
        if not display_present():
            raise ActuationUnavailable(_HEADLESS_MSG)

    from jarvis.cu.actuate.posix import PosixActuator  # noqa: PLC0415

    if isinstance(_ACTUATOR_CACHE, PosixActuator):
        return _ACTUATOR_CACHE
    try:
        actuator = PosixActuator()
    except ActuationUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — import/init of pynput/pyautogui
        raise ActuationUnavailable(f"{_NO_BACKEND_MSG} ({exc})") from exc
    # Only the full pynput backend is retained: a pyautogui-fallback instance
    # (e.g. the keyboard-layout cache was not primed yet at first use) must
    # keep retrying pynput on later actions instead of being frozen in.
    if actuator.name == "posix-pynput":
        _ACTUATOR_CACHE = actuator
    return actuator
