"""Pure coordinate and timing math for Jarvis X — no Qt, no OS calls.

Two coordinate worlds meet here:

- **capture space** — what ``mss`` and the native window APIs use: physical
  pixels on Windows (the process is per-monitor DPI aware), points on macOS,
  root-window pixels on X11. ``monitors`` below are mss-shaped dicts
  (``[0]`` = the virtual desktop, ``[1:]`` = the physical screens).
- **Qt space** — the overlay sidecar's logical pixels, one ``QScreen`` per
  monitor with its own ``devicePixelRatio``.

A selection never crosses between them as absolute coordinates. The overlay
reports *which screen* (its Qt geometry and scale) and the selection as
*fractions of that screen*; :func:`match_monitor` finds the same screen in
capture space and :func:`fraction_to_bbox` turns the fractions back into
capture pixels. Fractions survive every mixed-DPI layout, because a screen's
content scales uniformly within itself even when the virtual-desktop origins
of different screens disagree between the two worlds.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A drag smaller than this (logical px, either side) is treated as a cancel.
MIN_SELECTION_PX = 4

#: At most this many thumbnail cards rest on screen; the oldest leaves first.
MAX_CARDS = 5

#: Bounds for ``[jarvisx].thumbnail_dismiss_s``.
DISMISS_MIN_S = 1
DISMISS_MAX_S = 3600

Rect = tuple[int, int, int, int]
FRect = tuple[float, float, float, float]


# --------------------------------------------------------------------------
# Selection → capture pixels
# --------------------------------------------------------------------------


def selection_fractions(
    x0: float, y0: float, x1: float, y1: float, width: float, height: float
) -> FRect | None:
    """A drag from ``(x0, y0)`` to ``(x1, y1)`` on a ``width × height`` screen.

    Returns ``(fx, fy, fw, fh)`` clamped to the screen, or ``None`` when the
    drag is too small to be a selection (a click, a slip of the mouse).
    Works for a drag in any direction.
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


def fraction_to_bbox(monitor: dict, frac: FRect | list[float]) -> Rect:
    """Fractions of ``monitor`` → ``(left, top, width, height)`` in capture space.

    Edges are rounded independently so two adjacent selections never overlap
    or leave a gap, and the result is clamped to the monitor.
    """
    ml, mt = int(monitor.get("left", 0)), int(monitor.get("top", 0))
    mw, mh = max(1, int(monitor.get("width", 1))), max(1, int(monitor.get("height", 1)))
    fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in frac)
    x0 = ml + round(fx * mw)
    y0 = mt + round(fy * mh)
    x1 = ml + round(min(1.0, fx + fw) * mw)
    y1 = mt + round(min(1.0, fy + fh) * mh)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def match_monitor(screen: dict, monitors: list[dict]) -> dict | None:
    """The capture-space monitor that is the overlay's Qt ``screen``.

    ``screen`` carries the Qt geometry ``x, y, w, h`` (logical) and ``dpr``.
    Each monitor is compared with every plausible reading of the Qt geometry
    — logical as-is (macOS points, X11 at scale 1), logical origin with
    physical size, and fully physical (Windows per-monitor DPI) — and the
    closest one wins. ``None`` only when there are no monitors at all.
    """
    physical = [m for m in (monitors[1:] if len(monitors) > 1 else monitors) if isinstance(m, dict)]
    if not physical:
        return None
    return min(physical, key=lambda mon: screen_monitor_score(screen, mon))


