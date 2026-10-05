"""PySide6 area picker for region appshots.

Runs ONLY inside ``python -m jarvis.appshot.picker``; the main process never
imports this module (AP-26). One frameless, always-on-top window per screen:

- **Frozen screen.** Each screen is grabbed once before the overlay appears
  and shown under a light dim, so nothing moves under the selection. Where the
  grab fails (no permission, odd platform) a translucent dim layer over the
  live desktop stands in.
- **Crosshair.** The pointer is a thin crosshair with a small ring in the
  middle; beside it, two small numbers — the position before the drag, the
  width and height while dragging. No lens, no labels in boxes, no banner.
- **Window snapping.** Hovering lifts the window under the pointer out of the
  dim (rectangles from the main process, top-most first); a click without a
  drag takes exactly that window.
- **Drag to select.** The screen around the selection
  clears and the selected area itself turns a translucent grey, with one thin
  border.

- **Mark up in place.** Releasing the drag does not take the
  shot yet: the selection keeps resize handles and a toolbar docks under it
  (:mod:`jarvis.appshot.picker.annotate`). The user draws boxes, arrows, text,
  steps, blur and so on right on the frozen frame; Enter or the check button
  takes the appshot with the markings, Ctrl+C / Ctrl+S / Ctrl+E also copy,
  save or open it in the full editor.

Only the parts that change are repainted, so the numbers follow the pointer
without dragging a full 4K repaint behind them. Esc or a right-click cancels.
The result is reported as fractions of its screen, so mixed-DPI layouts map
back to capture pixels exactly (see :mod:`jarvis.appshot.region`).
"""

from __future__ import annotations

import sys
import threading
from contextlib import suppress

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.appshot import picker as wire
from jarvis.appshot.picker import annotate
from jarvis.appshot.picker import markup_model as mm
from jarvis.appshot.region import match_monitor, selection_fractions, snap_rects_on_screen

#: A light veil before the drag (the picker is armed). While dragging the
#: veil lifts and the selection itself takes a neutral grey tint instead, so
#: the chosen area reads as "marked" on light and dark content alike.
_DIM_IDLE = QColor(0, 0, 0, 55)
_SELECTION_TINT = QColor(128, 128, 132, 105)
#: The selection border: a white hairline with a faint dark edge outside it,
#: so it reads on light and dark content alike.
_BORDER = QColor(255, 255, 255, 235)
_BORDER_EDGE = QColor(0, 0, 0, 70)

#: The numbers beside the crosshair: dark ink with a white halo.
_INK = QColor(20, 20, 22)
_HALO = QColor(255, 255, 255, 235)
_NUMBER_PT = 8.5
#: Offset of the numbers from the crosshair centre, and the screen-edge gap.
_NUMBER_OFFSET = QPointF(12.0, 10.0)
_EDGE_GAP = 4.0

#: Crosshair cursor geometry (logical px): arm length, ring radius.
_CROSS_ARM = 11
_CROSS_RING = 3.5

#: Windows smaller than this (logical px, either side) are not snap targets.
_MIN_SNAP_PX = 24

#: Cursor per selection resize handle.
_HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}
#: Cursor per marking grip kind (:func:`markup_model.grip_cursor`).
_GRIP_CURSORS = {
    "fdiag": Qt.CursorShape.SizeFDiagCursor,
    "bdiag": Qt.CursorShape.SizeBDiagCursor,
    "hor": Qt.CursorShape.SizeHorCursor,
    "grab": Qt.CursorShape.OpenHandCursor,
}
#: Arrow keys nudge the selected marking.
_NUDGE = {
    Qt.Key.Key_Left: (-1.0, 0.0),
    Qt.Key.Key_Right: (1.0, 0.0),
    Qt.Key.Key_Up: (0.0, -1.0),
    Qt.Key.Key_Down: (0.0, 1.0),
}
#: Keyboard shortcuts for the toolbar's actions (with Ctrl / Cmd).
_ACTION_KEYS = {
    Qt.Key.Key_C: wire.ACTION_COPY,
    Qt.Key.Key_S: wire.ACTION_SAVE,
    Qt.Key.Key_E: wire.ACTION_EDIT,
}


def _screen_info(screen) -> dict[str, float]:
    g = screen.geometry()
    return {
        "x": float(g.x()),
        "y": float(g.y()),
        "w": float(g.width()),
        "h": float(g.height()),
        "dpr": float(screen.devicePixelRatio() or 1.0),
    }


def _emit(payload: dict) -> None:
    # A failed write means the parent is gone; exiting is all that is left.
    with suppress(Exception):
        sys.stdout.write(wire.encode(payload))
        sys.stdout.flush()


def _number_font() -> QFont:
    font = QFont()
    font.setPointSizeF(_NUMBER_PT)
    font.setWeight(QFont.Weight.DemiBold)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


