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
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Max |cursor - target| in input units after a move before we refuse to act.
#: 2 units absorbs sub-pixel rounding; a real mapping bug is off by 10s-100s.
LANDING_TOLERANCE = 2


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
    the cursor position is unreadable the move is trusted (``landed=None``) —
    refusing to act on an unreadable-but-working host would brick CU there.
    """
    last: tuple[int, int] | None = None
    for attempt in (1, 2):
        actuator.move(int(x), int(y))
        last = actuator.cursor_pos()
        if last is None:
            return ActResult(
                ok=True,
                detail="cursor position unreadable — move unverified",
                landed=None,
            )
        if abs(last[0] - int(x)) <= tolerance and abs(last[1] - int(y)) <= tolerance:
            return ActResult(ok=True, detail=f"landed at {last}", landed=last)
        logger.debug(
            "[cu] move to (%d,%d) landed at %s (attempt %d)", x, y, last, attempt,
        )
    return ActResult(
        ok=False,
        detail=(
            f"cursor was positioned to ({x},{y}) but landed at {last} — "
            "coordinate-space mismatch (DPI/monitor mapping); refusing to act"
        ),
        landed=last,
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


def get_actuator() -> Actuator:
    """Resolve the platform input backend by runtime capability.

    Raises :class:`ActuationUnavailable` with an actionable English message
    when the host cannot receive synthetic input. Never returns a backend
    that is known-broken for the session type.
    """
    if os.name == "nt":
        from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

        return WindowsActuator()

    from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

    if sys.platform != "darwin":
        if is_wayland():
            raise ActuationUnavailable(_WAYLAND_MSG)
        if not display_present():
            raise ActuationUnavailable(_HEADLESS_MSG)

    from jarvis.cu.actuate.posix import PosixActuator  # noqa: PLC0415

    try:
        return PosixActuator()
    except ActuationUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — import/init of pynput/pyautogui
        raise ActuationUnavailable(f"{_NO_BACKEND_MSG} ({exc})") from exc
