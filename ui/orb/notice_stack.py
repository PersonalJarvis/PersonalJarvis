"""The desktop pet's notifications: a small stack of cards under the strip.

When something the user is waiting for happens — a coding agent finished, a
pane asks a question, a background task failed — the pet's bell rings and a
card drops out from under the control strip. Several cards stack: the newest
in front, up to two older ones peeking out behind it, a little narrower and a
little darker each. Hovering the stack fans it out into a column so every card
can be read; leaving it folds it back. A click dismisses the card under the
pointer, and every card leaves on its own after a while (never while the
pointer is on the stack).

Split in two, like :mod:`ui.orb.controls`:

* **Pure model and geometry** (:class:`NoticeModel`, :func:`stack_slots`,
  :class:`Spring`, :func:`render_icon`) — no Tk, fully testable.
* **The window** (:class:`PetNoticeStack`) — one frameless, colour-keyed Tk
  toplevel whose canvas is redrawn from the model while something moves and
  left alone while nothing does.

The window is colour-keyed like every other pet window: card shapes are Tk
canvas polygons (hard edges, no pink fringe), and the only antialiased pixels —
the icon discs — are painted onto the card's own fill colour, never onto the
key. Motion comes from springs rather than fixed tweens, so a card that is
pushed back while it is still dropping in simply changes course instead of
jumping.
"""

from __future__ import annotations

import functools
import logging
import math
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from PIL import Image, ImageDraw, ImageTk

log = logging.getLogger("jarvis.orb")

_Rgb = tuple[int, int, int]

#: What a notice is about. ``done``: something finished. ``attention``: it
#: waits for the user (a question, an approval). ``error``: it failed.
#: ``info``: anything else worth a glance.
NOTICE_KINDS: tuple[str, ...] = ("done", "attention", "error", "info")

#: Seconds a card stays before it leaves on its own. Something that waits for
#: the user stays longest; a plain "done" is news for a few seconds.
NOTICE_TTL_S: dict[str, float] = {"done": 8.0, "attention": 20.0, "error": 14.0, "info": 6.0}

#: Cards kept at most; a newer one pushes the oldest out.
NOTICE_KEEP = 4
#: Cards visible behind the front one while the stack is folded.
NOTICE_PEEK = 2
#: The same notice again within this window refreshes the card instead of
#: stacking a twin (two events for one finished job).
NOTICE_DEDUPE_S = 3.0

#: Unscaled geometry at 100 % and ``pet_scale`` 1.0.
CARD_HEIGHT = 60
CARD_RADIUS = 20
CARD_PAD_X = 14
CARD_ICON = 32
CARD_ICON_GAP = 12
CARD_GAP = 8
#: How far each card behind the front one peeks out, and how much narrower it
#: is on each side.
PEEK_OFFSET = 9
PEEK_INSET = 11
CARD_TITLE_FONT_SIZE = 10
CARD_DETAIL_FONT_SIZE = 9
CARD_FONT_FAMILY = "Segoe UI"

#: Palette — the strip's blue-black, a shade lighter so a card reads as a
#: surface above the desktop, with a hairline rim.
CARD_FILL: _Rgb = (21, 25, 32)
CARD_FILL_HOVER: _Rgb = (28, 33, 43)
CARD_BORDER: _Rgb = (46, 52, 64)
#: A card two places back sinks this far toward the desktop-dark.
CARD_FILL_DEEP: _Rgb = (12, 14, 19)
CARD_BORDER_DEEP: _Rgb = (28, 32, 40)
CARD_TITLE: _Rgb = (243, 244, 247)
CARD_DETAIL: _Rgb = (156, 161, 172)

#: Icon colours per kind: the glyph, and the disc it sits on.
ICON_COLORS: dict[str, tuple[_Rgb, _Rgb]] = {
    "done": ((74, 222, 128), (20, 58, 38)),
    "attention": ((251, 191, 36), (66, 50, 14)),
    "error": ((248, 113, 113), (70, 26, 30)),
    "info": ((126, 186, 255), (22, 42, 74)),
}

#: The icon's entrance: the disc pops in with a slight overshoot, then the
#: glyph draws itself — a check is ticked, not stamped.
ICON_ANIM_S = 0.55
ICON_FRAMES = 14