def crosshair_cursor(dpr: float) -> QCursor:
    """The crosshair: two thin arms with a ring at the centre.

    Drawn by the OS as the real cursor (no lag behind the mouse), at the
    screen's pixel density, black on a white halo so it reads everywhere.
    """
    arm = _CROSS_ARM
    side = arm * 2 + 3
    scale = max(1.0, float(dpr))
    pixmap = QPixmap(round(side * scale), round(side * scale))
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = side / 2.0
    gap = _CROSS_RING + 1.5
    segments = (
        (QPointF(c - arm, c), QPointF(c - gap, c)),
        (QPointF(c + gap, c), QPointF(c + arm, c)),
        (QPointF(c, c - arm), QPointF(c, c - gap)),
        (QPointF(c, c + gap), QPointF(c, c + arm)),
    )
    for colour, width in ((_HALO, 3.0), (_INK, 1.1)):
        pen = QPen(colour, width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for a, b in segments:
            painter.drawLine(a, b)
        painter.drawEllipse(QPointF(c, c), _CROSS_RING, _CROSS_RING)
    painter.end()
    hot = round(c)
    return QCursor(pixmap, hot, hot)


class _MarkupState:
    """Everything the annotation mode of one screen window holds."""

    def __init__(self) -> None:
        #: The chosen area (logical px); ``None`` while still choosing.
        self.sel: QRectF | None = None
        self.shapes: list[mm.Shape] = []
        self.history = mm.History()
        self.tool = mm.ARROW
        self.color = mm.PALETTE[0]
        self.width_index = mm.DEFAULT_WIDTH_INDEX
        self.arrow_style = mm.ARROW_STYLES[0]
        self.text_style = mm.TEXT_STYLES[0]
        self.redact_mode = mm.REDACT_MODES[0]
        #: The background frame (preset, padding, radius, shadow), or ``None``.
        self.background: dict | None = None
        #: The marking being dragged out right now.
        self.drawing: mm.Shape | None = None
        #: The text being typed (not in ``shapes`` while open); where an edited
        #: text came from, and how it was, so Esc can put it back.
        self.typing: mm.Shape | None = None
        self.typing_index: int | None = None
        self.typing_original: mm.Shape | None = None
        #: Index into ``shapes`` of the selected marking (grips shown).
        self.selected: int | None = None
        #: ``("area", handle)`` / ``("grip", grip)`` / ``("move", None)``.
        self.drag: tuple[str, str | None] | None = None
        #: The selected marking as it was when a grip drag began.
        self.original: mm.Shape | None = None
        self.last: QPointF | None = None
        self.moved = False

    @property
    def width(self) -> float:
        return mm.WIDTHS[self.width_index]

    def box(self) -> mm.Box:
        sel = self.sel
        assert sel is not None
        return (sel.x(), sel.y(), sel.width(), sel.height())

    def selected_shape(self) -> mm.Shape | None:
        if self.selected is None or not 0 <= self.selected < len(self.shapes):
            return None
        return self.shapes[self.selected]


class _SelectWindow(QWidget):
    """One screen's selection canvas."""

    def __init__(self, screen, frozen: QPixmap | None, owner: Picker) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if frozen is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if sys.platform == "darwin":
            # Qt::Tool is an NSPanel on macOS; without this it stays invisible
            # while this never-activated process is in the background.
            always_show = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
            if always_show is not None:
                self.setAttribute(always_show)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(crosshair_cursor(screen.devicePixelRatio() or 1.0))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.screen_ref = screen
        self._frozen = frozen
        self._owner = owner
        self._start: QPointF | None = None
        self._end: QPointF | None = None
        self._pointer: QPointF | None = None
        #: Snap targets in this window's logical coordinates, top-most first.
        self._snaps: list[QRectF] = []
        self._monitor: dict | None = None
        self._font = _number_font()
        self._metrics = QFontMetricsF(self._font)
        #: What was painted last, so the next move repaints only what changed.
        self._painted_numbers: QRectF | None = None
        self._painted_hole: QRectF | None = None
        self._cross = crosshair_cursor(screen.devicePixelRatio() or 1.0)
        self._markup = _MarkupState()
        self._patches = annotate.HidePatches(frozen, self._scale)
        self._toolbar: annotate.Toolbar | None = None

    # -- data from the main process ------------------------------------------
    def set_layout(self, monitors: list[dict], windows: list[list[int]]) -> None:
        """Map capture-space window rects onto this screen's logical pixels."""
        info = _screen_info(self.screen_ref)
        self._monitor = match_monitor(info, monitors)
        rects = snap_rects_on_screen(
            info, (self.width(), self.height()), monitors, windows, min_px=_MIN_SNAP_PX
        )
        self._snaps = [QRectF(*rect) for rect in rects]
        self.update()

    # -- input ---------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        marking = self._owner.marking
        if marking is not None and marking is not self:
            marking.keyPressEvent(event)
            return
        if self._markup.sel is not None:
            self._markup_key(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._owner.finish(None, None)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._owner.marking is not None and self._owner.marking is not self:
            return
        if self._markup.sel is not None:
            self._markup_press(event)
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._owner.finish(None, None)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position()
            self._end = event.position()
            self._owner.focus_on(self)
            # The veil lifts for the drag: one full repaint.
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._owner.marking is not None:
            if self._owner.marking is self:
                self._markup_move(event)
            return
        self._pointer = event.position()
        if self._start is not None:
            self._end = event.position()
        self._owner.focus_on(self)
        self._repaint_changes()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._owner.marking is not None:
            if self._owner.marking is self:
                self._markup_release(event)
            return
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        end = event.position()
        frac = selection_fractions(
            self._start.x(), self._start.y(), end.x(), end.y(), self.width(), self.height()
        )
        self._start = None
        self._end = None
        if frac is None:
            # A click, not a drag: take the window under the pointer, if any.
            snap = self._snap_at(end)
            if snap is not None:
                frac = (
                    snap.x() / self.width(),
                    snap.y() / self.height(),
                    snap.width() / self.width(),
                    snap.height() / self.height(),
                )
        if frac is None:
            self.update()
            return
        fx, fy, fw, fh = frac
        self._begin_markup(
            QRectF(fx * self.width(), fy * self.height(), fw * self.width(), fh * self.height())
        )

    def clear_pointer(self) -> None:
        if self._pointer is not None or self._start is not None:
            self._pointer = None
            self._start = None
            self._end = None
            self.update()

    # -- geometry ------------------------------------------------------------
    def _selection(self) -> QRectF | None:
        if self._start is None or self._end is None:
            return None
        return QRectF(self._start, self._end).normalized()

    def _snap_at(self, point: QPointF) -> QRectF | None:
        for rect in self._snaps:
            if rect.contains(point):
                return rect
        return None

    def _hole(self) -> QRectF | None:
        sel = self._selection()
        if sel is not None:
            return sel
        if self._pointer is not None:
            return self._snap_at(self._pointer)
        return None

    def _scale(self) -> float:
        """Device pixels per logical pixel — from the frozen frame when present."""
        if self._frozen is not None and self.width() > 0:
            return self._frozen.width() / self.width()
        return float(self.screen_ref.devicePixelRatio() or 1.0)

    def _capture_point(self, point: QPointF) -> tuple[int, int]:
        """``point`` in capture coordinates (what the screenshot will use)."""
        mon = self._monitor
        if mon is None:
            scale = self._scale()
            return round(point.x() * scale), round(point.y() * scale)
        fx = point.x() / max(1.0, self.width())
        fy = point.y() / max(1.0, self.height())
        return (
            round(float(mon.get("left", 0)) + fx * float(mon.get("width", 0))),
            round(float(mon.get("top", 0)) + fy * float(mon.get("height", 0))),
        )

    def _numbers(self) -> tuple[str, str] | None:
        """The two stacked numbers: position, or width and height while dragging."""
        sel = self._selection()
        if sel is not None:
            scale = self._scale()
            return str(round(sel.width() * scale)), str(round(sel.height() * scale))
        if self._pointer is None:
            return None
        x, y = self._capture_point(self._pointer)
        return str(x), str(y)

    def _numbers_rect(self) -> QRectF | None:
        """Where the numbers go: below-right of the crosshair, flipped at edges."""
        point = self._end if self._end is not None else self._pointer
        numbers = self._numbers()
        if point is None or numbers is None:
            return None
        w = max(self._metrics.horizontalAdvance(n) for n in numbers) + 4.0
        line = self._metrics.height()
        h = line * 2 + 2.0
        x = point.x() + _NUMBER_OFFSET.x()
        y = point.y() + _NUMBER_OFFSET.y()
        if x + w > self.width() - _EDGE_GAP:
            x = point.x() - _NUMBER_OFFSET.x() - w
        if y + h > self.height() - _EDGE_GAP:
            y = point.y() - _NUMBER_OFFSET.y() - h
        return QRectF(round(x), round(y), round(w), round(h))

    def _repaint_changes(self) -> None:
        """Repaint the old and new numbers, and the hole only when it changed."""
        numbers = self._numbers_rect()
        hole = self._hole()
        dirty = QRectF()
        for part in (numbers, self._painted_numbers):
            if part is not None:
                dirty = dirty.united(part.adjusted(-3, -3, 3, 3))
        if hole != self._painted_hole:
            for part in (hole, self._painted_hole):
                if part is not None:
                    dirty = dirty.united(part.adjusted(-3, -3, 3, 3))
        self._painted_numbers = numbers
        self._painted_hole = hole
        if not dirty.isEmpty():
            self.update(dirty.toAlignedRect())

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setClipRect(event.rect())
        full = QRectF(0, 0, self.width(), self.height())
        if self._frozen is not None:
            painter.drawPixmap(full, self._frozen, QRectF(self._frozen.rect()))
        if self._markup.sel is not None:
            self._paint_markup(painter, full)
            painter.end()
            return
        hole = self._hole()
        if self._selection() is not None and hole is not None:
            # Dragging: the surroundings stay clear, the selection turns grey.
            painter.fillRect(hole, _SELECTION_TINT)
        else:
            dim = QPainterPath()
            dim.addRect(full)
            if hole is not None:
                cut = QPainterPath()
                cut.addRect(hole)
                dim = dim.subtracted(cut)
            painter.fillPath(dim, _DIM_IDLE)
        if hole is not None:
            self._paint_border(painter, hole)
        self._paint_numbers(painter)
        painter.end()

    def _paint_border(self, painter: QPainter, rect: QRectF) -> None:
        # Drawn without antialiasing so both hairlines land on whole pixels.
        r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_BORDER_EDGE, 1.0))
        painter.drawRect(r.adjusted(-1.0, -1.0, 1.0, 1.0))
        painter.setPen(QPen(_BORDER, 1.0))
        painter.drawRect(r)

    def _paint_numbers(self, painter: QPainter) -> None:
        numbers = self._numbers()
        box = self._numbers_rect()
        if numbers is None or box is None:
            return
        line = self._metrics.height()
        ascent = self._metrics.ascent()
        path = QPainterPath()
        for i, text in enumerate(numbers):
            # Right-aligned in their column.
            x = box.right() - 2.0 - self._metrics.horizontalAdvance(text)
            path.addText(QPointF(x, box.top() + 1.0 + ascent + i * line), self._font, text)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        halo = QPen(_HALO, 2.6)
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, halo)
        painter.fillPath(path, _INK)
        painter.restore()

    # -- marking up the chosen area ------------------------------------------
    def _begin_markup(self, sel: QRectF) -> None:
        """The area is chosen: keep the overlay and dock the toolbar under it."""
        m = self._markup
        m.sel = sel
        self._pointer = None
        self._painted_numbers = None
        self._painted_hole = None
        if self._toolbar is None:
            bar = annotate.Toolbar(
                self, language=self._owner.language, dpr=self.screen_ref.devicePixelRatio() or 1.0
            )
            bar.tool_chosen.connect(self._set_tool)
            bar.color_chosen.connect(self._set_color)
            bar.width_cycled.connect(lambda: self._step_width(1, wrap=True))
            bar.action.connect(self._toolbar_action)
            bar.option_chosen.connect(self._set_option)
            self._toolbar = bar
        self._toolbar.set_tool(m.tool)
        self._toolbar.set_color(m.color)
        self._toolbar.set_width(m.width)
        self._sync_options()
        self._sync_history()
        self._place_toolbar()
        self._toolbar.show()
        self._toolbar.raise_()
        self._owner.begin_markup(self)
        self._update_cursor(None)
        self.update()

    def _end_markup(self) -> None:
        """Back to choosing an area (right-click on an unmarked selection)."""
        self._markup = _MarkupState()
        if self._toolbar is not None:
            self._toolbar.hide()
        self._owner.end_markup(self)
        self.setCursor(self._cross)
        self.update()

    def _place_toolbar(self) -> None:
        bar = self._toolbar
        if bar is None or self._markup.sel is None:
            return
        bar.adjustSize()
        x, y = mm.toolbar_origin(
            self._markup.box(), (bar.width(), bar.height()), (self.width(), self.height())
        )
        bar.move(round(x), round(y))

    def _sync_history(self) -> None:
        if self._toolbar is not None:
            h = self._markup.history
            self._toolbar.set_history(h.can_undo, h.can_redo)

    def _sync_options(self) -> None:
        """The looks shown for the current tool (or the selected marking's kind)."""
        bar = self._toolbar
        if bar is None:
            return
        m = self._markup
        kind = m.tool
        selected = m.selected_shape()
        if kind == mm.MOVE and selected is not None:
            kind = selected.kind
        if kind == mm.ARROW:
            style = selected.style if selected is not None and selected.kind == mm.ARROW else ""
            options = [(s, f"arrow_{s}") for s in mm.ARROW_STYLES]
            bar.set_options(options, style or m.arrow_style, "arrow")
        elif kind == mm.TEXT:
            style = selected.style if selected is not None and selected.kind == mm.TEXT else ""
            options = [(s, f"text_{s}") for s in mm.TEXT_STYLES]
            bar.set_options(options, style or m.text_style, "text")
        elif kind == mm.REDACT:
            mode = selected.mode if selected is not None and selected.kind == mm.REDACT else ""
            options = [(s, f"redact_{s}") for s in mm.REDACT_MODES]
            bar.set_options(options, mode or m.redact_mode, "redact")
        elif kind == mm.BACKGROUND:
            options = [("off", "background_off")]
            options += [(preset, "preset") for preset, _stops in mm.BACKGROUND_PRESETS]
            chosen = m.background["preset"] if m.background else "off"
            bar.set_options(options, chosen, "background")
        else:
            bar.set_options([], "")
        self._place_toolbar()

    def _commit(self) -> None:
        self._markup.history.push(self._markup.shapes)
        self._sync_history()

    def _set_tool(self, kind: str) -> None:
        self._commit_typing()
        m = self._markup
        m.tool = kind
        if mm.grab_scope(kind) == "none":
            m.selected = None
        if kind == mm.BACKGROUND and m.background is None:
            m.background = dict(mm.DEFAULT_BACKGROUND)
        if self._toolbar is not None:
            self._toolbar.set_tool(kind)
        self._sync_options()
        self._update_cursor(None)
        self.update()

    def _set_color(self, colour: str) -> None:
        m = self._markup
        m.color = colour
        target = m.typing or m.selected_shape()
        if target is not None:
            target.color = colour
            if target is not m.typing:
                self._commit()
        if self._toolbar is not None:
            self._toolbar.set_color(colour)
        self.update()

    def _step_width(self, step: int, *, wrap: bool = False, to: int | None = None) -> None:
        m = self._markup
        count = len(mm.WIDTHS)
        if to is not None:
            m.width_index = max(0, min(count - 1, to))
        else:
            index = m.width_index + step
            m.width_index = index % count if wrap else max(0, min(count - 1, index))
        target = m.typing or m.selected_shape()
        if target is not None:
            target.width = m.width
            if target.kind == mm.TEXT:
                target.size = mm.text_size(m.width)
            elif target.kind == mm.COUNTER:
                target.size = mm.counter_size(m.width)
            if target is not m.typing:
                self._commit()
        if self._toolbar is not None:
            self._toolbar.set_width(m.width)
        self.update()

    def _set_option(self, group: str, value: str) -> None:
        m = self._markup
        selected = m.selected_shape()
        changed = False
        if group == "arrow":
            m.arrow_style = value
            if selected is not None and selected.kind == mm.ARROW:
                selected.style, changed = value, True
        elif group == "text":
            m.text_style = value
            target = m.typing or (selected if selected and selected.kind == mm.TEXT else None)
            if target is not None:
                target.style = value
                changed = target is not m.typing
        elif group == "redact":
            m.redact_mode = value
            if selected is not None and selected.kind == mm.REDACT:
                selected.mode, changed = value, True
        elif group == "background":
            if value == "off":
                m.background = None
            else:
                m.background = {**(m.background or mm.DEFAULT_BACKGROUND), "preset": value}
        if changed:
            self._commit()
        self._sync_options()
        self.update()

    def _toolbar_action(self, name: str) -> None:
        if name == "undo":
            self._undo(redo=False)
        elif name == "redo":
            self._undo(redo=True)
        elif name == "cancel":
            self._owner.finish(None, None)
        elif name in wire.ACTIONS:
            self._deliver(name)

    def _undo(self, *, redo: bool) -> None:
        m = self._markup
        m.typing = None
        m.typing_index = None
        state = m.history.redo() if redo else m.history.undo()
        if state is not None:
            m.shapes = state
            m.selected = None
        self._sync_history()
        self._sync_options()
        self.update()

    def _deliver(self, action: str) -> None:
        """Take the appshot: the area plus the markings, then close."""
        self._commit_typing()
        m = self._markup
        sel = m.sel
        if sel is None:
            return
        frac = (
            sel.x() / self.width(),
            sel.y() / self.height(),
            sel.width() / self.width(),
            sel.height() / self.height(),
        )
        markup: dict | None = None
        if m.shapes or m.background:
            markup = {
                "overlay": annotate.render_overlay(m.shapes, sel, self._scale())
                if m.shapes
                else "",
                "hides": mm.hide_fractions(m.shapes, m.box()),
            }
            if m.background:
                markup["background"] = dict(m.background)
        self._owner.finish(self, frac, action=action, markup=markup)

    def _start_typing(self, at: mm.Point, *, index: int | None = None) -> None:
        """Open the text box: a new one at ``at``, or the text at ``index``."""
        m = self._markup
        if index is not None:
            m.typing = m.shapes.pop(index)
            m.typing_index = index
            m.typing_original = m.typing.copy()
        else:
            size = mm.text_size(m.width)
            m.typing = mm.Shape(
                mm.TEXT,
                color=m.color,
                width=m.width,
                size=size,
                style=m.text_style,
                points=[(at[0], at[1] - size * 0.62)],
            )
            m.typing_index = None
        m.selected = None
        self._sync_options()
        self.update()

    def _commit_typing(self, *, cancel: bool = False) -> None:
        m = self._markup
        shape, index = m.typing, m.typing_index
        if shape is None:
            return
        m.typing, m.typing_index = None, None
        original, m.typing_original = m.typing_original, None
        if cancel:
            if index is not None and original is not None:
                # Esc on an edited text keeps it as it was.
                m.shapes.insert(min(index, len(m.shapes)), original)
            self.update()
            return
        shape.text = shape.text.rstrip()
        if mm.is_meaningful(shape):
            at = min(index, len(m.shapes)) if index is not None else len(m.shapes)
            m.shapes.insert(at, shape)
            # A finished text shows its points, like every fresh marking.
            m.selected = at
            self._commit()
            self._sync_options()
        elif index is not None:
            self._commit()  # emptied: the text is gone
        self.update()

    def _text_at(self, point: mm.Point) -> int | None:
        index = mm.shape_at(self._markup.shapes, point, annotate.qt_measure, areas=False)
        if index is not None and self._markup.shapes[index].kind == mm.TEXT:
            return index
        return None

    def _grabbable(self, point: mm.Point) -> int | None:
        """The marking a press at ``point`` would move with the current tool."""
        m = self._markup
        scope = mm.grab_scope(m.tool)
        if scope in ("none", "grips"):
            return None
        index = mm.shape_at(m.shapes, point, annotate.qt_measure, areas=scope == "any")
        if index is None or (m.tool == mm.TEXT and m.shapes[index].kind == mm.TEXT):
            return None
        return index

    def _markup_key(self, event) -> None:  # noqa: C901 - one key table
        m = self._markup
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if m.typing is not None:
            self._typing_key(event, key, mods, ctrl)
            return
        if key == Qt.Key.Key_Escape:
            if m.selected is not None:
                m.selected = None
                self._sync_options()
                self.update()
            else:
                self._owner.finish(None, None)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._deliver(wire.ACTION_DONE)
        elif ctrl and key == Qt.Key.Key_Z:
            self._undo(redo=bool(mods & Qt.KeyboardModifier.ShiftModifier))
        elif ctrl and key == Qt.Key.Key_Y:
            self._undo(redo=True)
        elif ctrl and key in _ACTION_KEYS:
            self._deliver(_ACTION_KEYS[key])
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and m.selected is not None:
            del m.shapes[m.selected]
            m.selected = None
            self._commit()
            self._sync_options()
            self.update()
        elif key in _NUDGE and m.selected is not None:
            dx, dy = _NUDGE[key]
            step = 10.0 if mods & Qt.KeyboardModifier.ShiftModifier else 1.0
            m.shapes[m.selected] = mm.translated(m.shapes[m.selected], dx * step, dy * step)
            self._commit()
            self.update()
        elif not ctrl and Qt.Key.Key_1 <= key <= Qt.Key.Key_5:
            self._step_width(0, to=int(key) - int(Qt.Key.Key_1))
        elif not ctrl and event.text():
            letter = event.text().upper()
            for kind, shortcut in mm.TOOL_KEYS.items():
                if shortcut == letter:
                    self._set_tool(kind)
                    break

    def _typing_key(self, event, key, mods, ctrl: bool) -> None:
        shape = self._markup.typing
        assert shape is not None
        if key == Qt.Key.Key_Escape:
            self._commit_typing(cancel=True)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ShiftModifier:
                shape.text += "\n"
            else:
                self._commit_typing()
                return
        elif key == Qt.Key.Key_Backspace:
            shape.text = shape.text[:-1]
        elif ctrl and key == Qt.Key.Key_V:
            shape.text += QGuiApplication.clipboard().text()
        elif not ctrl and event.text() and event.text().isprintable():
            shape.text += event.text()
        self.update()

    def _markup_press(self, event) -> None:  # noqa: C901 - one press table
        m = self._markup
        p = event.position()
        point = (p.x(), p.y())
        if event.button() == Qt.MouseButton.RightButton:
            if m.drawing is not None:
                m.drawing = None
            elif m.typing is not None:
                self._commit_typing(cancel=True)
            elif not m.shapes:
                self._end_markup()
                return
            self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._commit_typing()
        selected = m.selected_shape()
        if selected is not None and mm.grab_scope(m.tool) != "none":
            grip = mm.grip_at(selected, point, mm.HANDLE_GRAB_PX + 1, annotate.qt_measure)
            if grip is not None:
                m.drag, m.original, m.moved = ("grip", grip), selected.copy(), False
                return
        handle = mm.handle_at(m.box(), point)
        if handle is not None:
            m.drag = ("area", handle)
            return
        grabbed = self._grabbable(point)
        if grabbed is not None:
            m.selected = grabbed
            m.drag, m.last, m.moved = ("move", None), p, False
            self._sync_options()
            self.update()
            return
        if m.selected is not None:
            m.selected = None
            self._sync_options()
        inside = m.sel is not None and m.sel.contains(p)
        if m.tool in (mm.MOVE, mm.BACKGROUND):
            if not inside and not m.shapes:
                self._end_markup()
                self.mousePressEvent(event)
                return
            self.update()
            return
        if not inside:
            if not m.shapes:
                # A press outside an unmarked area starts choosing again.
                self._end_markup()
                self.mousePressEvent(event)
            return
        if m.tool == mm.TEXT:
            index = self._text_at(point)
            self._start_typing(point, index=index)
            return
        if m.tool == mm.COUNTER:
            m.shapes.append(
                mm.Shape(
                    mm.COUNTER,
                    color=m.color,
                    width=m.width,
                    size=mm.counter_size(m.width),
                    points=[point],
                    number=mm.next_counter(m.shapes),
                )
            )
            m.selected = len(m.shapes) - 1
            self._commit()
        elif m.tool in mm.STROKE_KINDS:
            m.drawing = mm.Shape(m.tool, color=m.color, width=m.width, points=[point])
        else:
            m.drawing = mm.Shape(
                m.tool,
                color=m.color,
                width=m.width,
                points=[point, point],
                style=m.arrow_style if m.tool == mm.ARROW else "",
                mode=m.redact_mode if m.tool == mm.REDACT else "",
            )
        self._update_markup_area()

    def _markup_move(self, event) -> None:
        m = self._markup
        p = event.position()
        point = (p.x(), p.y())
        drag = m.drag
        if drag is not None and drag[0] == "area":
            sel = mm.resize(m.box(), drag[1] or "se", point, (self.width(), self.height()))
            m.sel = QRectF(*sel)
            self._place_toolbar()
            self.update()
            return
        if drag is not None and drag[0] == "grip" and m.selected is not None and m.original:
            m.shapes[m.selected] = mm.reshape(
                m.original, drag[1] or "se", point, annotate.qt_measure
            )
            m.moved = True
            self._update_markup_area()
            return
        if drag is not None and drag[0] == "move" and m.selected is not None and m.last:
            delta = p - m.last
            m.shapes[m.selected] = mm.translated(m.shapes[m.selected], delta.x(), delta.y())
            m.last, m.moved = p, True
            self._update_markup_area()
            return
        shape = m.drawing
        if shape is not None:
            if shape.kind in mm.STROKE_KINDS:
                lx, ly = shape.points[-1]
                if abs(point[0] - lx) + abs(point[1] - ly) >= 1.5:
                    shape.points.append(point)
            else:
                end = point
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    end = mm.constrain(shape.kind, shape.points[0], point)
                shape.points[-1] = end
            self._update_markup_area()
            return
        self._update_cursor(point)

    def _markup_release(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        m = self._markup
        drag, m.drag = m.drag, None
        m.original = None
        if drag is not None and drag[0] in ("move", "grip") and m.moved:
            self._commit()
        shape, m.drawing = m.drawing, None
        if shape is not None and mm.is_meaningful(shape):
            m.shapes.append(shape)
            self._commit()
            # Every fresh marking shows its points at once (the previous one
            # loses them), so it can be moved and reshaped right away.
            m.selected = len(m.shapes) - 1
            self._sync_options()
        self._update_markup_area()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Double-click a text with the select or text tool to change it."""
        m = self._markup
        if m.sel is None or m.typing is not None or m.tool not in (mm.MOVE, mm.TEXT):
            return
        p = event.position()
        index = self._text_at((p.x(), p.y()))
        if index is not None:
            m.drag = None
            self._start_typing((p.x(), p.y()), index=index)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._markup.sel is None:
            return
        steps = event.angleDelta().y()
        if steps:
            self._step_width(1 if steps > 0 else -1)

    def _update_cursor(self, point: mm.Point | None) -> None:
        m = self._markup
        if point is not None:
            selected = m.selected_shape()
            if selected is not None and mm.grab_scope(m.tool) != "none":
                grip = mm.grip_at(selected, point, mm.HANDLE_GRAB_PX + 1, annotate.qt_measure)
                if grip is not None:
                    self.setCursor(_GRIP_CURSORS[mm.grip_cursor(grip)])
                    return
            handle = mm.handle_at(m.box(), point)
            if handle is not None:
                self.setCursor(_HANDLE_CURSORS[handle])
                return
            if self._grabbable(point) is not None:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
        if m.tool == mm.TEXT:
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif m.tool in (mm.MOVE, mm.BACKGROUND):
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(self._cross)

    def _update_markup_area(self) -> None:
        """Repaint the selection (markings are clipped to it) and its frame."""
        if self._markup.sel is None:
            self.update()
            return
        self.update(self._markup.sel.adjusted(-40, -40, 40, 40).toAlignedRect())

    def _paint_markup(self, painter: QPainter, full: QRectF) -> None:
        m = self._markup
        sel = m.sel
        assert sel is not None
        dim = QPainterPath()
        dim.addRect(full)
        cut = QPainterPath()
        cut.addRect(sel)
        painter.fillPath(dim.subtracted(cut), annotate.DIM_OUTSIDE)
        if self._frozen is None:
            # Live overlay: a fully clear pixel lets clicks fall through to the
            # window below, so the area keeps an invisible film to draw on.
            painter.fillRect(sel, QColor(0, 0, 0, 1))
        if m.background:
            annotate.paint_background_frame(painter, sel, m.background)
        live = [*m.shapes, *(s for s in (m.drawing,) if s is not None)]
        painter.save()
        painter.setClipRect(sel, Qt.ClipOperation.IntersectClip)
        for shape in (s for s in live if s.kind == mm.REDACT):
            annotate.paint_shape(painter, shape, hide_source=self._patches)
        for shape in (s for s in live if s.kind != mm.REDACT):
            annotate.paint_shape(painter, shape)
        annotate.paint_spotlights(painter, live, sel)
        painter.restore()
        if m.typing is not None:
            labels = annotate.labels_for(self._owner.language)
            annotate.paint_typing(painter, m.typing, labels["placeholder"], caret=True)
        selected = m.selected_shape()
        if selected is not None:
            annotate.paint_picked(painter, selected)
        annotate.paint_selection_frame(painter, sel)
        self._paint_size(painter, sel)

    def _paint_size(self, painter: QPainter, sel: QRectF) -> None:
        """``W x H`` in capture pixels, just above the area's top-left corner."""
        scale = self._scale()
        text = f"{round(sel.width() * scale)} × {round(sel.height() * scale)}"
        ascent = self._metrics.ascent()
        y = sel.top() - 6.0
        if y - ascent < _EDGE_GAP:
            y = sel.top() + ascent + 6.0
        path = QPainterPath()
        path.addText(QPointF(sel.left() + 1.0, y), self._font, text)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        halo = QPen(_HALO, 2.6)
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, halo)
        painter.fillPath(path, _INK)
        painter.restore()


