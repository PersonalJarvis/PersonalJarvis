"""The desktop pet's card column: what Jarvis is thinking, and what it finished.

Under the control strip hangs one quiet column (the look is
:mod:`ui.orb.pet_cards`):

* **The thinking line** at the top while Jarvis thinks — one muted line with a
  light sweeping across it. It changes in place as the thought moves on and
  fades out after Jarvis stops.
* **Done cards** below it when a Jarvis turn, or a task Jarvis started,
  finished: a check, what was asked, the start of the answer. Several stack:
  the newest in front, up to two older ones peeking out behind it, smaller
  and fainter. Hovering fans them into a column; a click sends one away; each
  leaves on its own after a while, never while the pointer rests on them.

Split like :mod:`ui.orb.controls`: a pure model and geometry
(:class:`NoticeModel`, :func:`column_layout`, :class:`Spring`), and one Tk
window (:class:`PetNoticeStack`) that composites the cards into a single
frame while something moves and leaves the screen alone while nothing does.
On Windows the frame goes through ``UpdateLayeredWindow`` with real per-pixel
alpha (see-through cards, soft shadows, true fades); elsewhere, or if Windows
refuses it, the same frame is flattened onto a colour key.
"""

from __future__ import annotations

import logging
import sys
import time
import tkinter as tk
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from PIL import Image, ImageTk

from jarvis.platform import layered_window
from ui.orb import pet_cards

log = logging.getLogger("jarvis.orb")

#: Card kinds the stack keeps (the thinking line is separate).
NOTICE_KINDS: tuple[str, ...] = ("done", "error")
#: Seconds a card stays before it leaves on its own.
NOTICE_TTL_S: dict[str, float] = {"done": 9.0, "error": 14.0}
#: Cards kept at most; a newer one pushes the oldest out.
NOTICE_KEEP = 3
#: Cards visible behind the front one while the stack is folded.
NOTICE_PEEK = 2
#: The same card again within this window refreshes instead of stacking.
NOTICE_DEDUPE_S = 3.0

#: Unscaled geometry: the gap between cards, how far a folded card peeks out
#: and how much smaller and fainter each one behind is.
CARD_GAP = 8
PEEK_OFFSET = 10
PEEK_SHRINK = 0.05
PEEK_FADE = 0.25

SPRING_STIFFNESS = 320.0
SPRING_DAMPING = 30.0
FRAME_MS = 16
#: Frame interval while only the thinking line's sweep moves.
SHIMMER_FRAME_MS = 50
EXPIRY_POLL_MS = 400
COLLAPSE_GRACE_MS = 280
#: How far a card drifts while it fades in or out.
DRIFT_PX = 10


def _spx(value: float, scale: float) -> int:
    return max(1, int(round(value * max(0.25, float(scale)))))


# --- model -------------------------------------------------------------------


@dataclass
class Notice:
    id: int
    kind: str
    title: str
    detail: str
    created: float
    expires: float


@dataclass
class NoticeModel:
    """The done cards, newest first. Pure: the clock is passed in."""

    keep: int = NOTICE_KEEP
    items: list[Notice] = field(default_factory=list)
    _ids: Any = field(default_factory=lambda: count(1))

    def push(self, kind: str, title: str, detail: str, now: float) -> tuple[Notice, list[Notice]]:
        """Add a card; returns it and the cards it pushed out of the stack."""
        kind = kind if kind in NOTICE_KINDS else "done"
        ttl = NOTICE_TTL_S[kind]
        for item in self.items:
            if (item.kind, item.title, item.detail) == (kind, title, detail) and (
                now - item.created < NOTICE_DEDUPE_S
            ):
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
        gone = [item for item in self.items if item.expires <= now]
        if gone:
            self.items = [item for item in self.items if item.expires > now]
        return gone

    def pause(self, seconds: float) -> None:
        """Push every deadline back (the pointer rested on the stack)."""
        if seconds > 0:
            for item in self.items:
                item.expires += seconds

    def clear(self) -> list[Notice]:
        gone, self.items = self.items, []
        return gone


# --- geometry ----------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """Where a card wants to be: its top edge, size factor and opacity."""

    y: float
    size: float
    opacity: float


