"""The lifted picture and pointer badge while the appshot card is dragged.

Runs on Qt's offscreen platform; auto-skips without PySide6 (a [desktop] extra).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="PySide6 not installed (indicator sidecar is a [desktop] extra)",
)


@pytest.fixture(scope="module")
def qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    app = QApplication.instance() or QApplication([])
    from jarvis.cu.indicator import renderer  # noqa: PLC0415

    yield renderer
    del app


def _thumb(width: int, height: int):
    from PySide6.QtGui import QColor, QImage  # noqa: PLC0415

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(200, 210, 230))
    return image


def test_preview_is_smaller_than_the_card_and_sharp(qt) -> None:
    from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415

    card = QRectF(12, 12, 320, 200)
    pixmap, _hot = qt._drag_preview(_thumb(560, 350), card, QPointF(172, 112), 2.0)
    pad = qt._DRAG_PREVIEW_PAD
    logical_w = pixmap.width() / pixmap.devicePixelRatio()
    assert pixmap.devicePixelRatio() == 2.0
    assert logical_w - 2 * pad == pytest.approx(qt._DRAG_PREVIEW_W, abs=1)
    assert logical_w - 2 * pad < card.width()


def test_hotspot_keeps_the_grab_point(qt) -> None:
    from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415

    card = QRectF(12, 12, 320, 200)
    pad = qt._DRAG_PREVIEW_PAD
    w = qt._DRAG_PREVIEW_W
    h = w * 350 / 560
    # Grabbed at the card's centre: the hotspot is the preview's centre.
    _pm, hot = qt._drag_preview(_thumb(560, 350), card, QPointF(172, 112), 1.0)
    assert (hot.x(), hot.y()) == (int(pad + w / 2), int(pad + h / 2))
    # Grabbed in the shadow margin: clamped onto the picture's corner.
    _pm, hot = qt._drag_preview(_thumb(560, 350), card, QPointF(0, 0), 1.0)
    assert (hot.x(), hot.y()) == (int(pad), int(pad))


def test_tall_picture_is_capped_by_height(qt) -> None:
    from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415

    pixmap, _hot = qt._drag_preview(_thumb(300, 900), QRectF(0, 0, 80, 240), QPointF(40, 120), 1.0)
    assert pixmap.height() - 2 * qt._DRAG_PREVIEW_PAD == pytest.approx(qt._DRAG_PREVIEW_H, abs=1)


def test_copy_cursor_draws_the_green_badge(qt) -> None:
    pixmap = qt._drag_copy_cursor(1.0)
    image = pixmap.toImage()
    badge = image.pixelColor(20 + 6, 22)  # inside the circle, off the plus
    assert (badge.red(), badge.green(), badge.blue()) == (52, 199, 89)
    tip = image.pixelColor(2, 4)  # inside the white arrow, by its tip
    assert tip.alpha() > 0


class _DropOwner:
    """Just enough of the renderer for a card drag: where the drag file goes."""

    def __init__(self, path) -> None:
        self._path = path

    def write_drag_file(self, _image):
        return self._path

    def card_gone(self, _card) -> None:
        pass


def _fake_drag(result):
    class _Drag:
        def __init__(self, _source) -> None:
            pass

        def setMimeData(self, _mime) -> None:  # noqa: N802 - Qt name
            pass

        def setPixmap(self, _pixmap) -> None:  # noqa: N802 - Qt name
            pass

        def setHotSpot(self, _hot) -> None:  # noqa: N802 - Qt name
            pass

        def setDragCursor(self, _cursor, _action) -> None:  # noqa: N802 - Qt name
            pass

        def exec(self, _action):
            return result

    return _Drag


def _dropped_card(qt, monkeypatch, tmp_path, *, pinned: bool):
    from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: PLC0415

    monkeypatch.setattr(qt, "QDrag", _fake_drag(Qt.DropAction.CopyAction))
    path = tmp_path / "appshot.png"
    path.write_bytes(b"")
    card = qt._CardWindow(
        QRectF(0, 0, 320, 200),
        _thumb(320, 200),
        "",
        _DropOwner(path),
        image=_thumb(320, 200),
    )
    card._pinned = pinned
    card._start_drag(QPointF(100, 100))
    return card


def test_dropped_card_leaves(qt, monkeypatch, tmp_path) -> None:
    card = _dropped_card(qt, monkeypatch, tmp_path, pinned=False)
    assert card.leaving
    card.finish_now()


def test_pinned_card_stays_after_a_drop(qt, monkeypatch, tmp_path) -> None:
    card = _dropped_card(qt, monkeypatch, tmp_path, pinned=True)
    assert not card.leaving
    assert not card._dismiss.isActive()
    card.finish_now()
