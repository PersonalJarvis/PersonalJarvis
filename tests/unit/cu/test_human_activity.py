"""Tests for the side-effect-free macOS physical-input detector."""
from __future__ import annotations

import pytest

from jarvis.cu import human_activity


def test_non_macos_is_advisory_noop() -> None:
    activity = human_activity.macos_human_activity(
        platform="linux",
        seconds_since_input=lambda: pytest.fail("non-macOS must not probe Quartz"),
    )

    assert activity.available is False
    assert activity.recent is False
    allowed, _detail = human_activity.human_input_allows_automation(
        platform="linux",
        seconds_since_input=lambda: pytest.fail("non-macOS must not probe Quartz"),
    )
    assert allowed is True


def test_recent_hardware_input_yields() -> None:
    activity = human_activity.macos_human_activity(
        platform="darwin",
        grace_s=0.25,
        seconds_since_input=lambda: 0.04,
    )

    assert activity.available is True
    assert activity.recent is True
    assert activity.seconds_since_input == pytest.approx(0.04)
    allowed, detail = human_activity.human_input_allows_automation(
        platform="darwin",
        grace_s=0.25,
        seconds_since_input=lambda: 0.04,
    )
    assert allowed is False
    assert "yielding" in detail


def test_quiet_hardware_input_allows_automation() -> None:
    allowed, detail = human_activity.human_input_allows_automation(
        platform="darwin",
        grace_s=0.25,
        seconds_since_input=lambda: 3.0,
    )

    assert allowed is True
    assert "3.000s" in detail


def test_unavailable_quartz_fails_closed_on_macos() -> None:
    allowed, detail = human_activity.human_input_allows_automation(
        platform="darwin",
        seconds_since_input=lambda: None,
    )

    assert allowed is False
    assert "state unknown" in detail
    assert "unavailable" in detail


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, "bad"])
def test_invalid_quartz_age_fails_closed_on_macos(value: object) -> None:
    activity = human_activity.macos_human_activity(
        platform="darwin",
        seconds_since_input=lambda: value,  # type: ignore[return-value]
    )
    allowed, detail = human_activity.human_input_allows_automation(
        platform="darwin",
        seconds_since_input=lambda: value,  # type: ignore[return-value]
    )

    assert activity.available is False
    assert activity.recent is False
    assert allowed is False
    assert "state unknown" in detail


def test_negative_grace_is_rejected() -> None:
    with pytest.raises(ValueError):
        human_activity.macos_human_activity(platform="darwin", grace_s=-0.01)
