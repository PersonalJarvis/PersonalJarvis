"""OCR geometry and accessibility-density guards for Screen Context."""
from __future__ import annotations

import sys
from types import SimpleNamespace

from jarvis.screen_context.uitext import (
    ocr_supplement_with_regions,
    text_is_sparse,
)


def test_ocr_words_are_grouped_into_redactable_lines(monkeypatch) -> None:
    data = {
        "text": ["Card", "4111", "1111"],
        "left": [10, 55, 100],
        "top": [20, 20, 20],
        "width": [35, 35, 35],
        "height": [12, 12, 12],
        "block_num": [1, 1, 1],
        "par_num": [1, 1, 1],
        "line_num": [1, 1, 1],
    }
    fake = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda _image, *, output_type: data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    result = ocr_supplement_with_regions(object())

    assert result.text == "Card 4111 1111"
    assert result.regions[0].bounds == (10, 20, 125, 12)
    assert result.degradation is None


def test_sparse_text_threshold_scales_with_full_monitor_area() -> None:
    text = "A few toolbar labels that exceed the legacy fixed threshold"

    assert not text_is_sparse(text, image_size=(400, 200))
    assert text_is_sparse(text, image_size=(2048, 1152))
