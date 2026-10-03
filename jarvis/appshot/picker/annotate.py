"""Mark up a chosen area in place with the picker's annotation tools.

Runs ONLY inside ``python -m jarvis.appshot.picker`` (PySide6, AP-26). Once
an area is chosen the overlay stays up: a toolbar docks under the selection
and the user draws on the frozen frame right there — boxes, circles, arrows,
lines, pen and highlighter strokes, text, numbered steps, blur and pixelate.
Enter (or the check button) takes the appshot WITH the markings, so the
assistant sees exactly what was pointed at.

The rules live in :mod:`jarvis.appshot.picker.markup_model` (plain data);
this module paints them, draws the toolbar and turns input into model calls.
The markings leave the process as a transparent PNG overlay plus the
blur/pixelate rectangles; the main process applies both to the real,
privacy-filtered capture (:mod:`jarvis.appshot.markup`).
"""

from __future__ import annotations

import base64
import math
from collections.abc import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QToolButton,
    QWidget,
)

from jarvis.appshot.picker import markup_model as mm

#: The world outside the selection while marking up: darker than the idle
#: veil, so the eye stays on the area being marked.
DIM_OUTSIDE = QColor(0, 0, 0, 120)
_HANDLE_FILL = QColor(255, 255, 255)
_HANDLE_EDGE = QColor(0, 0, 0, 110)
_HANDLE_R = 4.0
#: Logical px per pixelate block, and the blur's downscale factor.
_PIXEL_BLOCK = 9.0
_BLUR_FACTOR = 12.0
_HIGHLIGHT_ALPHA = 105

# Toolbar wording — app chrome, so it follows [ui].language like the card.
_LABELS: dict[str, dict[str, str]] = {
    "en": {
        mm.MOVE: "Select and move",
        mm.RECT: "Rectangle",
        mm.ELLIPSE: "Ellipse",
        mm.ARROW: "Arrow",
        mm.LINE: "Line",
        mm.PEN: "Pen",
        mm.HIGHLIGHT: "Highlighter",
        mm.TEXT: "Text",
        mm.COUNTER: "Numbered step",
        mm.BLUR: "Blur",
        mm.PIXELATE: "Pixelate",
        "color": "Colour",
        "width": "Line width (mouse wheel)",
        "undo": "Undo",
        "redo": "Redo",
        "copy": "Copy",
        "save": "Save to Downloads",
        "edit": "Open in the editor",
        "cancel": "Cancel",
        "done": "Done — send to the assistant",
    },
    "de": {
        mm.MOVE: "Auswählen und verschieben",  # i18n-allow: product UI string
        mm.RECT: "Rechteck",  # i18n-allow: product UI string
        mm.ELLIPSE: "Ellipse",  # i18n-allow: product UI string
        mm.ARROW: "Pfeil",  # i18n-allow: product UI string
        mm.LINE: "Linie",  # i18n-allow: product UI string
        mm.PEN: "Stift",  # i18n-allow: product UI string
        mm.HIGHLIGHT: "Textmarker",  # i18n-allow: product UI string
        mm.TEXT: "Text",  # i18n-allow: product UI string
        mm.COUNTER: "Nummerierter Schritt",  # i18n-allow: product UI string
        mm.BLUR: "Weichzeichnen",  # i18n-allow: product UI string
        mm.PIXELATE: "Verpixeln",  # i18n-allow: product UI string
        "color": "Farbe",  # i18n-allow: product UI string
        "width": "Linienstärke (Mausrad)",  # i18n-allow: product UI string
        "undo": "Rückgängig",  # i18n-allow: product UI string
        "redo": "Wiederholen",  # i18n-allow: product UI string
        "copy": "Kopieren",  # i18n-allow: product UI string
        "save": "In Downloads speichern",  # i18n-allow: product UI string
        "edit": "Im Editor öffnen",  # i18n-allow: product UI string
        "cancel": "Abbrechen",  # i18n-allow: product UI string
        "done": "Fertig — an den Assistenten",  # i18n-allow: product UI string
    },
    "es": {
        mm.MOVE: "Seleccionar y mover",  # i18n-allow: product UI string
        mm.RECT: "Rectángulo",  # i18n-allow: product UI string
        mm.ELLIPSE: "Elipse",  # i18n-allow: product UI string
        mm.ARROW: "Flecha",  # i18n-allow: product UI string
        mm.LINE: "Línea",  # i18n-allow: product UI string
        mm.PEN: "Lápiz",  # i18n-allow: product UI string
        mm.HIGHLIGHT: "Resaltador",  # i18n-allow: product UI string
        mm.TEXT: "Texto",  # i18n-allow: product UI string
        mm.COUNTER: "Paso numerado",  # i18n-allow: product UI string
        mm.BLUR: "Desenfocar",  # i18n-allow: product UI string
        mm.PIXELATE: "Pixelar",  # i18n-allow: product UI string
        "color": "Color",  # i18n-allow: product UI string
        "width": "Grosor (rueda del ratón)",  # i18n-allow: product UI string
        "undo": "Deshacer",  # i18n-allow: product UI string
        "redo": "Rehacer",  # i18n-allow: product UI string
        "copy": "Copiar",  # i18n-allow: product UI string
        "save": "Guardar en Descargas",  # i18n-allow: product UI string
        "edit": "Abrir en el editor",  # i18n-allow: product UI string
        "cancel": "Cancelar",  # i18n-allow: product UI string
        "done": "Listo — enviar al asistente",  # i18n-allow: product UI string
    },
}

