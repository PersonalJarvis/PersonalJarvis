"""Mark up a chosen area in place with the picker's annotation tools.

Runs ONLY inside ``python -m jarvis.appshot.picker`` (PySide6, AP-26). Once
an area is chosen the overlay stays up: a toolbar docks under the selection
and the user draws on the frozen frame right there, with the same tools,
looks and keys as the full appshot editor. Enter (or the check button) takes
the appshot WITH the markings, so the assistant sees exactly what was pointed
at.

The rules live in :mod:`jarvis.appshot.picker.markup_model` (plain data);
this module paints them like the full editor does, draws the toolbar and its
looks for the current tool inline, and measures text. The markings leave the process as a
transparent PNG overlay plus the pixelate/blur rectangles and the background
frame; the main process applies them to the real, privacy-filtered capture
(:mod:`jarvis.appshot.markup`).
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
    QLinearGradient,
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
_GRIP_R = 5.0
_ACCENT = QColor(10, 132, 255)
#: Logical px per pixelate block, and the blur's downscale factor.
_PIXEL_BLOCK = 9.0
_BLUR_FACTOR = 12.0
_SPOTLIGHT_DIM = QColor(0, 0, 0, 140)

# Toolbar wording — app chrome, so it follows [ui].language like the card.
_LABELS: dict[str, dict[str, str]] = {
    "en": {
        mm.MOVE: "Select and move",
        mm.RECT: "Rectangle",
        mm.FILLED: "Filled rectangle",
        mm.ELLIPSE: "Ellipse",
        mm.LINE: "Line",
        mm.ARROW: "Arrow",
        mm.TEXT: "Text",
        mm.REDACT: "Pixelate / blur",
        mm.SPOTLIGHT: "Spotlight",
        mm.COUNTER: "Numbered step",
        mm.PEN: "Draw",
        mm.HIGHLIGHT: "Highlighter",
        mm.BACKGROUND: "Background",
        "arrow_tapered": "Tapered",
        "arrow_classic": "Classic",
        "arrow_double": "Two heads",
        "text_plain": "Plain",
        "text_label": "Label",
        "text_outline": "Outline",
        "redact_pixelate": "Pixelate",
        "redact_blur": "Blur",
        "background_off": "No background",
        "color": "Colour",
        "width": "Size (1-5, mouse wheel)",
        "undo": "Undo",
        "redo": "Redo",
        "copy": "Copy",
        "save": "Save to Downloads",
        "edit": "Open in the editor",
        "cancel": "Cancel",
        "done": "Done — send to the assistant",
        "placeholder": "Type text",
    },
    "de": {
        mm.MOVE: "Auswählen und verschieben",  # i18n-allow: product UI string
        mm.RECT: "Rechteck",  # i18n-allow: product UI string
        mm.FILLED: "Gefülltes Rechteck",  # i18n-allow: product UI string
        mm.ELLIPSE: "Ellipse",  # i18n-allow: product UI string
        mm.LINE: "Linie",  # i18n-allow: product UI string
        mm.ARROW: "Pfeil",  # i18n-allow: product UI string
        mm.TEXT: "Text",  # i18n-allow: product UI string
        mm.REDACT: "Verpixeln / Weichzeichnen",  # i18n-allow: product UI string
        mm.SPOTLIGHT: "Hervorheben",  # i18n-allow: product UI string
        mm.COUNTER: "Nummerierter Schritt",  # i18n-allow: product UI string
        mm.PEN: "Zeichnen",  # i18n-allow: product UI string
        mm.HIGHLIGHT: "Textmarker",  # i18n-allow: product UI string
        mm.BACKGROUND: "Hintergrund",  # i18n-allow: product UI string
        "arrow_tapered": "Spitz zulaufend",  # i18n-allow: product UI string
        "arrow_classic": "Klassisch",  # i18n-allow: product UI string
        "arrow_double": "Zwei Spitzen",  # i18n-allow: product UI string
        "text_plain": "Schlicht",  # i18n-allow: product UI string
        "text_label": "Etikett",  # i18n-allow: product UI string
        "text_outline": "Kontur",  # i18n-allow: product UI string
        "redact_pixelate": "Verpixeln",  # i18n-allow: product UI string
        "redact_blur": "Weichzeichnen",  # i18n-allow: product UI string
        "background_off": "Kein Hintergrund",  # i18n-allow: product UI string
        "color": "Farbe",  # i18n-allow: product UI string
        "width": "Größe (1-5, Mausrad)",  # i18n-allow: product UI string
        "undo": "Rückgängig",  # i18n-allow: product UI string
        "redo": "Wiederholen",  # i18n-allow: product UI string
        "copy": "Kopieren",  # i18n-allow: product UI string
        "save": "In Downloads speichern",  # i18n-allow: product UI string
        "edit": "Im Editor öffnen",  # i18n-allow: product UI string
        "cancel": "Abbrechen",  # i18n-allow: product UI string
        "done": "Fertig — an den Assistenten",  # i18n-allow: product UI string
        "placeholder": "Text eingeben",  # i18n-allow: product UI string
    },
    "es": {
        mm.MOVE: "Seleccionar y mover",  # i18n-allow: product UI string
        mm.RECT: "Rectángulo",  # i18n-allow: product UI string
        mm.FILLED: "Rectángulo relleno",  # i18n-allow: product UI string
        mm.ELLIPSE: "Elipse",  # i18n-allow: product UI string
        mm.LINE: "Línea",  # i18n-allow: product UI string
        mm.ARROW: "Flecha",  # i18n-allow: product UI string
        mm.TEXT: "Texto",  # i18n-allow: product UI string
        mm.REDACT: "Pixelar / desenfocar",  # i18n-allow: product UI string
        mm.SPOTLIGHT: "Resaltar zona",  # i18n-allow: product UI string
        mm.COUNTER: "Paso numerado",  # i18n-allow: product UI string
        mm.PEN: "Dibujar",  # i18n-allow: product UI string
        mm.HIGHLIGHT: "Resaltador",  # i18n-allow: product UI string
        mm.BACKGROUND: "Fondo",  # i18n-allow: product UI string
        "arrow_tapered": "Afilada",  # i18n-allow: product UI string
        "arrow_classic": "Clásica",  # i18n-allow: product UI string
        "arrow_double": "Dos puntas",  # i18n-allow: product UI string
        "text_plain": "Simple",  # i18n-allow: product UI string
        "text_label": "Etiqueta",  # i18n-allow: product UI string
        "text_outline": "Contorno",  # i18n-allow: product UI string
        "redact_pixelate": "Pixelar",  # i18n-allow: product UI string
        "redact_blur": "Desenfocar",  # i18n-allow: product UI string
        "background_off": "Sin fondo",  # i18n-allow: product UI string
        "color": "Color",  # i18n-allow: product UI string
        "width": "Tamaño (1-5, rueda del ratón)",  # i18n-allow: product UI string
        "undo": "Deshacer",  # i18n-allow: product UI string
        "redo": "Rehacer",  # i18n-allow: product UI string
        "copy": "Copiar",  # i18n-allow: product UI string
        "save": "Guardar en Descargas",  # i18n-allow: product UI string
        "edit": "Abrir en el editor",  # i18n-allow: product UI string
        "cancel": "Cancelar",  # i18n-allow: product UI string
        "done": "Listo — enviar al asistente",  # i18n-allow: product UI string
        "placeholder": "Escribe un texto",  # i18n-allow: product UI string
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

#: Toolbar order — the full editor's: selection, shapes, text, effects, ink.
TOOL_ORDER = (
    mm.MOVE,
    mm.RECT,
    mm.FILLED,
    mm.ELLIPSE,
    mm.LINE,
    mm.ARROW,
    mm.TEXT,
    mm.REDACT,
    mm.SPOTLIGHT,
    mm.COUNTER,
    mm.PEN,
    mm.HIGHLIGHT,
    mm.BACKGROUND,
)


def labels_for(language: str) -> dict[str, str]:
    return dict(_LABELS.get((language or "").lower()[:2], _LABELS["en"]))


def _qcolor(hex_color: str, alpha: int = 255) -> QColor:
    colour = QColor(hex_color)
    colour.setAlpha(alpha)
    return colour


def contrast_on(hex_color: str) -> QColor:
    """Black or white, whichever reads on ``hex_color`` (the full editor's rule)."""
    c = QColor(hex_color)
    luminance = (0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()) / 255
    return QColor(17, 17, 17) if luminance > 0.6 else QColor(255, 255, 255)


def _qrect(box: mm.Box) -> QRectF:
    return QRectF(box[0], box[1], box[2], box[3])


def text_font(size: float) -> QFont:
    """The editors' text face: Inter, else the system UI font, semibold."""
    font = QFont()
    font.setFamilies(["Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans"])
    font.setPixelSize(max(6, round(size)))
    font.setWeight(QFont.Weight.DemiBold)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return font


_metrics_cache: dict[int, QFontMetricsF] = {}


def qt_measure(text: str, size: float) -> float:
    """Width of ``text`` at ``size`` px in the editors' face."""
    key = max(6, round(size))
    metrics = _metrics_cache.get(key)
    if metrics is None:
        metrics = QFontMetricsF(text_font(key))
        _metrics_cache[key] = metrics
    return metrics.horizontalAdvance(text)


# --------------------------------------------------------------------------
# Painting the markings — ports of the full editor's renderer
# --------------------------------------------------------------------------


def tapered_arrow_outline(start: mm.Point, end: mm.Point, width: float) -> list[mm.Point]:
    """The tapered arrow, tail to tip and back (``taperedArrowOutline`` in TS)."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 2:
        return []
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    head = min(max(12.0, width * 4.6 + 6.0), length * 0.62)
    barb = head * 0.56
    neck_at = length - head * 0.7
    tail = max(0.6, width * 0.14)
    neck = max(1.4, width * 0.78)

    def at(along: float, across: float) -> mm.Point:
        return (start[0] + ux * along + nx * across, start[1] + uy * along + ny * across)

    return [
        at(0, tail),
        at(neck_at, neck),
        at(length - head, barb),
        at(length, 0),
        at(length - head, -barb),
        at(neck_at, -neck),
        at(0, -tail),
    ]


def _polygon(points: list[mm.Point]) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in points])


def _soft_shadow(painter: QPainter, path: QPainterPath, blur: float, dy: float, alpha: int) -> None:
    """A cheap soft lift: a few offset, fading copies of the shape's fill."""
    steps = 3
    for i in range(steps, 0, -1):
        spread = blur * i / steps
        shade = QColor(0, 0, 0, max(4, alpha // (steps + i)))
        pen = QPen(shade, spread)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.save()
        painter.translate(0, dy)
        painter.setPen(pen)
        painter.setBrush(shade)
        painter.drawPath(path)
        painter.restore()


def _tapered_arrow(painter: QPainter, shape: mm.Shape, colour: QColor) -> None:
    outline = tapered_arrow_outline(shape.points[0], shape.points[-1], shape.width)
    if not outline:
        return
    path = QPainterPath()
    path.addPolygon(_polygon(outline))
    path.closeSubpath()
    _soft_shadow(painter, path, max(3.0, shape.width * 1.1), max(1.0, shape.width * 0.3), 80)
    edge = QPen(colour, max(1.0, shape.width * 0.22))
    edge.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(edge)
    painter.setBrush(colour)
    painter.drawPath(path)


def _classic_arrow(painter: QPainter, start: mm.Point, end: mm.Point, shape: mm.Shape) -> None:
    colour = _qcolor(shape.color)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head = max(10.0, shape.width * 4.0)
    back = (end[0] - math.cos(angle) * head * 0.8, end[1] - math.sin(angle) * head * 0.8)
    pen = QPen(colour, shape.width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(QPointF(*start), QPointF(*back))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawPolygon(
        _polygon(
            [
                end,
                (end[0] - math.cos(angle - 0.45) * head, end[1] - math.sin(angle - 0.45) * head),
                (end[0] - math.cos(angle + 0.45) * head, end[1] - math.sin(angle + 0.45) * head),
            ]
        )
    )


def _smooth_path(points: list[mm.Point]) -> QPainterPath:
    """A hand-drawn stroke smoothed through the midpoints of its samples."""
    path = QPainterPath(QPointF(*points[0]))
    if len(points) == 1:
        path.lineTo(QPointF(points[0][0] + 0.1, points[0][1]))
        return path
    for i in range(1, len(points) - 1):
        mid = ((points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2)
        path.quadTo(QPointF(*points[i]), QPointF(*mid))
    path.lineTo(QPointF(*points[-1]))
    return path


def _rounded(box: mm.Box, radius: float) -> QPainterPath:
    path = QPainterPath()
    r = max(0.0, min(radius, box[2] / 2, box[3] / 2))
    path.addRoundedRect(_qrect(box), r, r)
    return path


def paint_shape(
    painter: QPainter,
    shape: mm.Shape,
    *,
    hide_source: Callable[[mm.Shape], QPixmap | None] | None = None,
) -> None:
    """Paint one marking in the painter's current (logical) coordinates.

    Spotlights are not painted here: they dim everything around them in one
    layer (:func:`paint_spotlights`).
    """
    if not shape.points or shape.kind == mm.SPOTLIGHT:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    colour = _qcolor(shape.color)
    kind = shape.kind
    if kind == mm.REDACT:
        box = _qrect(mm.normalized(shape.points[0], shape.points[-1]))
        patch = hide_source(shape) if hide_source is not None else None
        if patch is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, shape.mode == "blur")
            painter.drawPixmap(box, patch, QRectF(patch.rect()))
        else:
            painter.fillRect(box, QColor(128, 128, 132, 220))
    elif kind in (mm.RECT, mm.FILLED):
        path = _rounded(mm.normalized(shape.points[0], shape.points[-1]), shape.width)
        if kind == mm.RECT:
            pen = QPen(colour, shape.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
        painter.drawPath(path)
    elif kind == mm.ELLIPSE:
        painter.setPen(QPen(colour, shape.width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(_qrect(mm.normalized(shape.points[0], shape.points[-1])))
    elif kind == mm.LINE:
        pen = QPen(colour, shape.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(*shape.points[0]), QPointF(*shape.points[-1]))
    elif kind == mm.ARROW:
        start, end = shape.points[0], shape.points[-1]
        if shape.style == "classic":
            _classic_arrow(painter, start, end, shape)
        elif shape.style == "double":
            _classic_arrow(painter, start, end, shape)
            _classic_arrow(painter, end, start, shape)
        else:
            _tapered_arrow(painter, shape, colour)
    elif kind in mm.STROKE_KINDS:
        highlight = kind == mm.HIGHLIGHT
        pen = QPen(_qcolor(shape.color, 102) if highlight else colour)
        pen.setWidthF(shape.width * 4.0 if highlight else shape.width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap if highlight else Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_smooth_path(shape.points))
    elif kind == mm.TEXT:
        _paint_text(painter, shape)
    elif kind == mm.COUNTER:
        _paint_counter(painter, shape, colour)
    painter.restore()


def _paint_text(painter: QPainter, shape: mm.Shape) -> None:
    font = text_font(shape.size)
    metrics = QFontMetricsF(font)
    x, y = shape.points[0]
    lines = mm.text_lines(shape)
    line_height = shape.size * 1.25
    # Centre each line's glyphs in its 1.25 line box, like the canvas does
    # with textBaseline "top" plus the line height.
    lead = (line_height - metrics.height()) / 2.0 + metrics.ascent()
    ink = _qcolor(shape.color)
    if shape.style == "label":
        pad = mm.label_pad(shape)
        width = max(metrics.horizontalAdvance(line) for line in lines)
        box = (x - pad, y - pad, width + pad * 2, len(lines) * line_height + pad * 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawPath(_rounded(box, pad))
        ink = contrast_on(shape.color)
    if shape.style == "outline":
        path = QPainterPath()
        for i, line in enumerate(lines):
            path.addText(QPointF(x, y + lead + i * line_height), font, line)
        outline = QPen(contrast_on(shape.color), max(2.0, shape.size / 6.0))
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, outline)
        painter.fillPath(path, ink)
        return
    painter.setFont(font)
    painter.setPen(ink)
    for i, line in enumerate(lines):
        painter.drawText(QPointF(x, y + lead + i * line_height), line)


def _paint_counter(painter: QPainter, shape: mm.Shape, colour: QColor) -> None:
    r = shape.size
    centre = QPointF(*shape.points[0])
    disc = QPainterPath()
    disc.addEllipse(centre, r, r)
    _soft_shadow(painter, disc, max(2.0, r / 4.0), 1.0, 70)
    painter.setPen(QPen(QColor(255, 255, 255, 230), max(1.5, r / 9.0)))
    painter.setBrush(colour)
    painter.drawEllipse(centre, r, r)
    painter.setFont(text_font(r * (0.95 if shape.number > 9 else 1.15)))
    painter.setPen(contrast_on(shape.color))
    painter.drawText(
        QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r),
        Qt.AlignmentFlag.AlignCenter,
        str(shape.number),
    )


def paint_spotlights(painter: QPainter, shapes: list[mm.Shape], area: QRectF) -> None:
    """Dim the area outside every spotlight, in one layer so they never stack."""
    spots = [s for s in shapes if s.kind == mm.SPOTLIGHT and len(s.points) >= 2]
    if not spots:
        return
    dim = QPainterPath()
    dim.addRect(area)
    for spot in spots:
        box = mm.normalized(spot.points[0], spot.points[-1])
        dim = dim.subtracted(_rounded(box, min(box[2], box[3]) * 0.06))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(dim, _SPOTLIGHT_DIM)
    painter.restore()


def paint_typing(painter: QPainter, shape: mm.Shape, placeholder: str, caret: bool) -> None:
    """The text box being typed in: a visible field, the text, a caret."""
    font = text_font(shape.size)
    metrics = QFontMetricsF(font)
    x, y = shape.points[0]
    lines = mm.text_lines(shape)
    line_height = shape.size * 1.25
    shown = shape.text or placeholder
    width = max(metrics.horizontalAdvance(line) for line in shown.split("\n"))
    box = QRectF(x - 6, y - 4, max(width, shape.size * 4) + 12, len(lines) * line_height + 8)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(255, 255, 255, 220), 1.2, Qt.PenStyle.DashLine))
    painter.setBrush(QColor(0, 0, 0, 70))
    painter.drawRoundedRect(box, 5, 5)
    painter.restore()
    if shape.text:
        paint_shape(painter, shape)
    else:
        lead = (line_height - metrics.height()) / 2.0 + metrics.ascent()
        painter.save()
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 150))
        painter.drawText(QPointF(x, y + lead), placeholder)
        painter.restore()
    if caret:
        last = lines[-1] if shape.text else ""
        cx = x + metrics.horizontalAdvance(last) + 1.5
        cy = y + (len(lines) - 1) * line_height
        painter.save()
        painter.setPen(QPen(_qcolor(shape.color), max(1.5, shape.size / 14.0)))
        painter.drawLine(QPointF(cx, cy + line_height * 0.12), QPointF(cx, cy + line_height * 0.88))
        painter.restore()


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
    """The selected marking: a dashed frame (not for lines) and its grips."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if shape.kind not in mm.SEGMENT_KINDS:
        box = _qrect(mm.bounds(shape, qt_measure)).adjusted(-6, -6, 6, 6)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        dark = QPen(QColor(0, 0, 0, 150), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(dark)
        painter.drawRect(box)
        light = QPen(QColor(255, 255, 255), 1.5, Qt.PenStyle.DashLine)
        light.setDashOffset(3.0)
        painter.setPen(light)
        painter.drawRect(box)
    for _name, (gx, gy) in mm.grips(shape, qt_measure):
        painter.setPen(QPen(_ACCENT, 1.5))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QPointF(gx, gy), _GRIP_R, _GRIP_R)
    painter.restore()


def background_brush(preset_id: str, rect: QRectF) -> QColor | QLinearGradient:
    stops = dict(mm.BACKGROUND_PRESETS).get(preset_id) or mm.BACKGROUND_PRESETS[0][1]
    if len(stops) == 1:
        return QColor(stops[0])
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for i, stop in enumerate(stops):
        gradient.setColorAt(i / (len(stops) - 1), QColor(stop))
    return gradient


def paint_background_frame(painter: QPainter, sel: QRectF, background: dict) -> None:
    """Preview the background frame around the area, as the result will have it."""
    pad = mm.frame_padding((sel.x(), sel.y(), sel.width(), sel.height()), background["padding"])
    outer = sel.adjusted(-pad, -pad, pad, pad)
    unit = max(1.0, max(sel.width(), sel.height()) / 1400.0)
    radius = max(0.0, float(background["radius"])) * unit
    ring = QPainterPath()
    ring.addRoundedRect(outer, 6, 6)
    hole = QPainterPath()
    hole.addRoundedRect(sel, radius, radius)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(ring.subtracted(hole), background_brush(background["preset"], outer))
    painter.restore()


# --------------------------------------------------------------------------
# Pixelate / blur previews from the frozen frame
# --------------------------------------------------------------------------


class HidePatches:
    """Pixelate/blur previews cut from the frozen frame, cached per rectangle."""

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
        key = (shape.mode, round(x), round(y), round(w), round(h))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        source = self._frozen.copy(
            round(x * scale), round(y * scale), max(1, round(w * scale)), max(1, round(h * scale))
        )
        if source.isNull():
            return None
        factor = _BLUR_FACTOR if shape.mode == "blur" else max(2.0, _PIXEL_BLOCK * scale)
        small = source.scaled(
            max(1, round(source.width() / factor)),
            max(1, round(source.height() / factor)),
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
    """Every marking except redactions on a transparent PNG of the area, as base64.

    ``scale`` is device pixels per logical pixel, so the overlay is as sharp
    as the screen. The main process resizes it onto the real capture, after
    it has pixelated or blurred the redactions on the capture's own pixels.
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
        if shape.kind != mm.REDACT:
            paint_shape(painter, shape)
    paint_spotlights(painter, shapes, sel)
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


def _glyphs() -> dict[str, Callable[[QPainter, QColor], None]]:  # noqa: C901 - one glyph table
    def move(p: QPainter, c: QColor) -> None:
        path = QPainterPath(QPointF(4, 3))
        for x, y in ((4, 15), (7.5, 11.5), (10, 16.5), (12, 15.5), (9.6, 10.6), (14.5, 10.6)):
            path.lineTo(QPointF(x, y))
        path.closeSubpath()
        p.drawPath(path)

    def rect(p: QPainter, c: QColor) -> None:
        p.drawRoundedRect(QRectF(3, 4.5, 12, 9), 2, 2)

    def filled(p: QPainter, c: QColor) -> None:
        p.setBrush(c)
        p.drawRoundedRect(QRectF(3, 4.5, 12, 9), 2, 2)

    def ellipse(p: QPainter, c: QColor) -> None:
        p.drawEllipse(QRectF(2.5, 4, 13, 10))

    def arrow(p: QPainter, c: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(_polygon(tapered_arrow_outline((3.5, 14.5), (15, 3), 2.2)))

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
        # A text field with a bold T in it — reads as "write text here".
        frame = QColor(c)
        frame.setAlpha(150)
        dashed = QPen(frame, 1.1, Qt.PenStyle.DashLine)
        p.setPen(dashed)
        p.drawRoundedRect(QRectF(1.5, 1.5, 15, 15), 3, 3)
        bold = QPen(c, 2.3)
        bold.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(bold)
        p.drawLine(QPointF(5.5, 5.5), QPointF(12.5, 5.5))
        p.drawLine(QPointF(9, 5.5), QPointF(9, 13))

    def counter(p: QPainter, c: QColor) -> None:
        p.drawEllipse(QRectF(2.5, 2.5, 13, 13))
        font = QFont()
        font.setPixelSize(9)
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRectF(2.5, 2.5, 13, 13), Qt.AlignmentFlag.AlignCenter, "1")

    def redact(p: QPainter, c: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            for j in range(3):
                shade = QColor(c)
                shade.setAlpha(255 if (i + j) % 2 == 0 else 110)
                p.fillRect(QRectF(3 + i * 4.2, 3 + j * 4.2, 3.6, 3.6), shade)

    def blur(p: QPainter, c: QColor) -> None:
        path = QPainterPath(QPointF(9, 2.5))
        path.cubicTo(QPointF(9, 2.5), QPointF(3.5, 9), QPointF(3.5, 11.5))
        path.cubicTo(QPointF(3.5, 14.5), QPointF(6, 16), QPointF(9, 16))
        path.cubicTo(QPointF(12, 16), QPointF(14.5, 14.5), QPointF(14.5, 11.5))
        path.cubicTo(QPointF(14.5, 9), QPointF(9, 2.5), QPointF(9, 2.5))
        p.drawPath(path)

    def spotlight(p: QPainter, c: QColor) -> None:
        p.drawEllipse(QRectF(4.5, 4.5, 9, 9))
        for x0, y0, x1, y1 in ((9, 1.5, 9, 3), (9, 15, 9, 16.5), (1.5, 9, 3, 9), (15, 9, 16.5, 9)):
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def background(p: QPainter, c: QColor) -> None:
        p.drawRoundedRect(QRectF(2, 3, 14, 12), 2.5, 2.5)
        p.drawEllipse(QPointF(6.5, 7), 1.4, 1.4)
        path = QPainterPath(QPointF(2.5, 13.5))
        path.lineTo(QPointF(7, 9.5))
        path.lineTo(QPointF(10, 12))
        path.lineTo(QPointF(12.5, 10))
        path.lineTo(QPointF(15.5, 13))
        p.drawPath(path)

    def classic(p: QPainter, c: QColor) -> None:
        p.drawLine(QPointF(4, 14), QPointF(13, 5))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(_polygon([(15, 3), (9.5, 4.6), (13.4, 8.5)]))

    def double(p: QPainter, c: QColor) -> None:
        p.drawLine(QPointF(5, 13), QPointF(13, 5))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(_polygon([(15, 3), (9.5, 4.6), (13.4, 8.5)]))
        p.drawPolygon(_polygon([(3, 15), (4.6, 9.5), (8.5, 13.4)]))

    def text_plain(p: QPainter, c: QColor) -> None:
        font = text_font(12)
        p.setFont(font)
        p.drawText(QRectF(0, 0, 18, 18), Qt.AlignmentFlag.AlignCenter, "Aa")

    def text_label(p: QPainter, c: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(1.5, 3.5, 15, 11), 3, 3)
        p.setPen(QColor(30, 30, 34) if c.lightness() > 128 else QColor(250, 250, 250))
        p.setFont(text_font(10))
        p.drawText(QRectF(0, 0, 18, 18), Qt.AlignmentFlag.AlignCenter, "Aa")

    def text_outline(p: QPainter, c: QColor) -> None:
        path = QPainterPath()
        path.addText(QPointF(1.5, 13.5), text_font(12), "Aa")
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 1.0))
        p.drawPath(path)

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

    def off(p: QPainter, c: QColor) -> None:
        p.drawEllipse(QRectF(3, 3, 12, 12))
        p.drawLine(QPointF(5, 13), QPointF(13, 5))

    return {
        mm.MOVE: move,
        mm.RECT: rect,
        mm.FILLED: filled,
        mm.ELLIPSE: ellipse,
        mm.ARROW: arrow,
        mm.LINE: line,
        mm.PEN: pen,
        mm.HIGHLIGHT: highlight,
        mm.TEXT: text,
        mm.COUNTER: counter,
        mm.REDACT: redact,
        mm.SPOTLIGHT: spotlight,
        mm.BACKGROUND: background,
        "arrow_tapered": arrow,
        "arrow_classic": classic,
        "arrow_double": double,
        "text_plain": text_plain,
        "text_label": text_label,
        "text_outline": text_outline,
        "redact_pixelate": redact,
        "redact_blur": blur,
        "background_off": off,
        "undo": undo,
        "redo": redo,
        "copy": copy,
        "save": save,
        "edit": edit,
        "cancel": cancel,
        "done": done,
    }


class Toolbar(QFrame):
    """The floating marking toolbar, docked to the selection by its window.

    One row: the tools, then — for a tool with looks to choose from (arrow,
    text, pixelate/blur, background) — those looks, then colours, size,
    history and the finishing actions.
    """

    tool_chosen = Signal(str)
    color_chosen = Signal(str)
    width_cycled = Signal()
    action = Signal(str)
    #: ``(group, value)``: arrow / text / redact style, or background preset
    #: (``"off"`` switches the frame off).
    option_chosen = Signal(str, str)

    _BTN = 30
    _SWATCH = 18

    def __init__(self, parent: QWidget, *, language: str, dpr: float) -> None:
        super().__init__(parent)
        self.setObjectName("hud")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._labels = labels_for(language)
        self._dpr = max(1.0, dpr)
        self._glyphs = _glyphs()
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
            QToolButton#option:checked {{ background: rgba(10, 132, 255, 90); }}
            QToolButton {{ border: none; border-radius: 7px; background: transparent; }}
            QToolButton:hover {{ background: {hover}; }}
            QToolButton:checked {{ background: #0A84FF; }}
            QToolButton#done {{ background: #0A84FF; }}
            QToolButton#done:hover {{ background: #3B9BFF; }}
            QToolButton#swatch:checked {{ background: transparent; }}
            QToolButton:disabled {{ background: transparent; }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)

        main = QHBoxLayout(self)
        main.setContentsMargins(6, 5, 6, 5)
        main.setSpacing(2)
        self._options = QHBoxLayout()
        self._options.setSpacing(2)
        self._option_buttons: list[QToolButton] = []

        self._tools = QButtonGroup(self)
        self._tools.setExclusive(True)
        self._tool_buttons: dict[str, QToolButton] = {}
        for kind in TOOL_ORDER:
            tip = f"{self._labels[kind]} ({mm.TOOL_KEYS[kind]})"
            button = self._button(kind, tip, checkable=True)
            self._tools.addButton(button)
            self._tool_buttons[kind] = button
            button.clicked.connect(lambda _c=False, k=kind: self.tool_chosen.emit(k))
            if kind == mm.BACKGROUND:
                main.addWidget(self._separator())
            main.addWidget(button)
        # The current tool's looks sit right after the tools, in the same row.
        self._optsep = self._separator()
        main.addWidget(self._optsep)
        main.addLayout(self._options)
        main.addWidget(self._separator())
        self._swatches: dict[str, QToolButton] = {}
        for colour in mm.PALETTE:
            swatch = QToolButton(self)
            swatch.setObjectName("swatch")
            swatch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            swatch.setCheckable(True)
            swatch.setFixedSize(QSize(self._SWATCH + 4, self._BTN))
            swatch.setIconSize(QSize(self._SWATCH, self._SWATCH))
            swatch.setToolTip(self._labels["color"])
            swatch.clicked.connect(lambda _c=False, col=colour: self.color_chosen.emit(col))
            self._swatches[colour] = swatch
            main.addWidget(swatch)
        self._width = self._button("width", self._labels["width"], glyph=False)
        self._width.clicked.connect(lambda _c=False: self.width_cycled.emit())
        main.addWidget(self._width)
        main.addWidget(self._separator())
        self._undo = self._action_button("undo", main)
        self._redo = self._action_button("redo", main)
        main.addWidget(self._separator())
        for name in ("copy", "save", "edit"):
            self._action_button(name, main)
        main.addWidget(self._separator())
        self._action_button("cancel", main)
        done = self._action_button("done", main, colour=QColor(255, 255, 255))
        done.setObjectName("done")
        self._colour = mm.PALETTE[0]
        self._width_value = mm.WIDTHS[mm.DEFAULT_WIDTH_INDEX]
        self.set_options([], "")
        self.adjustSize()

    # -- building ------------------------------------------------------------
    def _icon(self, name: str, colour: QColor | None = None) -> QIcon:
        icon = QIcon()
        draw = self._glyphs[name]
        normal = colour or self._fg
        disabled = QColor(normal)
        disabled.setAlpha(80)
        for tint, mode, state in (
            (normal, QIcon.Mode.Normal, QIcon.State.Off),
            (QColor(255, 255, 255), QIcon.Mode.Normal, QIcon.State.On),
            (disabled, QIcon.Mode.Disabled, QIcon.State.Off),
        ):
            icon.addPixmap(_icon_pixmap(draw, tint, self._dpr), mode, state)
        return icon

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
            button.setIcon(self._icon(name, colour))
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

    def _swatch_icon(self, colour: str, chosen: bool, *, square: bool = False) -> QIcon:
        size = self._SWATCH
        pixmap = QPixmap(round(size * self._dpr), round(size * self._dpr))
        pixmap.setDevicePixelRatio(self._dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if chosen:
            painter.setPen(QPen(self._fg, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            outer = QRectF(0.8, 0.8, size - 1.6, size - 1.6)
            if square:
                painter.drawRoundedRect(outer, 4, 4)
            else:
                painter.drawEllipse(outer)
        inset = 3.6 if chosen else 2.4
        inner = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
        painter.setPen(QPen(QColor(128, 128, 128, 150), 1.0))
        if square:
            painter.setBrush(background_brush(colour, inner))
            painter.drawRoundedRect(inner, 3, 3)
        else:
            painter.setBrush(QColor(colour))
            painter.drawEllipse(inner)
        painter.end()
        return QIcon(pixmap)

    # -- state ---------------------------------------------------------------
    def set_tool(self, kind: str) -> None:
        button = self._tool_buttons.get(kind)
        if button is not None:
            button.setChecked(True)

    def set_color(self, colour: str) -> None:
        self._colour = colour
        for value, swatch in self._swatches.items():
            chosen = value.upper() == colour.upper()
            swatch.setChecked(chosen)
            swatch.setIcon(self._swatch_icon(value, chosen))
        self._refresh_width()

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

    def set_options(self, options: list[tuple[str, str]], chosen: str, group: str = "") -> None:
        """Show the current tool's looks: ``(value, kind)``, ``kind`` = glyph or ``preset``."""
        # Empty the row completely — buttons and the stretch after them.
        while self._options.count():
            item = self._options.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._option_buttons = []
        for value, kind in options:
            button = QToolButton(self)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCheckable(True)
            button.setChecked(value == chosen)
            button.setFixedSize(QSize(self._BTN, self._BTN))
            button.setIconSize(QSize(18, 18))
            if kind == "preset":
                button.setObjectName("swatch")
                button.setIcon(self._swatch_icon(value, value == chosen, square=True))
                button.setToolTip(value)
            else:
                button.setObjectName("option")
                button.setIcon(self._icon(kind))
                button.setToolTip(self._labels.get(kind, value))
            button.clicked.connect(lambda _c=False, g=group, v=value: self.option_chosen.emit(g, v))
            self._options.addWidget(button)
            self._option_buttons.append(button)
        self._optsep.setVisible(bool(self._option_buttons))
        self.adjustSize()


__all__ = [
    "DIM_OUTSIDE",
    "TOOL_ORDER",
    "HidePatches",
    "Toolbar",
    "background_brush",
    "contrast_on",
    "labels_for",
    "paint_background_frame",
    "paint_picked",
    "paint_selection_frame",
    "paint_shape",
    "paint_spotlights",
    "paint_typing",
    "qt_measure",
    "render_overlay",
    "tapered_arrow_outline",
    "text_font",
]
