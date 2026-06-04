"""Tests for the PointerElement data type (element under the cursor)."""

from __future__ import annotations

from jarvis.vision.pointer_types import PointerElement


def test_labeled_when_name_present() -> None:
    el = PointerElement(name="Submit", role="Button")
    assert el.is_labeled is True


def test_labeled_when_value_present() -> None:
    el = PointerElement(name="", role="Edit", value="hello@example.com")
    assert el.is_labeled is True


def test_unlabeled_image_is_not_labeled() -> None:
    # Pointing at a raster graphic with no accessible name/value: crop fallback.
    el = PointerElement(name="  ", role="Image", value="")
    assert el.is_labeled is False


def test_defaults_are_empty() -> None:
    el = PointerElement()
    assert el.role == ""
    assert el.bounds == (0, 0, 0, 0)
    assert el.is_labeled is False


def test_is_frozen() -> None:
    el = PointerElement(name="x")
    try:
        el.name = "y"  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("PointerElement should be immutable")
