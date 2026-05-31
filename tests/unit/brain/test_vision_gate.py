"""Tests for the conservative skip-when-safe vision gate (Wave 1).

Contract: the screenshot is dropped ONLY for confidently text-only turns
(smalltalk / simple Q&A) that contain no visual-reference marker. Anything
not classified as smalltalk, and anything with a deictic/visual marker, keeps
the image. This is the anti-regression stance after the 2026-04-28 incident
where on-demand-only vision made the router hallucinate a blank desktop.
"""
from __future__ import annotations

from jarvis.brain.vision_gate import has_visual_marker, should_attach_screenshot


def test_smalltalk_without_marker_drops_image() -> None:
    # The headline win: "what time is it" no longer pays the vision tax.
    assert should_attach_screenshot("wie spät ist es", is_smalltalk=True) is False
    assert should_attach_screenshot("hallo jarvis", is_smalltalk=True) is False
    assert should_attach_screenshot("danke dir", is_smalltalk=True) is False


def test_non_smalltalk_always_keeps_image() -> None:
    # Action / screen-ref / unknown intent: keep the image (conservative).
    assert should_attach_screenshot("öffne den browser", is_smalltalk=False) is True
    assert should_attach_screenshot("was siehst du hier", is_smalltalk=False) is True
    assert should_attach_screenshot("erklär mir was ein vektor ist", is_smalltalk=False) is True


def test_visual_marker_beats_smalltalk_classification() -> None:
    # Even if the classifier called it smalltalk, a visual reference keeps the image.
    assert should_attach_screenshot("schau mal das hier", is_smalltalk=True) is True
    assert should_attach_screenshot("was ist das da", is_smalltalk=True) is True
    assert should_attach_screenshot("klick das weg", is_smalltalk=True) is True


def test_visual_marker_detection_is_case_insensitive() -> None:
    assert has_visual_marker("Was ist DAS HIER") is True
    assert has_visual_marker("look at this") is True
    assert has_visual_marker("auf dem Bildschirm") is True


def test_plain_smalltalk_has_no_marker() -> None:
    assert has_visual_marker("wie geht es dir") is False
    assert has_visual_marker("guten morgen") is False