#: Spring tuning: stiff enough to feel quick, damped just under critical so a
#: card overshoots by a hair and settles.
SPRING_STIFFNESS = 340.0
SPRING_DAMPING = 30.0
FRAME_MS = 16
#: How often expiry is checked while the stack is at rest.
EXPIRY_POLL_MS = 400
#: Leaving the stack folds it after this long — a pointer crossing the gap
#: between two cards must not fold it under itself.
COLLAPSE_GRACE_MS = 280


def _lerp(a: _Rgb, b: _Rgb, t: float) -> _Rgb:
    t = max(0.0, min(1.0, t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def _hex(rgb: _Rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


# --- model -------------------------------------------------------------------


@dataclass
class Notice:
    """One card."""

    id: int
    kind: str
    title: str
    detail: str
    created: float
    expires: float


@dataclass
class NoticeModel:
    """The cards on screen, newest first. Pure: the clock is passed in."""

    keep: int = NOTICE_KEEP
    items: list[Notice] = field(default_factory=list)
    _ids: Any = field(default_factory=lambda: count(1))

    def push(self, kind: str, title: str, detail: str, now: float) -> tuple[Notice, list[Notice]]:
        """Add a card. Returns it and the cards it pushed out of the stack.

        A twin of a card added moments ago refreshes that card instead.
        """
        kind = kind if kind in NOTICE_KINDS else "info"
        ttl = NOTICE_TTL_S[kind]
        for item in self.items:
            if (item.kind, item.title, item.detail) == (
                kind,
                title,
                detail,
            ) and now - item.created < NOTICE_DEDUPE_S:
                item.expires = now + ttl
                return item, []
        notice = Notice(next(self._ids), kind, title, detail, now, now + ttl)
        self.items.insert(0, notice)
        dropped = self.items[self.keep :]
        del self.items[self.keep :]
        return notice, dropped

    def dismiss(self, notice_id: int) -> Notice | None:
        for index, item in enumerate(self.items):
            if item.id == notice_id:
                return self.items.pop(index)
        return None

    def expire(self, now: float) -> list[Notice]:
        """Remove and return every card whose time is up."""
        gone = [item for item in self.items if item.expires <= now]
        if gone:
            self.items = [item for item in self.items if item.expires > now]
        return gone

    def pause(self, seconds: float) -> None:
        """Push every deadline back (the pointer rested on the stack)."""
        if seconds <= 0:
            return
        for item in self.items:
            item.expires += seconds

    def clear(self) -> list[Notice]:
        gone, self.items = self.items, []
        return gone


# --- geometry ----------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """Where a card wants to be: its top edge, side inset and depth (0 = front)."""

    y: float
    inset: float
    depth: int


def stack_slots(
    count_: int, *, expanded: bool, card_h: int, scale: float, upward: bool = False
) -> list[Slot]:
    """Target slots for ``count_`` cards, newest first, inside the stack window.

    Folded, the front card sits at the top and each older one peeks out below
    it, narrower; cards past ``NOTICE_PEEK`` hide exactly behind the last
    peeking one. Fanned out, they form a column. ``upward`` mirrors it all for
    a stack that grows up from above the pet (no room below).
    """
    peek = PEEK_OFFSET * scale
    inset = PEEK_INSET * scale
    gap = CARD_GAP * scale
    height = stack_height(count_, expanded=expanded, card_h=card_h, scale=scale)
    slots: list[Slot] = []
    for index in range(count_):
        if expanded:
            y, side, depth = index * (card_h + gap), 0.0, 0
        else:
            level = min(index, NOTICE_PEEK)
            y, side, depth = level * peek, level * inset, min(index, NOTICE_PEEK + 1)
        if upward:
            y = height - card_h - y
        slots.append(Slot(y=y, inset=side, depth=depth))
    return slots


def stack_height(count_: int, *, expanded: bool, card_h: int, scale: float) -> int:
    """Height the stack takes with ``count_`` cards."""
    if count_ <= 0:
        return 0
    if expanded:
        return int(round(count_ * card_h + (count_ - 1) * CARD_GAP * scale))
    return int(round(card_h + min(count_ - 1, NOTICE_PEEK) * PEEK_OFFSET * scale))


def max_stack_height(card_h: int, scale: float) -> int:
    """The tallest the stack can get: every kept card fanned out."""
    return stack_height(NOTICE_KEEP, expanded=True, card_h=card_h, scale=scale)


class Spring:
    """One damped spring value. ``step`` advances it by ``dt`` seconds."""

    __slots__ = ("value", "velocity", "target")

    def __init__(self, value: float, target: float | None = None) -> None:
        self.value = float(value)
        self.velocity = 0.0
        self.target = float(value if target is None else target)

    def step(self, dt: float) -> None:
        # Sub-steps keep the integration stable when a frame comes in late.
        steps = max(1, int(math.ceil(dt / 0.008)))
        h = dt / steps
        for _ in range(steps):
            accel = SPRING_STIFFNESS * (self.target - self.value) - SPRING_DAMPING * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h

    @property
    def settled(self) -> bool:
        return abs(self.target - self.value) < 0.4 and abs(self.velocity) < 4.0

    def snap(self) -> None:
        self.value = self.target
        self.velocity = 0.0


# --- icons -------------------------------------------------------------------


def _ease_out_back(t: float) -> float:
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


def icon_frame(age_s: float) -> int:
    """The icon animation frame at ``age_s`` seconds after the card arrived."""
    if age_s >= ICON_ANIM_S:
        return ICON_FRAMES
    return max(0, min(ICON_FRAMES, int(age_s / ICON_ANIM_S * ICON_FRAMES)))


def _partial(points: list[tuple[float, float]], share: float) -> list[tuple[float, float]]:
    """The first ``share`` of a polyline's length."""
    if share >= 1.0:
        return points
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:], strict=False)]
    want = sum(lengths) * max(0.0, share)
    out = [points[0]]
    for (a, b), length in zip(zip(points, points[1:], strict=False), lengths, strict=False):
        if want <= 0:
            break
        if length <= want:
            out.append(b)
            want -= length
            continue
        f = want / length
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        break
    return out