#: Shortcut shown in each tooltip.
_ACTION_KEYS = {
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Y",
    "copy": "Ctrl+C",
    "save": "Ctrl+S",
    "edit": "Ctrl+E",
    "cancel": "Esc",
    "done": "Enter",
}


def labels_for(language: str) -> dict[str, str]:
    return dict(_LABELS.get((language or "").lower()[:2], _LABELS["en"]))


def _qcolor(hex_color: str, alpha: int = 255) -> QColor:
    colour = QColor(hex_color)
    colour.setAlpha(alpha)
    return colour


def _is_light(hex_color: str) -> bool:
    c = QColor(hex_color)
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) > 160


def _qrect(box: mm.Box) -> QRectF:
    return QRectF(box[0], box[1], box[2], box[3])


def _text_font(shape: mm.Shape) -> QFont:
    font = QFont()
    font.setPixelSize(max(8, round(mm.text_size(shape.width))))
    font.setWeight(QFont.Weight.Bold)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


def text_rect(shape: mm.Shape) -> QRectF:
    """A text marking's exact bounds, measured with its own font."""
    metrics = QFontMetricsF(_text_font(shape))
    lines = shape.text.split("\n") or [""]
    width = max(metrics.horizontalAdvance(line) for line in lines)
    x, y = shape.points[0]
    return QRectF(x, y, max(width, metrics.averageCharWidth()), metrics.lineSpacing() * len(lines))


# --------------------------------------------------------------------------
# Painting the markings
# --------------------------------------------------------------------------


