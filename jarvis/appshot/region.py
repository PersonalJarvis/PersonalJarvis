"""Select an area of the screen for a region appshot.

:func:`pick_region` starts the one-shot picker sidecar
(:mod:`jarvis.appshot.picker`), waits for the user to drag a rectangle and
returns it; :func:`selection_to_bbox` turns that into capture coordinates.

Two coordinate worlds meet here:

- **capture space** — what Screen Context grabs in: physical pixels on
  Windows (per-monitor DPI aware), points on macOS, root-window pixels on X11.
  ``monitors`` are mss-shaped dicts (``[0]`` = the virtual desktop).
- **Qt space** — the picker's logical pixels, one ``QScreen`` per monitor
  with its own ``devicePixelRatio``.

A selection never crosses between them as absolute coordinates. The picker
reports *which screen* (its Qt geometry and scale) and the selection as
*fractions of that screen*; :func:`match_monitor` finds the same screen in
capture space. Fractions survive every mixed-DPI layout, because a screen's
content scales uniformly within itself even where the virtual-desktop origins
of different screens disagree between the two worlds.

Everything above :func:`picker_capability` is pure and imports nothing
heavy; the picker itself is the only place PySide6 is loaded.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: A drag smaller than this (logical px, either side) is a click, not a pick.
MIN_SELECTION_PX = 4

#: The picker gives up on its own after this long without a selection.
PICK_TIMEOUT_S = 120.0
#: Grace for the picker to exit (and its overlay to leave the glass).
_EXIT_GRACE_S = 2.0
#: Extra settle time so the compositor has repainted without the dim layer.
_SETTLE_S = 0.08

Rect = tuple[int, int, int, int]
FRect = tuple[float, float, float, float]

# Hint pill over the screens — ALL supported output locales, resolved through
# the one turn-language resolver (a phrase table always carries every locale).
_HINTS: dict[str, str] = {
    "de": "Ziehe über den Bereich, den du zeigen willst · Esc bricht ab",  # i18n-allow
    "en": "Drag over the area you want to show · Esc to cancel",
    "es": "Arrastra sobre la zona que quieres mostrar · Esc para cancelar",  # i18n-allow
}


@dataclass(frozen=True, slots=True)
class Selection:
    """A finished selection: which Qt screen, which part of it."""

    screen: dict[str, float]
    rect: FRect


class RegionUnavailable(RuntimeError):
    """No area can be selected on this host; the message says why."""


# --------------------------------------------------------------------------
# Pure geometry
# --------------------------------------------------------------------------


def selection_fractions(
    x0: float, y0: float, x1: float, y1: float, width: float, height: float
) -> FRect | None:
    """A drag from ``(x0, y0)`` to ``(x1, y1)`` on a ``width × height`` screen.

    Returns ``(fx, fy, fw, fh)`` clamped to the screen, or ``None`` when the
    drag is too small to be a selection. Works for a drag in any direction.
    """
    if width <= 0 or height <= 0:
        return None
    left = max(0.0, min(float(x0), float(x1)))
    top = max(0.0, min(float(y0), float(y1)))
    right = min(float(width), max(float(x0), float(x1)))
    bottom = min(float(height), max(float(y0), float(y1)))
    if right - left < MIN_SELECTION_PX or bottom - top < MIN_SELECTION_PX:
        return None
    return (left / width, top / height, (right - left) / width, (bottom - top) / height)


def screen_monitor_score(screen: dict, monitor: dict) -> float:
    """How far a Qt screen is from a capture-space monitor (0 = identical).

    Each plausible reading of the Qt geometry is tried — logical as-is (macOS
    points, X11 at scale 1), logical origin with physical size (Qt 6 on
    Windows), and fully physical — and the closest one counts.
    """
    x = float(screen.get("x", 0))
    y = float(screen.get("y", 0))
    w = float(screen.get("w", 0))
    h = float(screen.get("h", 0))
    dpr = float(screen.get("dpr", 1.0) or 1.0)
    ml, mt = float(monitor.get("left", 0)), float(monitor.get("top", 0))
    mw, mh = float(monitor.get("width", 0)), float(monitor.get("height", 0))
    best = float("inf")
    for rx, ry, rw, rh in (
        (x, y, w, h),
        (x, y, w * dpr, h * dpr),
        (x * dpr, y * dpr, w * dpr, h * dpr),
    ):
        # Size mismatches weigh more than origin mismatches: origins are the
        # part mixed-DPI layouts disagree about.
        score = 2.0 * (abs(rw - mw) + abs(rh - mh)) + abs(rx - ml) + abs(ry - mt)
        best = min(best, score)
    return best


def match_monitor(screen: dict, monitors: list[dict]) -> dict | None:
    """The capture-space monitor that is the picker's Qt ``screen``."""
    physical = [m for m in (monitors[1:] if len(monitors) > 1 else monitors) if isinstance(m, dict)]
    if not physical:
        return None
    return min(physical, key=lambda mon: screen_monitor_score(screen, mon))