@functools.lru_cache(maxsize=256)
def render_icon(kind: str, frame: int, diameter: int, background: _Rgb) -> Image.Image:
    """The round icon for ``kind`` at animation ``frame`` on an opaque square.

    Drawn at 4x and downscaled, on the card's own fill, so its antialiasing
    never meets the colour key.
    """
    ss = 4
    size = max(2, diameter) * ss
    glyph, disc = ICON_COLORS.get(kind, ICON_COLORS["info"])
    layer = Image.new("RGB", (size, size), background)
    d = ImageDraw.Draw(layer)
    t = frame / ICON_FRAMES
    pop = _ease_out_back(min(1.0, t / 0.55))
    r = size / 2.0 * max(0.0, pop)
    c = size / 2.0
    if r > 0.5:
        d.ellipse([c - r, c - r, c + r, c + r], fill=disc)
    draw = max(0.0, min(1.0, (t - 0.3) / 0.6))
    width = max(1, int(round(size * 0.085)))
    unit = size / 24.0

    def pt(u: float, v: float) -> tuple[float, float]:
        return (c + (u - 12.0) * unit, c + (v - 12.0) * unit)

    def stroke(points: list[tuple[float, float]], share: float) -> None:
        if share <= 0.0:
            return
        line = _partial([pt(u, v) for u, v in points], share)
        if len(line) >= 2:
            d.line(line, fill=glyph, width=width, joint="curve")
        for x, y in (line[0], line[-1]):
            d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=glyph)

    if kind == "done":
        stroke([(7.2, 12.4), (10.4, 15.6), (16.8, 8.6)], draw)
    elif kind == "attention":
        stroke([(12, 6.8), (12, 13.2)], min(1.0, draw * 1.25))
        if draw >= 0.85:
            dot = width * 0.62
            x, y = pt(12, 17.0)
            d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=glyph)
    elif kind == "error":
        stroke([(8.2, 8.2), (15.8, 15.8)], min(1.0, draw * 2.0))
        stroke([(15.8, 8.2), (8.2, 15.8)], max(0.0, draw * 2.0 - 1.0))
    else:
        from ui.orb.controls import _bell_outline  # noqa: PLC0415 — one bell, one drawing

        for line in _bell_outline():
            scaled = [(12 + (u - 12) * 0.62, 12.6 + (v - 12) * 0.62) for u, v in line]
            stroke(scaled, draw)
    return layer.resize((diameter, diameter), Image.Resampling.LANCZOS)