def column_layout(
    status_h: int | None,
    heights: list[int],
    *,
    expanded: bool,
    scale: float,
) -> tuple[Slot | None, list[Slot]]:
    """Targets for the thinking line and the cards (newest first), top down.

    Folded, every card lines up its bottom with the front card's bottom and
    peeks out below it, a step smaller and fainter per place; cards past
    ``NOTICE_PEEK`` hide behind the last peeking one. Fanned out, they form a
    column.
    """
    gap = CARD_GAP * scale
    cursor = 0.0
    status = None
    if status_h is not None:
        status = Slot(0.0, 1.0, 1.0)
        cursor = status_h + gap
    slots: list[Slot] = []
    if expanded:
        for h in heights:
            slots.append(Slot(cursor, 1.0, 1.0))
            cursor += h + gap
        return status, slots
    if not heights:
        return status, slots
    front_bottom = cursor + heights[0]
    for index, h in enumerate(heights):
        level = min(index, NOTICE_PEEK)
        size = 1.0 - PEEK_SHRINK * level
        opacity = (1.0 - PEEK_FADE * level) if index <= NOTICE_PEEK else 0.0
        bottom = front_bottom + level * PEEK_OFFSET * scale
        slots.append(Slot(bottom - h * size, size, opacity))
    return status, slots


class Spring:
    """One damped spring value."""

    __slots__ = ("target", "value", "velocity")

    def __init__(self, value: float, target: float | None = None) -> None:
        self.value = float(value)
        self.velocity = 0.0
        self.target = float(value if target is None else target)

    def step(self, dt: float) -> None:
        steps = max(1, int(dt / 0.008 + 0.999))
        h = dt / steps
        for _ in range(steps):
            accel = SPRING_STIFFNESS * (self.target - self.value) - SPRING_DAMPING * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h

    @property
    def settled(self) -> bool:
        reach = max(1.0, abs(self.target))
        return abs(self.target - self.value) < 0.004 * reach and abs(self.velocity) < 0.04 * reach

    def snap(self) -> None:
        self.value = self.target
        self.velocity = 0.0


# --- the window --------------------------------------------------------------


@dataclass
class _Item:
    """One card on screen: its content and its moving parts."""

    key: int  # notice id; 0 is the thinking line
    kind: str
    title: str
    detail: str
    y: Spring
    size: Spring
    opacity: Spring
    born: float
    leaving: bool = False
    #: Where the card was last drawn (x0, y0, x1, y1) for hit tests.
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


