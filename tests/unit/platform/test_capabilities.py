"""Tests for the shared platform-capability layer (Wave 0, sub-task 0.1)."""

from __future__ import annotations

import sys

import pytest

from jarvis.platform import detect_platform
from jarvis.platform.capabilities import (
    Capabilities,
    detect_capabilities,
    reset_capabilities_cache,
)
from tests.fakes.fake_capabilities import (
    fake_headless_capabilities,
    fake_linux_capabilities,
    fake_macos_capabilities,
    fake_windows_capabilities,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_capabilities_cache()
    yield
    reset_capabilities_cache()


def test_detect_platform_maps_known_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert detect_platform() == "win32"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert detect_platform() == "darwin"
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_platform() == "linux"
    # Some Pythons report 'linux2' historically — must still map to linux.
    monkeypatch.setattr(sys, "platform", "linux2")
    assert detect_platform() == "linux"


def test_detect_platform_unknown_falls_back_to_linux_without_raising(monkeypatch):
    monkeypatch.setattr(sys, "platform", "aix")
    # AD-6: never raises; POSIX default.
    assert detect_platform() == "linux"


def test_detect_capabilities_is_cached_identity():
    first = detect_capabilities()
    second = detect_capabilities()
    assert first is second  # cache identity (acceptance criterion 0.1)


def test_reset_capabilities_cache_forces_recompute():
    first = detect_capabilities()
    reset_capabilities_cache()
    second = detect_capabilities()
    assert first is not second
    assert first == second  # same values, different instance


def test_capabilities_is_frozen():
    caps = detect_capabilities()
    with pytest.raises((AttributeError, TypeError)):
        caps.has_hotkey = True  # type: ignore[misc]


def test_capabilities_platform_matches_detect_platform():
    assert detect_capabilities().platform == detect_platform()


def test_real_host_capabilities_have_correct_types():
    caps = detect_capabilities()
    assert isinstance(caps, Capabilities)
    for field in (
        "has_hotkey",
        "has_ax_tree",
        "has_overlay",
        "has_pty",
        "has_elevation",
        "display_present",
        "is_wayland",
    ):
        assert isinstance(getattr(caps, field), bool), field
    assert caps.ax_permission_granted in (True, False, None)


@pytest.mark.parametrize(
    "factory,expected_platform",
    [
        (fake_windows_capabilities, "win32"),
        (fake_macos_capabilities, "darwin"),
        (fake_linux_capabilities, "linux"),
        (fake_headless_capabilities, "linux"),
    ],
)
def test_fakes_construct_per_platform(factory, expected_platform):
    caps = factory()
    assert isinstance(caps, Capabilities)
    assert caps.platform == expected_platform


def test_fake_overrides_apply():
    caps = fake_macos_capabilities(has_hotkey=False, is_wayland=True)
    assert caps.has_hotkey is False
    assert caps.is_wayland is True
    assert caps.platform == "darwin"


def test_headless_fake_has_no_gui_features():
    caps = fake_headless_capabilities()
    assert caps.display_present is False
    assert caps.has_overlay is False
    assert caps.has_hotkey is False
    assert caps.has_pty is True  # a VPS still has a PTY
