"""Virtual-cursor core — display-independent logic + process-wide singleton.

The "Jarvis virtual mouse" makes Computer-Use visible: when the agent moves
or clicks, the real OS cursor glides to the target and a styled overlay
(gold halo + click pulse) shows exactly where the action lands — the same
"you can watch what it does" affordance as the Claude-in-Chrome extension.

This module holds only the parts that need no display, so they can be
unit-tested on a headless CI box and on a €5 VPS:

* :func:`glide_points` — the eased path the real cursor follows.
* :class:`NullVirtualCursor` — a no-op that absorbs every call, so a missing
  display (headless VPS, cloud-first doctrine) can never break a real click.
* :func:`get_virtual_cursor` / :func:`set_virtual_cursor` — the singleton
  accessor pattern, so the low-level mouse tools can fire an indicator from
  anywhere without threading the EventBus through ``ExecutionContext``.

The actual Tk window that renders the halo + pulse lives in
``ui/orb/virtual_cursor_window.py`` and registers itself here via
:func:`set_virtual_cursor` when the desktop UI boots.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "VirtualCursor",
    "NullVirtualCursor",
    "glide_points",
    "glide_cursor",
    "pulse_state",
    "get_virtual_cursor",
    "set_virtual_cursor",
    "virtual_cursor_enabled",
]


def _ease_in_out_cubic(t: float) -> float:
    """Smooth acceleration then deceleration — feels like a real hand move."""
    if t < 0.5:
        return 4.0 * t * t * t
    f = (2.0 * t) - 2.0
    return 0.5 * f * f * f + 1.0


def glide_points(
    x0: int, y0: int, x1: int, y1: int, *, steps: int
) -> list[tuple[int, int]]:
    """Eased integer path from ``(x0, y0)`` to ``(x1, y1)``.

    The first point is the start and the last point is always *exactly* the
    target, so the real OS click that follows the glide never misses by a
    rounding pixel. ``steps <= 1`` collapses to ``[(x1, y1)]`` — there is
    nothing to animate, so we land directly on the target.
    """
    if steps <= 1:
        return [(int(x1), int(y1))]

    pts: list[tuple[int, int]] = []
    last = steps - 1
    for i in range(steps):
        if i == last:
            pts.append((int(x1), int(y1)))
            break
        t = _ease_in_out_cubic(i / last)
        x = round(x0 + (x1 - x0) * t)
        y = round(y0 + (y1 - y0) * t)
        pts.append((int(x), int(y)))
    return pts


def glide_cursor(
    x1: int,
    y1: int,
    *,
    start: tuple[int, int] | None = None,
    get_pos: Callable[[], tuple[int, int]] | None = None,
    duration_s: float = 0.22,
    hz: int = 60,
    set_pos: Callable[[int, int], None],
    notify: Callable[[int, int], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Move the real OS cursor along an eased path to ``(x1, y1)``.

    ``set_pos`` is the only mandatory side effect (e.g. ``SetCursorPos``);
    ``notify`` mirrors each intermediate point to the overlay so the gold
    highlight tracks the real cursor frame-for-frame. The number of frames is
    ``round(duration_s * hz)`` — a ``duration_s`` of ``0`` collapses to a
    single landing on the target (instant, no animation).

    A broken ``notify`` (e.g. the overlay thread died) is swallowed: the real
    cursor must always reach its target so the subsequent click lands.
    """
    if start is None:
        if get_pos is not None:
            try:
                start = get_pos()
            except Exception:  # noqa: BLE001 — fall back to a direct landing
                start = (int(x1), int(y1))
        else:
            start = (int(x1), int(y1))

    steps = max(1, round(duration_s * hz))
    path = glide_points(start[0], start[1], x1, y1, steps=steps)

    last = len(path) - 1
    for i, (px, py) in enumerate(path):
        set_pos(px, py)
        if notify is not None:
            try:
                notify(px, py)
            except Exception:  # noqa: BLE001 — overlay must never block a click
                pass
        if i != last:
            sleep(1.0 / float(hz))


def pulse_state(
    elapsed_ms: float, *, duration_ms: float, max_radius: float
) -> tuple[float, float] | None:
    """Click-pulse ring geometry at ``elapsed_ms`` into its lifetime.

    Returns ``(radius, alpha)`` — radius eases outward to ``max_radius`` while
    alpha fades linearly ``1.0 -> 0.0`` — or ``None`` once the pulse has
    expired (``elapsed_ms > duration_ms``), which tells the renderer to drop it.
    """
    if elapsed_ms > duration_ms:
        return None
    if duration_ms <= 0:
        return (max_radius, 0.0)
    t = max(0.0, min(1.0, elapsed_ms / duration_ms))
    # Ease-out (decelerating) expansion reads as a "ping".
    radius = max_radius * (1.0 - (1.0 - t) * (1.0 - t))
    alpha = 1.0 - t
    return (radius, alpha)


@runtime_checkable
class VirtualCursor(Protocol):
    """Indicator surface the low-level mouse tools talk to.

    All methods are called from arbitrary worker threads (the click tools run
    under ``asyncio.to_thread``); implementations must marshal to their own UI
    thread and must never raise back into the caller.
    """

    def show_move(self, x: int, y: int, *, monitor: int = 0) -> None: ...

    def show_click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
        monitor: int = 0,
    ) -> None: ...

    def show_path_point(self, x: int, y: int) -> None: ...

    def clear(self) -> None: ...

    def shutdown(self) -> None: ...


class NullVirtualCursor:
    """No-op cursor. Active whenever no display-backed cursor is installed.

    Every method silently absorbs its call. This is what keeps the feature
    safe on a headless VPS: ``get_virtual_cursor().show_click(...)`` is always
    callable and always harmless.
    """

    def show_move(self, x: int, y: int, *, monitor: int = 0) -> None:
        return None

    def show_click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
        monitor: int = 0,
    ) -> None:
        return None

    def show_path_point(self, x: int, y: int) -> None:
        return None

    def clear(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


_lock = threading.Lock()
_active: VirtualCursor | None = None
_NULL = NullVirtualCursor()


def set_virtual_cursor(cursor: VirtualCursor | None) -> None:
    """Install (or clear with ``None``) the process-wide cursor indicator."""
    global _active
    with _lock:
        _active = cursor


def get_virtual_cursor() -> VirtualCursor:
    """Return the active cursor, or a shared :class:`NullVirtualCursor`."""
    with _lock:
        return _active if _active is not None else _NULL


def virtual_cursor_enabled(cfg: Any) -> bool:
    """Read ``[computer_use].show_virtual_cursor`` (default ``True``).

    Accepts either a plain config dict or an object exposing a
    ``computer_use`` attribute, so it works both with the raw TOML mapping
    and with the parsed :class:`JarvisConfig`.
    """
    section: Any = None
    if isinstance(cfg, dict):
        section = cfg.get("computer_use")
    else:
        section = getattr(cfg, "computer_use", None)

    if section is None:
        return True

    if isinstance(section, dict):
        value = section.get("show_virtual_cursor", True)
    else:
        value = getattr(section, "show_virtual_cursor", True)
    return bool(value)