class PetNoticeStack:
    """The card column's own frameless window. Every method runs on the Tk thread."""

    def __init__(self, parent: tk.Misc, *, scale: float = 1.0, width: int = 360) -> None:
        self._parent = parent
        self._scale = max(0.25, float(scale))
        self._card_w = max(160, int(width))
        self._model = NoticeModel()
        self._items: dict[int, _Item] = {}
        self._leaving: list[_Item] = []
        self._status: _Item | None = None
        self._expanded = False
        self._hovered: int | None = None
        self._pointer_inside = False
        self._hover_since: float | None = None
        self._upward = False
        self._anchor: tuple[int, int, int, int, int] | None = None
        self._top: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._image_id: int | None = None
        self._alpha_hwnd = 0
        self._mac_transparent = False
        self._frame_after: str | None = None
        self._expiry_after: str | None = None
        self._collapse_after: str | None = None
        self._status_clear_after: str | None = None
        self._last_tick = 0.0
        self._shown = False
        self._build()

    # -- setup ----------------------------------------------------------------

    @property
    def _pad(self) -> int:
        return _spx(pet_cards.SHADOW_PAD, self._scale)

    @property
    def window_size(self) -> tuple[int, int]:
        """Room for the thinking line plus every kept card fanned out."""
        s = self._scale
        tallest_card = _spx(
            2 * pet_cards.PAD_Y + 1.45 * pet_cards.TITLE_PX + 3 + 2 * 1.45 * pet_cards.BODY_PX, s
        )
        gap = CARD_GAP * s
        height = _spx(pet_cards.STATUS_HEIGHT, s) + NOTICE_KEEP * (tallest_card + gap) + gap
        return self._card_w + 2 * self._pad, int(height) + 2 * self._pad

    def _blank(self) -> Image.Image:
        return Image.new("RGBA", self.window_size, (0, 0, 0, 0))

    def _build(self) -> None:
        from ui.orb import overlay as _ov  # noqa: PLC0415 — overlay imports this module

        top = tk.Toplevel(self._parent)
        top.overrideredirect(True)
        top.wm_attributes("-topmost", True)
        width, height = self.window_size
        if sys.platform == "darwin":
            try:
                top.wm_attributes("-transparent", True)
                top.configure(bg="systemTransparent")
                self._mac_transparent = True
            except tk.TclError:
                log.warning("macOS -transparent unsupported — the pet's cards render opaque")
                top.configure(bg=_ov.COLOR_KEY_HEX)
        else:
            if layered_window.per_pixel_alpha_supported():
                top.update_idletasks()
                hwnd = layered_window.tk_toplevel_hwnd(top)
                # The first frame goes in now: Windows refuses the very first
                # UpdateLayeredWindow on a window that was already withdrawn.
                if layered_window.enable_per_pixel_alpha(hwnd) and layered_window.update_layered(
                    hwnd, self._blank()
                ):
                    self._alpha_hwnd = hwnd
            if not self._alpha_hwnd:
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
            width=width,
            height=height,
            bg="systemTransparent" if self._mac_transparent else _ov.COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        if self._mac_transparent:
            top.update_idletasks()
            _ov.apply_macos_clear_backing()
        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Enter>", self._on_motion)
        canvas.bind("<Leave>", self._on_leave)
        canvas.bind("<ButtonRelease-1>", self._on_click)
        self._top = top
        self._canvas = canvas

    # -- public ---------------------------------------------------------------

    @property
    def count(self) -> int:
        """Done cards on screen (the thinking line is not counted)."""
        return len(self._model.items)

    @property
    def status_showing(self) -> bool:
        return self._status is not None

    @property
    def window(self) -> tk.Toplevel | None:
        """The column's toplevel while it is on screen (for Z-order repair)."""
        return self._top if self._shown else None

    def set_scale(self, scale: float, width: int) -> None:
        scale, width = max(0.25, float(scale)), max(160, int(width))
        if (scale, width) == (self._scale, self._card_w):
            return
        self._scale, self._card_w = scale, width
        if self._canvas is not None:
            w, h = self.window_size
            try:
                self._canvas.configure(width=w, height=h)
            except tk.TclError:
                log.debug("card canvas resize failed", exc_info=True)
        self._retarget(snap=True)
        self._place()
        self._kick()

    def set_anchor(self, anchor: tuple[int, int, int, int, int]) -> None:
        """``(center_x, below_y, above_y, limit_bottom, screen_w)``.

        The column hangs from ``below_y`` when it fits above ``limit_bottom``,
        else it grows upward from ``above_y``.
        """
        self._anchor = anchor
        upward = anchor[1] + self.window_size[1] - 2 * self._pad > anchor[3]
        if upward != self._upward:
            self._upward = upward
            self._retarget(snap=True)
            self._kick()
        self._place()

    def push(self, kind: str, title: str, detail: str) -> None:
        """A done (or failed) card."""
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
        item = self._items.get(notice.id)
        if item is None:
            self._items[notice.id] = _Item(
                key=notice.id,
                kind=notice.kind,
                title=notice.title,
                detail=notice.detail,
                y=Spring(0.0),
                size=Spring(0.96),
                opacity=Spring(0.0),
                born=now,
            )
            self._retarget(fresh=notice.id)
        else:
            item.born = now
            self._retarget()
        self._show()
        self._kick()

    def set_status(self, text: str) -> None:
        """Show or update the thinking line."""
        text = " ".join(str(text or "").split())
        if not text:
            self.clear_status(0.0)
            return
        self._cancel(("_status_clear_after",))
        item = self._status
        if item is None:
            item = _Item(
                key=0,
                kind="status",
                title=text,
                detail="",
                y=Spring(0.0),
                size=Spring(0.97),
                opacity=Spring(0.0),
                born=time.monotonic(),
            )
            self._status = item
            self._retarget(fresh=0)
        else:
            item.title = text
            self._retarget()
        self._show()
        self._kick()

    def clear_status(self, linger_s: float = 1.5) -> None:
        """Fade the thinking line out after ``linger_s`` seconds (at once when 0).

        A pending clear keeps its earlier deadline; a new status cancels it.
        """
        if self._status is None or self._top is None or self._status_clear_after is not None:
            return
        delay = max(0, int(float(linger_s) * 1000))
        if delay == 0:
            self._drop_status()
            return
        self._status_clear_after = self._top.after(delay, self._drop_status)

    def clear(self) -> None:
        """Send every done card away (the bell was switched off)."""
        for gone in self._model.clear():
            self._send_off(gone.id)
        self._expanded = False
        self._retarget()
        self._kick()

    def hide(self) -> None:
        """Withdraw at once (the pet was hidden); everything is dropped."""
        self._model.clear()
        self._items.clear()
        self._leaving.clear()
        self._status = None
        self._withdraw()

    def destroy(self) -> None:
        self._cancel_all()
        top, self._top, self._canvas = self._top, None, None
        self._photo = None
        if top is not None:
            try:
                top.destroy()
            except tk.TclError:
                log.debug("card window destroy failed", exc_info=True)

    # -- motion ---------------------------------------------------------------

    def _drop_status(self) -> None:
        self._status_clear_after = None
        item = self._status
        if item is None:
            return
        item.leaving = True
        item.opacity.target = 0.0
        item.size.target = 0.97
        self._leaving.append(item)
        self._status = None
        self._retarget()
        self._kick()

    def _send_off(self, notice_id: int) -> None:
        item = self._items.pop(notice_id, None)
        if item is None:
            return
        item.leaving = True
        item.opacity.target = 0.0
        item.size.target = item.size.value * 0.95
        self._leaving.append(item)

    def _card_image(
        self,
        item: _Item,
        *,
        hovered: bool = False,
        frame: int | None = None,
        content: bool = True,
    ) -> Image.Image:
        scale = round(self._scale, 3)
        if item.kind == "status":
            return pet_cards.render_card(
                "status", item.title, "", scale=scale, max_width=self._card_w
            )
        return pet_cards.render_card(
            item.kind,
            item.title,
            item.detail,
            scale=scale,
            max_width=self._card_w,
            hovered=hovered,
            icon_frame=pet_cards.ICON_FRAMES if frame is None else frame,
            content=content,
        )

    def _card_height(self, item: _Item) -> int:
        return self._card_image(item).height - 2 * self._pad

    def _cards(self) -> list[_Item]:
        """The done cards on screen, newest first."""
        return [self._items[n.id] for n in self._model.items if n.id in self._items]

    def _retarget(self, *, snap: bool = False, fresh: int | None = None) -> None:
        status_h = self._card_height(self._status) if self._status is not None else None
        cards = self._cards()
        heights = [self._card_height(c) for c in cards]
        status_slot, slots = column_layout(
            status_h, heights, expanded=self._expanded, scale=self._scale
        )
        inner_h = self.window_size[1] - 2 * self._pad
        pairs: list[tuple[_Item, Slot, int]] = []
        if self._status is not None and status_slot is not None and status_h is not None:
            pairs.append((self._status, status_slot, status_h))
        pairs += list(zip(cards, slots, heights, strict=False))
        for item, slot, h in pairs:
            y = slot.y
            if self._upward:
                # Growing up from above the pet: mirror the column, newest
                # nearest the pet.
                y = inner_h - (slot.y + h * slot.size)
            item.y.target = y
            item.size.target = slot.size
            item.opacity.target = slot.opacity
            if item.key == fresh:
                # A new card starts a little off its place and drifts in
                # while it fades up.
                drift = DRIFT_PX * self._scale
                item.y.value = y + (drift if self._upward else -drift)
            if snap:
                for spring in (item.y, item.size, item.opacity):
                    spring.snap()

    def _kick(self, delay: int = FRAME_MS) -> None:
        if self._top is None or self._frame_after is not None:
            return
        self._cancel(("_expiry_after",))
        self._last_tick = time.monotonic()
        self._frame_after = self._top.after(delay, self._tick)

    def _tick(self) -> None:
        self._frame_after = None
        if self._top is None:
            return
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._last_tick))
        self._last_tick = now
        moving = False
        for item in [*self._all_items(), *self._leaving]:
            for spring in (item.y, item.size, item.opacity):
                if spring.settled:
                    spring.snap()
                else:
                    spring.step(dt)
                    moving = True
            if item.kind != "status" and now - item.born < 0.6:
                moving = True  # the check is still ticking itself
        self._leaving = [i for i in self._leaving if i.opacity.value > 0.02]
        self._paint(now)
        if moving or self._leaving:
            self._frame_after = self._top.after(FRAME_MS, self._tick)
            return
        if self._status is not None:
            # Only the sweep moves: a calmer frame rate is plenty.
            self._frame_after = self._top.after(SHIMMER_FRAME_MS, self._tick)
            if self._items:
                self._schedule_expiry()
            return
        if not self._items:
            self._withdraw()
            return
        self._schedule_expiry()

    def _all_items(self) -> list[_Item]:
        return ([self._status] if self._status is not None else []) + self._cards()

    def _schedule_expiry(self) -> None:
        if self._top is None or self._expiry_after is not None or not self._items:
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
                self._expanded = False
            self._retarget()
            self._kick()
            return
        self._schedule_expiry()

    # -- painting -------------------------------------------------------------

    def _paint(self, now: float) -> None:
        frame = self._blank()
        pad = self._pad
        width = frame.width
        # Back to front: the deepest card first, the thinking line last.
        order = [*reversed(self._cards()), *self._leaving]
        if self._status is not None:
            order.append(self._status)
        for item in order:
            opacity = max(0.0, min(1.0, item.opacity.value))
            if opacity <= 0.01:
                item.box = (0.0, 0.0, 0.0, 0.0)
                continue
            if item.kind == "status":
                step = int(now / pet_cards.SHIMMER_PERIOD_S * pet_cards.SHIMMER_STEPS)
                image = pet_cards.shimmer(self._card_image(item), step, round(self._scale, 3))
            else:
                ticked = int((now - item.born) / 0.5 * pet_cards.ICON_FRAMES)
                image = self._card_image(
                    item,
                    hovered=item.key == self._hovered and not item.leaving,
                    frame=min(pet_cards.ICON_FRAMES, ticked),
                    # Behind the front card: its edge only, no text showing
                    # through the see-through card in front.
                    content=item.size.value > 0.985 or item.leaving,
                )
            size = max(0.5, item.size.value)
            if abs(size - 1.0) > 0.004:
                image = image.resize(
                    (max(1, int(image.width * size)), max(1, int(image.height * size))),
                    Image.Resampling.BILINEAR,
                )
            if opacity < 0.995:
                image = image.copy()
                image.putalpha(image.getchannel("A").point(lambda v, o=opacity: int(v * o)))
            inset = pad * size
            x = (width - image.width) // 2
            y = int(round(item.y.value + pad - inset))
            frame.alpha_composite(image, (max(0, x), max(0, y)))
            item.box = (x + inset, y + inset, x + image.width - inset, y + image.height - inset)
        self._present(frame)

    def _present(self, frame: Image.Image) -> None:
        from ui.orb import overlay as _ov  # noqa: PLC0415 — overlay imports this module

        if self._alpha_hwnd:
            if layered_window.update_layered(self._alpha_hwnd, frame):
                return
            log.warning("per-pixel alpha refused; the pet's cards fall back to the colour key")
            self._alpha_hwnd = 0
            if self._top is not None:
                try:
                    self._top.wm_attributes("-transparentcolor", _ov.COLOR_KEY_HEX)
                except tk.TclError:
                    log.debug("colour-key fallback failed", exc_info=True)
        canvas = self._canvas
        if canvas is None:
            return
        key = (int(_ov.COLOR_KEY_RGB[0]), int(_ov.COLOR_KEY_RGB[1]), int(_ov.COLOR_KEY_RGB[2]))
        flat = pet_cards.flatten_for_key(frame, key)
        image = _ov.key_to_alpha(flat) if self._mac_transparent else flat
        try:
            self._photo = ImageTk.PhotoImage(image, master=canvas)
            if self._image_id is None:
                self._image_id = canvas.create_image(0, 0, anchor="nw", image=self._photo)
            else:
                canvas.itemconfigure(self._image_id, image=self._photo)
        except tk.TclError:
            log.debug("card frame paint failed", exc_info=True)

    # -- pointer --------------------------------------------------------------

    def _card_at(self, x: float, y: float) -> int | None:
        for item in self._cards():  # front-most first
            x0, y0, x1, y1 = item.box
            if x0 <= x <= x1 and y0 <= y <= y1 and item.opacity.value > 0.5:
                return item.key
        return None

    def _on_motion(self, event: tk.Event) -> None:
        hit = self._card_at(event.x, event.y)
        inside = hit is not None
        if inside and not self._pointer_inside:
            self._hover_since = time.monotonic()
        self._pointer_inside = inside
        if inside:
            self._cancel(("_collapse_after",))
            if not self._expanded and len(self._model.items) > 1:
                self._expanded = True
                self._retarget()
        elif self._expanded:
            self._schedule_collapse()
        if hit != self._hovered:
            self._hovered = hit
            if self._canvas is not None:
                try:
                    self._canvas.configure(cursor="hand2" if hit is not None else "")
                except tk.TclError:
                    log.debug("card cursor change failed", exc_info=True)
        self._kick()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._pointer_inside and self._hover_since is not None:
            self._model.pause(time.monotonic() - self._hover_since)
        self._pointer_inside = False
        self._hover_since = None
        self._hovered = None
        self._schedule_collapse()
        self._kick()

    def _on_click(self, event: tk.Event) -> None:
        hit = self._card_at(event.x, event.y)
        if hit is None:
            return
        self._model.dismiss(hit)
        self._send_off(hit)
        self._hovered = None
        if not self._model.items:
            self._expanded = False
        self._retarget()
        self._kick()

    def _schedule_collapse(self) -> None:
        if self._top is None or self._collapse_after is not None or not self._expanded:
            return
        self._collapse_after = self._top.after(COLLAPSE_GRACE_MS, self._collapse_now)

    def _collapse_now(self) -> None:
        self._collapse_after = None
        if not self._pointer_inside and self._expanded:
            self._expanded = False
            self._retarget()
            self._kick()

    # -- window ---------------------------------------------------------------

    def _place(self) -> None:
        if self._top is None or self._anchor is None:
            return
        center_x, below_y, above_y, _limit, screen_w = self._anchor
        width, height = self.window_size
        pad = self._pad
        x = max(8 - pad, min(center_x - width // 2, screen_w - width + pad - 8))
        y = above_y - height + pad if self._upward else below_y - pad
        try:
            self._top.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            log.debug("card window placement failed", exc_info=True)

    def _show(self) -> None:
        if self._top is None or self._shown:
            return
        self._place()
        try:
            self._top.deiconify()
            self._top.lift()
        except tk.TclError:
            log.debug("card window show failed", exc_info=True)
            return
        self._shown = True

    def _withdraw(self) -> None:
        self._cancel_all()
        self._expanded = False
        self._pointer_inside = False
        self._hovered = None
        if self._alpha_hwnd:
            # A layered window keeps its last bitmap; leave nothing behind.
            layered_window.update_layered(self._alpha_hwnd, self._blank())
        if self._top is not None and self._shown:
            try:
                self._top.withdraw()
            except tk.TclError:
                log.debug("card window withdraw failed", exc_info=True)
        self._shown = False

    def _cancel(self, names: tuple[str, ...]) -> None:
        top = self._top
        for attr in names:
            after_id = getattr(self, attr)
            if after_id is not None and top is not None:
                try:
                    top.after_cancel(after_id)
                except tk.TclError:
                    log.debug("card timer cancel failed", exc_info=True)
            setattr(self, attr, None)

    def _cancel_all(self) -> None:
        self._cancel(("_frame_after", "_expiry_after", "_collapse_after", "_status_clear_after"))


__all__ = [
    "NOTICE_KINDS",
    "Notice",
    "NoticeModel",
    "PetNoticeStack",
    "Slot",
    "Spring",
    "column_layout",
]
