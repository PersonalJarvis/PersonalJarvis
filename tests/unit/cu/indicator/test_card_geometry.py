"""The corner card stays visible however small or thin the captured area is."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from jarvis.cu.indicator import renderer  # noqa: E402


def test_a_thin_strip_still_lands_as_a_full_card() -> None:
    card_w, card_h, pic_w, pic_h = renderer._card_geometry(853, 12)
    assert (card_w, card_h) == (renderer._SNAP_THUMB_W, renderer._CARD_MIN_H)
    assert pic_w == renderer._SNAP_THUMB_W and pic_h < 5


def test_a_tiny_area_keeps_its_real_size_inside_the_card() -> None:
    card_w, card_h, pic_w, pic_h = renderer._card_geometry(20, 20)
    assert (card_w, card_h) == (renderer._CARD_MIN_W, renderer._CARD_MIN_H)
    assert (pic_w, pic_h) == (20, 20)  # never scaled up


def test_a_tall_narrow_area_keeps_the_minimum_width() -> None:
    card_w, card_h, pic_w, pic_h = renderer._card_geometry(13, 400)
    assert card_w == renderer._CARD_MIN_W and card_h == renderer._SNAP_THUMB_MAX_H
    assert pic_h == renderer._SNAP_THUMB_MAX_H and pic_w < 10


def test_an_ordinary_area_fills_its_card() -> None:
    card_w, card_h, pic_w, pic_h = renderer._card_geometry(900, 600)
    assert (card_w, card_h) == (pic_w, pic_h) == (320, pytest.approx(213.33, abs=0.01))