def fraction_to_bbox(monitor: dict, frac: FRect | list[float]) -> Rect:
    """Fractions of ``monitor`` → ``(left, top, width, height)`` in capture space."""
    ml, mt = int(monitor.get("left", 0)), int(monitor.get("top", 0))
    mw, mh = max(1, int(monitor.get("width", 1))), max(1, int(monitor.get("height", 1)))
    fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in frac)
    x0 = ml + round(fx * mw)
    y0 = mt + round(fy * mh)
    x1 = ml + round(min(1.0, fx + fw) * mw)
    y1 = mt + round(min(1.0, fy + fh) * mh)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def selection_to_bbox(selection: Selection, monitors: list[dict]) -> Rect | None:
    """The selection in capture coordinates, or ``None`` without monitors."""
    monitor = match_monitor(selection.screen, monitors)
    if monitor is None:
        return None
    return fraction_to_bbox(monitor, selection.rect)


def parse_selection(payload: dict[str, Any]) -> Selection | None:
    """A picker ``selection`` event → :class:`Selection` (``None`` = cancelled)."""
    if payload.get("cancelled"):
        return None
    screen, rect = payload.get("screen"), payload.get("rect")
    if not isinstance(screen, dict) or not isinstance(rect, list) or len(rect) != 4:
        return None
    try:
        info = {key: float(screen[key]) for key in ("x", "y", "w", "h", "dpr")}
        frac = tuple(max(0.0, min(1.0, float(v))) for v in rect)
    except (KeyError, TypeError, ValueError):
        return None
    if frac[2] <= 0.0 or frac[3] <= 0.0:
        return None
    return Selection(screen=info, rect=frac)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The picker process
# --------------------------------------------------------------------------


def picker_capability() -> tuple[bool, str]:
    """Whether an area can be selected on this host, and why not."""
    try:
        from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

        if not display_present():
            return False, "there is no screen on this computer"
        if is_wayland():
            return False, "Wayland does not let apps draw a selection over other windows"
    except Exception:  # noqa: BLE001 - probes missing: let the picker itself decide
        log.debug("appshot: platform probes unavailable", exc_info=True)
    if importlib.util.find_spec("PySide6") is None:
        return False, "the selection overlay needs PySide6 (the [desktop] extra)"
    return True, ""


def _hint() -> str:
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415
        from jarvis.core.turn_language import (  # noqa: PLC0415
            DEFAULT_LOCALE,
            resolve_output_language,
        )

        pin = str(getattr(load_config().brain, "reply_language", "") or "")
        language = resolve_output_language(pin, "", "", default=DEFAULT_LOCALE)
        return _HINTS.get(language, _HINTS["en"])
    except Exception:  # noqa: BLE001 - the hint is decoration; English is honest
        log.debug("appshot: picker hint language unresolved", exc_info=True)
        return _HINTS["en"]


def _spawn(hint: str) -> subprocess.Popen[str]:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    return subprocess.Popen(
        [sys.executable, "-m", "jarvis.appshot.picker", "--hint", hint],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        env=os.environ.copy(),
    )