class Picker(QObject):
    """Owns the per-screen windows and reports exactly one result."""

    def __init__(self, app: QApplication, *, language: str = "en") -> None:
        super().__init__()
        self._app = app
        self.language = language
        self._windows: list[_SelectWindow] = []
        self._focused: _SelectWindow | None = None
        #: The window whose area is being marked up; the others stay inert.
        self.marking: _SelectWindow | None = None
        self._done = False

    def start(self) -> None:
        screens = QGuiApplication.screens()
        # Freeze every screen BEFORE any overlay window exists.
        frozen = {id(s): self._grab(s) for s in screens}
        self._windows = [self.make_window(s, frozen[id(s)]) for s in screens]
        cursor_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self._focused = next(
            (w for w in self._windows if w.screen_ref is cursor_screen),
            self._windows[0] if self._windows else None,
        )
        for win in self._windows:
            win.show()
        if self._focused is not None:
            self._focused.raise_()
            self._focused.activateWindow()
            self._focused.setFocus()
        self._app.processEvents()
        _emit({"event": wire.EVENT_READY})

    def make_window(self, screen, frozen: QPixmap | None) -> _SelectWindow:
        """Allow capture modes to provide their own selection completion behavior."""
        return _SelectWindow(screen, frozen, self)

    @staticmethod
    def _grab(screen) -> QPixmap | None:
        try:
            pixmap = screen.grabWindow(0)
        except Exception:  # noqa: BLE001 - no frozen frame: fall back to a live dim layer
            sys.stderr.write("appshot-picker: screen grab failed; using a live overlay\n")
            return None
        return None if pixmap.isNull() or pixmap.width() <= 0 else pixmap

    def set_layout(self, monitors: list[dict], windows: list[list[int]]) -> None:
        for win in self._windows:
            win.set_layout(monitors, windows)

    def focus_on(self, window: _SelectWindow) -> None:
        """The pointer moved onto ``window``: only that screen shows the numbers."""
        if self._focused is window or self.marking is not None:
            return
        for other in self._windows:
            if other is not window:
                other.clear_pointer()
        self._focused = window

    def begin_markup(self, window: _SelectWindow) -> None:
        """An area is chosen on ``window``: the toolbar is up, keys go there."""
        self.marking = window
        self._focused = window
        for other in self._windows:
            if other is not window:
                other.clear_pointer()
        window.raise_()
        window.activateWindow()
        window.setFocus()
        # The parent drops its global Esc now: Esc may end a text box here.
        _emit({"event": wire.EVENT_MARKING})

    def end_markup(self, window: _SelectWindow) -> None:
        if self.marking is window:
            self.marking = None

    def finish(
        self,
        window: _SelectWindow | None,
        frac,
        *,
        action: str = "done",
        markup: dict | None = None,
    ) -> None:
        if self._done:
            return
        self._done = True
        info = _screen_info(window.screen_ref) if window is not None else None
        for win in self._windows:
            win.hide()
        # The overlay must be off the glass before the parent grabs.
        self._app.processEvents()
        if info is None or frac is None:
            _emit({"event": wire.EVENT_SELECTION, "cancelled": True})
        else:
            payload: dict = {
                "event": wire.EVENT_SELECTION,
                "screen": info,
                "rect": list(frac),
                "action": action,
            }
            if markup is not None:
                payload["markup"] = markup
            _emit(payload)
        self._app.quit()

    @Slot(str)
    def on_line(self, raw: str) -> None:
        payload = wire.decode(raw)
        if payload is None:
            return
        cmd = payload.get("cmd")
        if cmd == wire.CMD_CANCEL:
            self.finish(None, None)
        elif cmd == wire.CMD_LAYOUT:
            monitors = payload.get("monitors")
            windows = payload.get("windows")
            if isinstance(monitors, list) and isinstance(windows, list):
                self.set_layout([m for m in monitors if isinstance(m, dict)], windows)

    @Slot()
    def on_eof(self) -> None:
        self.finish(None, None)