def _arrow_polygon(start: mm.Point, end: mm.Point, width: float) -> QPolygonF | None:
    """A tapered arrow: thin tail, wide shaft, sharp head."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1.0:
        return None
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    head = min(length * 0.6, max(12.0, width * 3.4 + 8.0))
    head_half = head * 0.62
    tail_half = max(0.6, width * 0.3)
    shaft_half = max(1.2, width * 0.75)
    bx, by = end[0] - ux * head, end[1] - uy * head
    points = [
        (start[0] + nx * tail_half, start[1] + ny * tail_half),
        (bx + nx * shaft_half, by + ny * shaft_half),
        (bx + nx * head_half, by + ny * head_half),
        end,
        (bx - nx * head_half, by - ny * head_half),
        (bx - nx * shaft_half, by - ny * shaft_half),
        (start[0] - nx * tail_half, start[1] - ny * tail_half),
    ]
    return QPolygonF([QPointF(x, y) for x, y in points])


def _stroke_path(points: list[mm.Point]) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    if len(points) == 1:
        path.lineTo(QPointF(points[0][0] + 0.01, points[0][1]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    return path


def paint_shape(
    painter: QPainter,
    shape: mm.Shape,
    *,
    hide_source: Callable[[mm.Shape], QPixmap | None] | None = None,
    caret: bool = False,
) -> None:
    """Paint one marking in the painter's current (logical) coordinates."""
    if not shape.points:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    colour = _qcolor(shape.color)
    kind = shape.kind
    if kind in mm.HIDE_KINDS:
        box = _qrect(mm.normalized(shape.points[0], shape.points[-1]))
        patch = hide_source(shape) if hide_source is not None else None
        if patch is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, kind == mm.BLUR)
            painter.drawPixmap(box, patch, QRectF(patch.rect()))
        else:
            painter.fillRect(box, QColor(128, 128, 132, 220))
    elif kind in (mm.RECT, mm.ELLIPSE):
        pen = QPen(colour, shape.width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        box = _qrect(mm.normalized(shape.points[0], shape.points[-1]))
        if kind == mm.RECT:
            painter.drawRect(box)
        else:
            painter.drawEllipse(box)
    elif kind == mm.LINE:
        pen = QPen(colour, shape.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(*shape.points[0]), QPointF(*shape.points[-1]))
    elif kind == mm.ARROW:
        polygon = _arrow_polygon(shape.points[0], shape.points[-1], shape.width)
        if polygon is not None:
            edge = QPen(colour, 1.0)
            edge.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(edge)
            painter.setBrush(colour)
            painter.drawPolygon(polygon)
    elif kind in mm.STROKE_KINDS:
        highlight = kind == mm.HIGHLIGHT
        width = shape.width * 4.0 + 8.0 if highlight else shape.width
        pen = QPen(_qcolor(shape.color, _HIGHLIGHT_ALPHA) if highlight else colour, width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap if highlight else Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_stroke_path(shape.points))
    elif kind == mm.TEXT:
        _paint_text(painter, shape, caret=caret)
    elif kind == mm.COUNTER:
        r = mm.counter_radius(shape.width)
        centre = QPointF(*shape.points[0])
        painter.setPen(QPen(QColor(255, 255, 255, 235), 2.0))
        painter.setBrush(colour)
        painter.drawEllipse(centre, r, r)
        font = QFont()
        font.setPixelSize(max(8, round(r * 1.15)))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor(17, 17, 17) if _is_light(shape.color) else QColor(255, 255, 255))
        painter.drawText(
            QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r),
            Qt.AlignmentFlag.AlignCenter,
            str(shape.number),
        )
    painter.restore()


def _paint_text(painter: QPainter, shape: mm.Shape, *, caret: bool) -> None:
    font = _text_font(shape)
    metrics = QFontMetricsF(font)
    x, y = shape.points[0]
    path = QPainterPath()
    lines = shape.text.split("\n")
    for i, line in enumerate(lines):
        path.addText(QPointF(x, y + metrics.ascent() + i * metrics.lineSpacing()), font, line)
    halo = QColor(17, 17, 17, 220) if _is_light(shape.color) else QColor(255, 255, 255, 235)
    outline = QPen(halo, max(2.0, font.pixelSize() / 7.0))
    outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.strokePath(path, outline)
    painter.fillPath(path, _qcolor(shape.color))
    if caret:
        last = lines[-1] if lines else ""
        cx = x + metrics.horizontalAdvance(last) + 1.5
        cy = y + (len(lines) - 1) * metrics.lineSpacing()
        painter.setPen(QPen(_qcolor(shape.color), 1.5))
        painter.drawLine(QPointF(cx, cy + 2), QPointF(cx, cy + metrics.height() - 2))


def paint_selection_frame(painter: QPainter, sel: QRectF) -> None:
    """The selection's border and its eight resize handles."""
    painter.save()
    r = sel.adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(0, 0, 0, 70), 1.0))
    painter.drawRect(r.adjusted(-1.0, -1.0, 1.0, 1.0))
    painter.setPen(QPen(QColor(255, 255, 255, 235), 1.0))
    painter.drawRect(r)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(_HANDLE_EDGE, 1.0))
    painter.setBrush(_HANDLE_FILL)
    for hx, hy in mm.handle_points((sel.x(), sel.y(), sel.width(), sel.height())).values():
        painter.drawEllipse(QPointF(hx, hy), _HANDLE_R, _HANDLE_R)
    painter.restore()


