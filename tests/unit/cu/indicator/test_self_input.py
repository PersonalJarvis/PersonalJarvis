"""Self-typed-Escape suppression stamps (jarvis/cu/indicator/self_input)."""
from __future__ import annotations

import pytest

from jarvis.cu.indicator import self_input


@pytest.fixture(autouse=True)
def _clean_stamp():
    self_input.reset()
    yield
    self_input.reset()


def test_escape_combo_stamps_and_suppresses() -> None:
    assert self_input.stamp_if_escape(["esc"]) is True
    assert self_input.esc_recently_synthesized() is True


def test_escape_full_name_and_case_insensitive() -> None:
    assert self_input.stamp_if_escape(["Escape"]) is True
    assert self_input.stamp_if_escape(["ESC"]) is True


def test_non_escape_combo_does_not_stamp() -> None:
    assert self_input.stamp_if_escape(["ctrl", "s"]) is False
    assert self_input.esc_recently_synthesized() is False


def test_suppression_window_expires() -> None:
    self_input.stamp_if_escape(["esc"])
    # A zero-width window means the stamp is already stale.
    assert self_input.esc_recently_synthesized(window_ms=0) is False
    assert self_input.esc_recently_synthesized(window_ms=60_000) is True


def test_reset_clears_stamp() -> None:
    self_input.stamp_if_escape(["esc"])
    self_input.reset()
    assert self_input.esc_recently_synthesized() is False