def _read_result(proc: subprocess.Popen[str]) -> dict[str, Any] | None:
    """Block until the picker reports its selection; ``None`` on EOF."""
    from jarvis.appshot import picker as wire  # noqa: PLC0415

    assert proc.stdout is not None
    for line in proc.stdout:
        payload = wire.decode(line)
        if payload is not None and payload.get("event") == wire.EVENT_SELECTION:
            return payload
    return None


def _cancel(proc: subprocess.Popen[str]) -> None:
    from jarvis.appshot import picker as wire  # noqa: PLC0415

    with contextlib.suppress(Exception):  # a closed pipe means it already ended
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.write(wire.encode({"cmd": wire.CMD_CANCEL}))
            proc.stdin.flush()


def _reap(proc: subprocess.Popen[str]) -> int | None:
    try:
        proc.wait(timeout=_EXIT_GRACE_S)
    except subprocess.TimeoutExpired:
        log.info("appshot: area picker did not exit in time; stopping it")
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=1.0)
    for pipe in (proc.stdin, proc.stdout):
        if pipe is not None:
            with contextlib.suppress(Exception):
                pipe.close()
    return proc.returncode


async def _escape_cancels(proc: subprocess.Popen[str]) -> None:
    """A global Escape also cancels: the picker may not get keyboard focus.

    Windows refuses to hand the foreground to a background process, so the
    picker's own Esc handler can stay deaf until the first click; the shared
    hotkey backend is not.
    """
    try:
        from jarvis.platform.probes import has_hotkey  # noqa: PLC0415

        if not has_hotkey():
            return
        from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

        key = "escape" if sys.platform == "win32" else "esc"
        trigger = HotkeyTrigger({"appshot_region_cancel": [key]})
        async with trigger:
            async for name in trigger.events():
                if name == "appshot_region_cancel":
                    await asyncio.to_thread(_cancel, proc)
                    return
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the picker's own Esc and right-click still cancel
        log.debug("appshot: global Escape for the area picker unavailable", exc_info=True)


async def pick_region(*, timeout_s: float = PICK_TIMEOUT_S) -> Selection | None:
    """Let the user drag a rectangle. ``None`` = cancelled or timed out.

    Raises :class:`RegionUnavailable` when no picker can run on this host.
    """
    from jarvis.appshot.picker import EXIT_NO_GUI  # noqa: PLC0415

    ok, reason = picker_capability()
    if not ok:
        raise RegionUnavailable(f"An area cannot be selected here: {reason}.")
    hint = await asyncio.to_thread(_hint)
    try:
        proc = await asyncio.to_thread(_spawn, hint)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as unavailable
        log.warning("appshot: area picker could not be started", exc_info=True)
        raise RegionUnavailable("The selection overlay could not be started.") from exc
    escape = asyncio.get_running_loop().create_task(
        _escape_cancels(proc), name="appshot-region-esc"
    )
    reader = asyncio.ensure_future(asyncio.to_thread(_read_result, proc))
    try:
        try:
            payload = await asyncio.wait_for(asyncio.shield(reader), timeout=timeout_s)
        except TimeoutError:
            await asyncio.to_thread(_cancel, proc)
            payload = None
    finally:
        escape.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await escape
        code = await asyncio.to_thread(_reap, proc)
        with contextlib.suppress(Exception):  # EOF after the reap ends the reader
            await reader
    if payload is None and code == EXIT_NO_GUI:
        raise RegionUnavailable(
            "An area cannot be selected here: the selection overlay found no usable screen."
        )
    selection = parse_selection(payload) if payload is not None else None
    if selection is not None:
        await asyncio.sleep(_SETTLE_S)
    return selection


__all__ = [
    "MIN_SELECTION_PX",
    "RegionUnavailable",
    "Selection",
    "fraction_to_bbox",
    "match_monitor",
    "parse_selection",
    "pick_region",
    "picker_capability",
    "screen_monitor_score",
    "selection_fractions",
    "selection_to_bbox",
]