# --- the window ----------------------------------------------------------------


@dataclass
class _Card:
    notice: Notice
    y: Spring
    inset: Spring
    shade: Spring
    x: Spring
    born: float
    leaving: bool = False


def _fit(text: str, font: tkfont.Font, width: int) -> str:
    """``text`` cut with an ellipsis to fit ``width`` pixels."""
    if width <= 0 or not text:
        return ""
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid].rstrip() + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


class PetNoticeStack:
    """The cards' own frameless window. Every method runs on the Tk thread."""

    def __init__(self, parent: tk.Misc, *, scale: float = 1.0, width: int = 320) -> None:
        self._parent = parent
        self._scale = max(0.25, float(scale))
        self._width = max(160, int(width))
        self._model = NoticeModel()
        self._cards: dict[int, _Card] = {}
        self._leaving: list[_Card] = []
        self._expanded = False
        self._hovered_id: int | None = None
        self._pointer_inside = False
        self._hover_since: float | None = None
        self._upward = False
        self._anchor: tuple[int, int, int, int, int] | None = None
        self._top: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._title_font: tkfont.Font | None = None
        self._detail_font: tkfont.Font | None = None
        self._photos: list[ImageTk.PhotoImage] = []
        self._frame_after: str | None = None
        self._expiry_after: str | None = None
        self._collapse_after: str | None = None
        self._last_tick = 0.0
        self._shown = False
        self._mac_transparent = False
        self._build()

    # -- setup --------------------------------------------------------------

    def _build(self) -> None:
        from ui.orb import overlay as _ov  # noqa: PLC0415 — overlay imports this module

        top = tk.Toplevel(self._parent)
        top.overrideredirect(True)
        top.wm_attributes("-topmost", True)
        if sys.platform == "darwin":
            try:
                top.wm_attributes("-transparent", True)
                top.configure(bg="systemTransparent")
                self._mac_transparent = True
            except tk.TclError:
                log.warning("macOS -transparent unsupported — notices render opaque")
                top.configure(bg=_ov.COLOR_KEY_HEX)
        else:
            try:
                top.wm_attributes("-transparentcolor", _ov.COLOR_KEY_HEX)
            except tk.TclError:
                log.debug("no colour key on this window system", exc_info=True)
            top.configure(bg=_ov.COLOR_KEY_HEX)
        _ov._hide_tk_window_from_task_switcher(top)  # noqa: SLF001 — shared overlay helper
        _ov._exclude_tk_window_from_capture(top)  # noqa: SLF001 — shared overlay helper
        top.withdraw()
        canvas = tk.Canvas(
            top,
            bg="systemTransparent" if self._mac_transparent else _ov.COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        if self._mac_transparent:
            top.update_idletasks()
            _ov.apply_macos_clear_backing()
        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Enter>", self._on_enter)
        canvas.bind("<Leave>", self._on_leave)
        canvas.bind("<ButtonRelease-1>", self._on_click)
        self._title_font = tkfont.Font(
            root=top, family=CARD_FONT_FAMILY, size=CARD_TITLE_FONT_SIZE, weight="bold"
        )
        self._detail_font = tkfont.Font(
            root=top, family=CARD_FONT_FAMILY, size=CARD_DETAIL_FONT_SIZE
        )
        self._top = top
        self._canvas = canvas

    # -- geometry -----------------------------------------------------------

    @property
    def card_height(self) -> int:
        return max(24, int(round(CARD_HEIGHT * self._scale)))

    @property
    def window_height(self) -> int:
        return max_stack_height(self.card_height, self._scale) + 2

    @property
    def count(self) -> int:
        return len(self._model.items)

    @property
    def showing(self) -> bool:
        return self._shown

    def set_scale(self, scale: float, width: int) -> None:
        scale = max(0.25, float(scale))
        width = max(160, int(width))
        if (scale, width) == (self._scale, self._width):
            return
        self._scale, self._width = scale, width
        self._retarget(snap=True)
        self._place()
        self._draw()

    def set_anchor(self, anchor: tuple[int, int, int, int, int]) -> None:
        """``(center_x, below_y, above_y, limit_bottom, screen_w)``.

        The stack hangs from ``below_y`` when its fanned-out height fits above
        ``limit_bottom``, else it grows upward from ``above_y``.
        """
        self._anchor = anchor
        upward = anchor[1] + self.window_height > anchor[3]
        if upward != self._upward:
            self._upward = upward
            self._retarget(snap=True)
            self._draw()
        self._place()

    def _place(self) -> None:
        if self._top is None or self._anchor is None:
            return
        center_x, below_y, above_y, _limit, screen_w = self._anchor
        width, height = self._width, self.window_height
        x = max(8, min(center_x - width // 2, screen_w - width - 8))
        y = max(8, above_y - height) if self._upward else below_y
        try:
            self._top.geometry(f"{width}x{height}+{x}+{y}")
            if self._canvas is not None:
                self._canvas.configure(width=width, height=height)
        except tk.TclError:
            log.debug("notice stack placement failed", exc_info=True)

    # -- public ---------------------------------------------------------------

    def push(self, kind: str, title: str, detail: str) -> None:
        title = " ".join(str(title or "").split())
        detail = " ".join(str(detail or "").split())
        if not title:
            title, detail = detail, ""
        if not title:
            return
        now = time.monotonic()
        notice, dropped = self._model.push(kind, title, detail, now)
        for gone in dropped:
            self._send_off(gone.id)
        if notice.id not in self._cards:
            card_h = self.card_height
            start_y = -card_h * 0.8 if not self._upward else self.window_height + card_h * 0.2
            self._cards[notice.id] = _Card(
                notice=notice,
                y=Spring(start_y),
                inset=Spring(PEEK_INSET * self._scale * 2),
                shade=Spring(0.0),
                x=Spring(0.0),
                born=now,
            )
        else:
            self._cards[notice.id].born = now
        self._retarget()
        self._show()
        self._kick()

    def clear(self) -> None:
        """Send every card away (the bell was switched off)."""
        for gone in self._model.clear():
            self._send_off(gone.id)
        self._retarget()
        self._kick()

    def hide(self) -> None:
        """Withdraw at once (the pet was hidden); the cards are dropped."""
        self._model.clear()
        self._cards.clear()
        self._leaving.clear()
        self._withdraw()

    def destroy(self) -> None:
        self._cancel_all()
        top, self._top, self._canvas = self._top, None, None
        self._photos.clear()
        if top is not None:
            try:
                top.destroy()
            except tk.TclError:
                log.debug("notice stack destroy failed", exc_info=True)

    # -- motion -----------------------------------------------------------------

    def _send_off(self, notice_id: int) -> None:
        card = self._cards.pop(notice_id, None)
        if card is None:
            return
        card.leaving = True
        card.x.target = self._width + 24
        card.x.velocity = max(card.x.velocity, 260.0)
        self._leaving.append(card)

    def _retarget(self, *, snap: bool = False) -> None:
        items = self._model.items
        slots = stack_slots(
            len(items),
            expanded=self._expanded,
            card_h=self.card_height,
            scale=self._scale,
            upward=self._upward,
        )
        for item, slot in zip(items, slots, strict=False):
            card = self._cards.get(item.id)
            if card is None:
                continue
            card.y.target = slot.y
            card.inset.target = slot.inset
            card.shade.target = float(min(slot.depth, NOTICE_PEEK + 1))
            if snap:
                for spring in (card.y, card.inset, card.shade):
                    spring.snap()

    def _kick(self) -> None:
        if self._top is None or self._frame_after is not None:
            return
        self._last_tick = time.monotonic()
        self._frame_after = self._top.after(FRAME_MS, self._tick)

    def _tick(self) -> None:
        self._frame_after = None
        if self._top is None:
            return
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._last_tick))
        self._last_tick = now
        moving = False
        for card in list(self._cards.values()) + self._leaving:
            for spring in (card.y, card.inset, card.shade, card.x):
                if not spring.settled:
                    spring.step(dt)
                    moving = True
                else:
                    spring.snap()
            if now - card.born < ICON_ANIM_S + 0.05:
                moving = True
        self._leaving = [c for c in self._leaving if c.x.value < self._width + 8]
        self._draw()
        if moving or self._leaving:
            self._frame_after = self._top.after(FRAME_MS, self._tick)
            return
        if not self._cards:
            self._withdraw()
            return
        self._schedule_expiry()

    def _schedule_expiry(self) -> None:
        if self._top is None or self._expiry_after is not None or not self._cards:
            return
        self._expiry_after = self._top.after(EXPIRY_POLL_MS, self._check_expiry)

    def _check_expiry(self) -> None:
        self._expiry_after = None
        if self._top is None:
            return
        now = time.monotonic()
        if self._pointer_inside:
            # Reading is not idling: the clocks stand still under the pointer.
            if self._hover_since is not None:
                self._model.pause(now - self._hover_since)
            self._hover_since = now
        gone = self._model.expire(now)
        for notice in gone:
            self._send_off(notice.id)
        if gone:
            if not self._model.items:
                self._set_expanded(False)
            self._retarget()
            self._kick()
            return
        self._schedule_expiry()

    # -- pointer ------------------------------------------------------------

    def _card_at(self, x: float, y: float) -> int | None:
        """The front-most card under ``(x, y)``."""
        card_h = self.card_height
        for item in self._model.items:
            card = self._cards.get(item.id)
            if card is None:
                continue
            x0 = card.inset.value + card.x.value
            x1 = self._width - card.inset.value + card.x.value
            if x0 <= x <= x1 and card.y.value <= y <= card.y.value + card_h:
                return item.id
        return None

    def _on_enter(self, event: tk.Event) -> None:
        self._on_motion(event)

    def _on_motion(self, event: tk.Event) -> None:
        hit = self._card_at(event.x, event.y)
        inside = hit is not None
        if inside and not self._pointer_inside:
            self._hover_since = time.monotonic()
        self._pointer_inside = inside
        if inside:
            self._cancel_collapse()
            self._set_expanded(True)
        elif self._expanded:
            self._schedule_collapse()
        if hit != self._hovered_id:
            self._hovered_id = hit
            if self._canvas is not None:
                try:
                    self._canvas.configure(cursor="hand2" if hit is not None else "")
                except tk.TclError:
                    log.debug("notice cursor change failed", exc_info=True)
            self._draw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._pointer_inside and self._hover_since is not None:
            self._model.pause(time.monotonic() - self._hover_since)
        self._pointer_inside = False
        self._hover_since = None
        if self._hovered_id is not None:
            self._hovered_id = None
            self._draw()
        self._schedule_collapse()

    def _on_click(self, event: tk.Event) -> None:
        hit = self._card_at(event.x, event.y)
        if hit is None:
            return
        self._model.dismiss(hit)
        self._send_off(hit)
        self._hovered_id = None
        if not self._model.items:
            self._set_expanded(False)
        self._retarget()
        self._kick()

    def _set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._retarget()
        self._kick()

    def _schedule_collapse(self) -> None:
        if self._top is None or self._collapse_after is not None or not self._expanded:
            return
        self._collapse_after = self._top.after(COLLAPSE_GRACE_MS, self._collapse_now)

    def _collapse_now(self) -> None:
        self._collapse_after = None
        if not self._pointer_inside:
            self._set_expanded(False)

    def _cancel_collapse(self) -> None:
        if self._collapse_after is not None and self._top is not None:
            try:
                self._top.after_cancel(self._collapse_after)
            except tk.TclError:
                log.debug("collapse cancel failed", exc_info=True)
        self._collapse_after = None

    # -- painting -------------------------------------------------------------

    def _show(self) -> None:
        if self._top is None or self._shown:
            return
        self._place()
        try:
            self._top.deiconify()
            self._top.lift()
        except tk.TclError:
            log.debug("notice stack show failed", exc_info=True)
            return
        self._shown = True

    def _withdraw(self) -> None:
        self._cancel_all()
        self._expanded = False
        self._pointer_inside = False
        self._hovered_id = None
        if self._canvas is not None:
            try:
                self._canvas.delete("all")
            except tk.TclError:
                log.debug("notice canvas clear failed", exc_info=True)
        self._photos.clear()
        if self._top is not None and self._shown:
            try:
                self._top.withdraw()
            except tk.TclError:
                log.debug("notice stack withdraw failed", exc_info=True)
        self._shown = False

    def _cancel_all(self) -> None:
        top = self._top
        for attr in ("_frame_after", "_expiry_after", "_collapse_after"):
            after_id = getattr(self, attr)
            if after_id is not None and top is not None:
                try:
                    top.after_cancel(after_id)
                except tk.TclError:
                    log.debug("notice timer cancel failed", exc_info=True)
            setattr(self, attr, None)

    def _draw(self) -> None:
        canvas = self._canvas
        if canvas is None or self._title_font is None or self._detail_font is None:
            return
        try:
            canvas.delete("all")
        except tk.TclError:
            return
        self._photos.clear()
        order = [self._cards[i.id] for i in self._model.items if i.id in self._cards]
        # Back to front: the deepest card first, leaving cards above the rest
        # so a dismissed card slides out over its neighbours.
        for card in reversed(order):
            self._draw_card(card)
        for card in self._leaving:
            self._draw_card(card)

    def _draw_card(self, card: _Card) -> None:
        canvas = self._canvas
        assert canvas is not None and self._title_font is not None  # noqa: S101
        assert self._detail_font is not None  # noqa: S101
        scale = self._scale
        card_h = self.card_height
        depth = max(0.0, card.shade.value)
        if depth > NOTICE_PEEK + 0.5:
            return
        x0 = card.inset.value + card.x.value
        x1 = self._width - card.inset.value + card.x.value - 1
        y0 = card.y.value
        y1 = y0 + card_h - 1
        hovered = card.notice.id == self._hovered_id and not card.leaving
        deep = min(1.0, depth / max(1, NOTICE_PEEK))
        fill = _lerp(CARD_FILL_HOVER if hovered else CARD_FILL, CARD_FILL_DEEP, deep * 0.85)
        border = _lerp(CARD_BORDER, CARD_BORDER_DEEP, deep)
        radius = min(card_h / 2.0, CARD_RADIUS * scale)
        _rounded_rect(canvas, x0, y0, x1, y1, radius, fill=_hex(fill), outline=_hex(border))
        if depth > 0.35:
            return  # a card behind the front one shows only its edge
        icon_d = max(8, int(round(CARD_ICON * scale)))
        pad_x = CARD_PAD_X * scale
        icon_x = x0 + pad_x
        icon_y = y0 + (card_h - icon_d) / 2.0
        age = time.monotonic() - card.born
        image = render_icon(card.notice.kind, icon_frame(age), icon_d, fill)
        photo = ImageTk.PhotoImage(image, master=canvas)
        self._photos.append(photo)
        canvas.create_image(int(round(icon_x)), int(round(icon_y)), anchor="nw", image=photo)
        text_x = icon_x + icon_d + CARD_ICON_GAP * scale
        text_w = int(x1 - pad_x - text_x)
        title_lh = int(self._title_font.metrics("linespace"))
        detail_lh = int(self._detail_font.metrics("linespace"))
        detail = card.notice.detail
        block = title_lh + (detail_lh + 1 if detail else 0)
        ty = y0 + (card_h - block) / 2.0
        canvas.create_text(
            text_x,
            ty,
            text=_fit(card.notice.title, self._title_font, text_w),
            font=self._title_font,
            anchor="nw",
            fill=_hex(CARD_TITLE),
        )
        if detail:
            canvas.create_text(
                text_x,
                ty + title_lh + 1,
                text=_fit(detail, self._detail_font, text_w),
                font=self._detail_font,
                anchor="nw",
                fill=_hex(CARD_DETAIL),
            )


def _rounded_rect(
    canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, r: float, **kwargs: Any
) -> int:
    """A rounded rectangle with true circular corners, as one canvas polygon.

    The corners are explicit arc points rather than Tk's ``smooth`` spline,
    which pulls a corner flatter than its radius.
    """
    r = max(1.0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    steps = max(4, int(r / 2.0))
    points: list[float] = []
    for cx, cy, start in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90),
                          (x0 + r, y0 + r, 180)):  # fmt: skip
        for i in range(steps + 1):
            a = math.radians(start + 90.0 * i / steps)
            points.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return canvas.create_polygon(points, **kwargs)


__all__ = [
    "NOTICE_KINDS",
    "Notice",
    "NoticeModel",
    "PetNoticeStack",
    "Slot",
    "Spring",
    "icon_frame",
    "max_stack_height",
    "render_icon",
    "stack_height",
    "stack_slots",
]