class _StdinPump(QObject):
    """Reads stdin on a daemon thread; signals deliver to the Qt thread."""

    line = Signal(str)
    eof = Signal()

    def start(self) -> None:
        threading.Thread(target=self._run, name="appshot-picker-stdin", daemon=True).start()

    def _run(self) -> None:
        # A dying pipe simply means "parent gone" — treated as EOF.
        with suppress(Exception):
            for raw in sys.stdin:
                self.line.emit(raw)
        self.eof.emit()


def run(language: str = "en") -> int:
    """Picker main loop. Returns the process exit code."""
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    try:
        app = QApplication(sys.argv[:1] or ["appshot-picker"])
    except Exception as exc:  # noqa: BLE001 - no display / no platform plugin
        sys.stderr.write(f"appshot-picker: no usable display ({exc!r}).\n")
        return wire.EXIT_NO_GUI
    from jarvis.platform.qt_sidecar import hide_from_dock

    hide_from_dock()  # macOS: no "Python" Dock icon for the resident picker
    app.setQuitOnLastWindowClosed(False)
    picker = Picker(app, language=language)
    pump = _StdinPump()
    pump.line.connect(picker.on_line, Qt.ConnectionType.QueuedConnection)
    pump.eof.connect(picker.on_eof, Qt.ConnectionType.QueuedConnection)
    picker.start()
    pump.start()
    return app.exec()


__all__ = ["Picker", "crosshair_cursor", "run"]