def paint_picked(painter: QPainter, shape: mm.Shape) -> None:
    """A dashed frame around the marking the move tool holds."""
    box = text_rect(shape) if shape.kind == mm.TEXT else _qrect(mm.bounds(shape))
    pad = shape.width / 2.0 + 4.0
    painter.save()
    pen = QPen(QColor(255, 255, 255, 230), 1.0, Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(box.adjusted(-pad, -pad, pad, pad))
    painter.restore()


# --------------------------------------------------------------------------
# Hide patches from the frozen frame
# --------------------------------------------------------------------------


class HidePatches:
    """Blur/pixelate previews cut from the frozen frame, cached per rectangle."""

    def __init__(self, frozen: QPixmap | None, scale: Callable[[], float]) -> None:
        self._frozen = frozen
        self._scale = scale
        self._cache: dict[tuple, QPixmap] = {}

    def __call__(self, shape: mm.Shape) -> QPixmap | None:
        if self._frozen is None or len(shape.points) < 2:
            return None
        x, y, w, h = mm.normalized(shape.points[0], shape.points[-1])
        if w < 1 or h < 1:
            return None
        scale = self._scale()
        key = (shape.kind, round(x), round(y), round(w), round(h))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        source = self._frozen.copy(
            round(x * scale), round(y * scale), max(1, round(w * scale)), max(1, round(h * scale))
        )
        if source.isNull():
            return None
        if shape.kind == mm.PIXELATE:
            block = max(2.0, _PIXEL_BLOCK * scale)
            small = source.scaled(
                max(1, round(source.width() / block)),
                max(1, round(source.height() / block)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            small = source.scaled(
                max(1, round(source.width() / _BLUR_FACTOR)),
                max(1, round(source.height() / _BLUR_FACTOR)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = small
        return small


# --------------------------------------------------------------------------
# Rendering the result
# --------------------------------------------------------------------------


def render_overlay(shapes: list[mm.Shape], sel: QRectF, scale: float) -> str:
    """Every non-hide marking on a transparent PNG of the selection, as base64.

    ``scale`` is device pixels per logical pixel, so the overlay is as sharp
    as the screen. The main process resizes it onto the real capture.
    """
    width = max(1, round(sel.width() * scale))
    height = max(1, round(sel.height() * scale))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.scale(width / sel.width(), height / sel.height())
    painter.translate(-sel.x(), -sel.y())
    painter.setClipRect(sel)
    for shape in shapes:
        if shape.kind not in mm.HIDE_KINDS:
            paint_shape(painter, shape)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return base64.b64encode(bytes(data.data())).decode("ascii")


# --------------------------------------------------------------------------
# The toolbar
# --------------------------------------------------------------------------


def _dark_hud() -> bool:
    """Follow the system appearance where Qt can read it; dark otherwise."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        return scheme != Qt.ColorScheme.Light
    except AttributeError:  # Qt < 6.5 cannot tell
        return True


def _icon_pixmap(draw: Callable[[QPainter, QColor], None], colour: QColor, dpr: float) -> QPixmap:
    size = 18
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(colour, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter, colour)
    painter.end()
    return pixmap


def _glyph(name: str) -> Callable[[QPainter, QColor], None]:  # noqa: C901 - one table of glyphs
    def move(p: QPainter, c: QColor) -> None:
        path = QPainterPath(QPointF(4, 3))
        for x, y in ((4, 15), (7.5, 11.5), (10, 16.5), (12, 15.5), (9.6, 10.6), (14.5, 10.6)):
            path.lineTo(QPointF(x, y))
        path.closeSubpath()
        p.drawPath(path)

    def rect(p: QPainter, c: QColor) -> None:
        p.drawRoundedRect(QRectF(3, 4.5, 12, 9), 1.5, 1.5)

    def ellipse(p: QPainter, c: QColor) -> None:
        p.drawEllipse(QRectF(2.5, 4, 13, 10))

    def arrow(p: QPainter, c: QColor) -> None:
        p.drawLine(QPointF(4, 14), QPointF(14, 4))
        p.drawLine(QPointF(14, 4), QPointF(8, 4))
        p.drawLine(QPointF(14, 4), QPointF(14, 10))

    def line(p: QPainter, c: QColor) -> None:
        p.drawLine(QPointF(4, 14), QPointF(14, 4))

    def pen(p: QPainter, c: QColor) -> None:
        path = QPainterPath(QPointF(3, 15))
        path.lineTo(QPointF(4, 11.5))
        path.lineTo(QPointF(12, 3.5))
        path.lineTo(QPointF(14.5, 6))
        path.lineTo(QPointF(6.5, 14))
        path.closeSubpath()
        p.drawPath(path)

    def highlight(p: QPainter, c: QColor) -> None:
        p.drawRoundedRect(QRectF(5, 3, 8, 8), 1.5, 1.5)
        p.drawLine(QPointF(7, 11), QPointF(7, 13.5))
        p.drawLine(QPointF(11, 11), QPointF(11, 13.5))
        p.drawLine(QPointF(3, 15.5), QPointF(15, 15.5))

    def text(p: QPainter, c: QColor) -> None:
        p.drawLine(QPointF(4, 4.5), QPointF(14, 4.5))
        p.drawLine(QPointF(9, 4.5), QPointF(9, 14.5))

    def counter(p: QPainter, c: QColor) -> None:
        p.drawEllipse(QRectF(2.5, 2.5, 13, 13))
        font = QFont()
        font.setPixelSize(9)
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRectF(2.5, 2.5, 13, 13), Qt.AlignmentFlag.AlignCenter, "1")

    def blur(p: QPainter, c: QColor) -> None:
        path = QPainterPath(QPointF(9, 2.5))
        path.cubicTo(QPointF(9, 2.5), QPointF(3.5, 9), QPointF(3.5, 11.5))
        path.cubicTo(QPointF(3.5, 14.5), QPointF(6, 16), QPointF(9, 16))
        path.cubicTo(QPointF(12, 16), QPointF(14.5, 14.5), QPointF(14.5, 11.5))
        path.cubicTo(QPointF(14.5, 9), QPointF(9, 2.5), QPointF(9, 2.5))
        p.drawPath(path)

    def pixelate(p: QPainter, c: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            for j in range(3):
                shade = QColor(c)
                shade.setAlpha(255 if (i + j) % 2 == 0 else 110)
                p.fillRect(QRectF(3 + i * 4.2, 3 + j * 4.2, 3.6, 3.6), shade)

    def undo(p: QPainter, c: QColor) -> None:
        path = QPainterPath(QPointF(5, 7))
        path.lineTo(QPointF(11, 7))
        path.cubicTo(QPointF(16, 7), QPointF(16, 15), QPointF(11, 15))
        path.lineTo(QPointF(7, 15))
        p.drawPath(path)
        p.drawLine(QPointF(5, 7), QPointF(8, 4))
        p.drawLine(QPointF(5, 7), QPointF(8, 10))

    def redo(p: QPainter, c: QColor) -> None:
        p.translate(18, 0)
        p.scale(-1, 1)
        undo(p, c)

    def copy(p: QPainter, c: QColor) -> None:
        p.drawRoundedRect(QRectF(6, 6, 9, 9.5), 1.5, 1.5)
        path = QPainterPath(QPointF(4, 12))
        path.lineTo(QPointF(3, 12))
        path.lineTo(QPointF(3, 2.5))
        path.lineTo(QPointF(11, 2.5))
        path.lineTo(QPointF(11, 4))
        p.drawPath(path)

    def save(p: QPainter, c: QColor) -> None:
        p.drawLine(QPointF(9, 2.5), QPointF(9, 11.5))
        p.drawLine(QPointF(5.5, 8), QPointF(9, 11.5))
        p.drawLine(QPointF(12.5, 8), QPointF(9, 11.5))
        p.drawLine(QPointF(3, 15), QPointF(15, 15))

    def edit(p: QPainter, c: QColor) -> None:
        p.drawRoundedRect(QRectF(2.5, 3.5, 13, 11), 2, 2)
        p.drawLine(QPointF(6, 11), QPointF(12, 7))

    def cancel(p: QPainter, c: QColor) -> None:
        p.drawLine(QPointF(5, 5), QPointF(13, 13))
        p.drawLine(QPointF(13, 5), QPointF(5, 13))

    def done(p: QPainter, c: QColor) -> None:
        pen_ = QPen(c, 2.0)
        pen_.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen_.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen_)
        path = QPainterPath(QPointF(4, 9.5))
        path.lineTo(QPointF(7.5, 13))
        path.lineTo(QPointF(14, 5.5))
        p.drawPath(path)

    table = {
        mm.MOVE: move,
        mm.RECT: rect,
        mm.ELLIPSE: ellipse,
        mm.ARROW: arrow,
        mm.LINE: line,
        mm.PEN: pen,
        mm.HIGHLIGHT: highlight,
        mm.TEXT: text,
        mm.COUNTER: counter,
        mm.BLUR: blur,
        mm.PIXELATE: pixelate,
        "undo": undo,
        "redo": redo,
        "copy": copy,
        "save": save,
        "edit": edit,
        "cancel": cancel,
        "done": done,
    }
    return table[name]


class Toolbar(QFrame):
    """The floating marking toolbar, docked to the selection by its window."""

    tool_chosen = Signal(str)
    color_chosen = Signal(str)
    width_cycled = Signal()
    action = Signal(str)

    _BTN = 30
    _SWATCH = 18

    def __init__(self, parent: QWidget, *, language: str, dpr: float) -> None:
        super().__init__(parent)
        self.setObjectName("hud")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._labels = labels_for(language)
        self._dpr = max(1.0, dpr)
        dark = _dark_hud()
        self._fg = QColor(236, 236, 240) if dark else QColor(28, 28, 32)
        bg = "rgba(28, 28, 31, 242)" if dark else "rgba(250, 250, 252, 245)"
        edge = "rgba(255, 255, 255, 34)" if dark else "rgba(0, 0, 0, 34)"
        hover = "rgba(255, 255, 255, 26)" if dark else "rgba(0, 0, 0, 18)"
        sep = "rgba(255, 255, 255, 40)" if dark else "rgba(0, 0, 0, 30)"
        self.setStyleSheet(
            f"""
            QFrame#hud {{ background: {bg}; border: 1px solid {edge}; border-radius: 11px; }}
            QFrame#sep {{ background: {sep}; border: none; }}
            QToolButton {{ border: none; border-radius: 7px; background: transparent; }}
            QToolButton:hover {{ background: {hover}; }}
            QToolButton:checked {{ background: #0A84FF; }}
            QToolButton#done {{ background: #0A84FF; }}
            QToolButton#done:hover {{ background: #3B9BFF; }}
            QToolButton:disabled {{ background: transparent; }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(2)
        self._tools = QButtonGroup(self)
        self._tools.setExclusive(True)
        self._tool_buttons: dict[str, QToolButton] = {}
        for kind, key in mm.TOOL_KEYS.items():
            button = self._button(kind, f"{self._labels[kind]} ({key})", checkable=True)
            self._tools.addButton(button)
            self._tool_buttons[kind] = button
            button.clicked.connect(lambda _c=False, k=kind: self.tool_chosen.emit(k))
            layout.addWidget(button)
        layout.addWidget(self._separator())
        self._swatches: dict[str, QToolButton] = {}
        for index, colour in enumerate(mm.PALETTE):
            swatch = QToolButton(self)
            swatch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            swatch.setCheckable(True)
            swatch.setFixedSize(QSize(self._SWATCH + 4, self._BTN))
            swatch.setToolTip(f"{self._labels['color']} ({index + 1})")
            swatch.clicked.connect(lambda _c=False, col=colour: self.color_chosen.emit(col))
            self._swatches[colour] = swatch
            layout.addWidget(swatch)
        self._width = self._button("width", self._labels["width"], glyph=False)
        self._width.clicked.connect(lambda _c=False: self.width_cycled.emit())
        layout.addWidget(self._width)
        layout.addWidget(self._separator())
        self._undo = self._action_button("undo", layout)
        self._redo = self._action_button("redo", layout)
        layout.addWidget(self._separator())
        for name in ("copy", "save", "edit"):
            self._action_button(name, layout)
        layout.addWidget(self._separator())
        self._action_button("cancel", layout)
        done = self._action_button("done", layout, colour=QColor(255, 255, 255))
        done.setObjectName("done")
        self._colour = mm.PALETTE[0]
        self._width_value = mm.WIDTHS[mm.DEFAULT_WIDTH_INDEX]
        self.adjustSize()

    # -- building ------------------------------------------------------------
    def _button(
        self,
        name: str,
        tip: str,
        *,
        checkable: bool = False,
        glyph: bool = True,
        colour: QColor | None = None,
    ) -> QToolButton:
        button = QToolButton(self)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedSize(QSize(self._BTN, self._BTN))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tip)
        button.setCheckable(checkable)
        if glyph:
            icon = QIcon()
            draw = _glyph(name)
            normal = colour or self._fg
            disabled = QColor(normal)
            disabled.setAlpha(80)
            for tint, mode, state in (
                (normal, QIcon.Mode.Normal, QIcon.State.Off),
                (QColor(255, 255, 255), QIcon.Mode.Normal, QIcon.State.On),
                (disabled, QIcon.Mode.Disabled, QIcon.State.Off),
            ):
                icon.addPixmap(_icon_pixmap(draw, tint, self._dpr), mode, state)
            button.setIcon(icon)
        return button

    def _action_button(
        self, name: str, layout: QHBoxLayout, colour: QColor | None = None
    ) -> QToolButton:
        tip = f"{self._labels[name]} ({_ACTION_KEYS[name]})"
        button = self._button(name, tip, colour=colour)
        button.clicked.connect(lambda _c=False, n=name: self.action.emit(n))
        layout.addWidget(button)
        return button

    def _separator(self) -> QFrame:
        line = QFrame(self)
        line.setObjectName("sep")
        line.setFixedSize(QSize(1, 18))
        return line

    # -- state ---------------------------------------------------------------
    def set_tool(self, kind: str) -> None:
        button = self._tool_buttons.get(kind)
        if button is not None:
            button.setChecked(True)

    def set_color(self, colour: str) -> None:
        self._colour = colour
        for value, swatch in self._swatches.items():
            chosen = value == colour
            swatch.setChecked(chosen)
            # The ring in the icon marks the choice; no blue checked fill.
            swatch.setStyleSheet("QToolButton:checked { background: transparent; }")
            swatch.setIcon(self._swatch_icon(value, chosen))
            swatch.setIconSize(QSize(self._SWATCH, self._SWATCH))
        self._refresh_width()

    def _swatch_icon(self, colour: str, chosen: bool) -> QIcon:
        size = self._SWATCH
        pixmap = QPixmap(round(size * self._dpr), round(size * self._dpr))
        pixmap.setDevicePixelRatio(self._dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if chosen:
            painter.setPen(QPen(self._fg, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(0.8, 0.8, size - 1.6, size - 1.6))
        painter.setPen(QPen(QColor(128, 128, 128, 150), 1.0))
        painter.setBrush(QColor(colour))
        inset = 3.6 if chosen else 2.4
        painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
        painter.end()
        return QIcon(pixmap)

    def set_width(self, width: float) -> None:
        self._width_value = width
        self._refresh_width()

    def _refresh_width(self) -> None:
        size = 18
        pixmap = QPixmap(round(size * self._dpr), round(size * self._dpr))
        pixmap.setDevicePixelRatio(self._dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._fg, min(9.0, self._width_value))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(4.5, 9), QPointF(13.5, 9))
        painter.end()
        self._width.setIcon(QIcon(pixmap))

    def set_history(self, can_undo: bool, can_redo: bool) -> None:
        self._undo.setEnabled(can_undo)
        self._redo.setEnabled(can_redo)


__all__ = [
    "DIM_OUTSIDE",
    "HidePatches",
    "Toolbar",
    "labels_for",
    "paint_picked",
    "paint_selection_frame",
    "paint_shape",
    "render_overlay",
    "text_rect",
]