def screen_monitor_score(screen: dict, monitor: dict) -> float:
    """How far a Qt screen is from a capture-space monitor (0 = identical).

    Used in both directions: by the service to find the monitor of a
    selection, and by the overlay to find the screen of a monitor.
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


def monitor_fraction(bbox: Rect, monitor: dict) -> FRect:
    """``bbox`` (capture space) as fractions of ``monitor``, clipped to it."""
    left, top, width, height = bbox
    ml, mt = int(monitor.get("left", 0)), int(monitor.get("top", 0))
    mw, mh = max(1, int(monitor.get("width", 1))), max(1, int(monitor.get("height", 1)))
    x0, y0 = max(left, ml), max(top, mt)
    x1, y1 = min(left + width, ml + mw), min(top + height, mt + mh)
    if x1 <= x0 or y1 <= y0:
        return (0.0, 0.0, 1.0, 1.0)
    return ((x0 - ml) / mw, (y0 - mt) / mh, (x1 - x0) / mw, (y1 - y0) / mh)


def monitor_rect(monitor: dict) -> list[int]:
    return [
        int(monitor.get("left", 0)),
        int(monitor.get("top", 0)),
        int(monitor.get("width", 0)),
        int(monitor.get("height", 0)),
    ]


def even_size(width: int, height: int) -> tuple[int, int]:
    """H.264 with 4:2:0 chroma needs even dimensions; trim one pixel if odd."""
    return (max(2, int(width) - int(width) % 2), max(2, int(height) - int(height) % 2))


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------


def clamp_dismiss_s(value: object) -> int:
    try:
        seconds = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 30
    return max(DISMISS_MIN_S, min(DISMISS_MAX_S, seconds))


def card_stack(
    screen_w: float,
    screen_h: float,
    sizes: list[tuple[float, float]],
    *,
    margin: float = 28.0,
    gap: float = 12.0,
) -> list[tuple[float, float]]:
    """Top-left corners for cards stacked up from the bottom-right corner.

    ``sizes`` is ordered newest first; the newest card sits lowest, older
    ones rise above it. A card that would leave the top of the screen is
    placed at the top margin (the caller caps the stack at ``MAX_CARDS``).
    """
    corners: list[tuple[float, float]] = []
    bottom = screen_h - margin
    for w, h in sizes:
        x = screen_w - margin - w
        y = max(margin, bottom - h)
        corners.append((x, y))
        bottom = y - gap
    return corners


class CardTimer:
    """When a resting thumbnail card fades out.

    ``persist`` cards never expire. Otherwise the card expires ``dismiss_s``
    seconds after it came to rest; while the pointer hovers it, the clock is
    paused, and leaving restarts the full countdown — a card never vanishes
    from under the cursor that is about to click it.
    """

    def __init__(self, *, persist: bool, dismiss_s: object, now: float) -> None:
        self.persist = bool(persist)
        self.dismiss_s = float(clamp_dismiss_s(dismiss_s))
        self._deadline: float | None = None if self.persist else now + self.dismiss_s
        self._hovered = False

    def hover(self, hovered: bool, now: float) -> None:
        if self.persist or hovered == self._hovered:
            return
        self._hovered = hovered
        self._deadline = None if hovered else now + self.dismiss_s

    def remaining(self, now: float) -> float | None:
        """Seconds left, or ``None`` while it cannot expire."""
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - now)

    def expired(self, now: float) -> bool:
        return self._deadline is not None and now >= self._deadline


# --------------------------------------------------------------------------
# Recording chrome
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PillPlacement:
    x: float
    y: float
    #: ``True`` when no spot outside the recorded area existed; the pill then
    #: relies on the OS capture exclusion to stay out of the video.
    overlaps: bool


def pill_placement(
    region: FRect | None,
    screen_w: float,
    screen_h: float,
    pill_w: float,
    pill_h: float,
    *,
    margin: float = 10.0,
) -> PillPlacement:
    """Where the "Stop" pill goes so it stays out of the recorded area.

    ``region`` is the recorded area as screen fractions (``None`` = the whole
    screen). Preference: centred below the region, then above it, then beside
    it; a full-screen recording gets the bottom centre and ``overlaps=True``.
    """
    if region is None:
        return PillPlacement((screen_w - pill_w) / 2.0, screen_h - pill_h - 24.0, True)
    fx, fy, fw, fh = region
    left, top = fx * screen_w, fy * screen_h
    right, bottom = left + fw * screen_w, top + fh * screen_h
    cx = min(max(margin, (left + right) / 2.0 - pill_w / 2.0), screen_w - pill_w - margin)
    if bottom + margin + pill_h + margin <= screen_h:
        return PillPlacement(cx, bottom + margin, False)
    if top - margin - pill_h - margin >= 0:
        return PillPlacement(cx, top - margin - pill_h, False)
    cy = min(max(margin, (top + bottom) / 2.0 - pill_h / 2.0), screen_h - pill_h - margin)
    if right + margin + pill_w + margin <= screen_w:
        return PillPlacement(right + margin, cy, False)
    if left - margin - pill_w - margin >= 0:
        return PillPlacement(left - margin - pill_w, cy, False)
    return PillPlacement((screen_w - pill_w) / 2.0, screen_h - pill_h - 24.0, True)


def format_elapsed(seconds: float) -> str:
    """``00:12`` / ``1:02:03`` for the recording pill."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


__all__ = [
    "MAX_CARDS",
    "MIN_SELECTION_PX",
    "CardTimer",
    "PillPlacement",
    "card_stack",
    "clamp_dismiss_s",
    "even_size",
    "format_elapsed",
    "fraction_to_bbox",
    "match_monitor",
    "monitor_fraction",
    "monitor_rect",
    "pill_placement",
    "screen_monitor_score",
    "selection_fractions",
]
