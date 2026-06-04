"""Tests for the per-OS element-at-point resolver (AI Pointer step 3).

The native point queries (UIA ElementFromPoint / AX CopyElementAtPosition /
AT-SPI getAccessibleAtPoint) are not CI-testable, so each resolver takes an
injectable ``query`` callable — the wrapper logic (error-swallowing, coord
pass-through, factory selection) is fake-tested here; the native path is
live-verified on Windows and labelled unverified-on-real-desktop for Mac/Linux.
"""

from __future__ import annotations

import sys

import pytest

from jarvis.vision import element_at_point as eap
from jarvis.vision.pointer_types import PointerElement
from tests.fakes.fake_capabilities import (
    fake_headless_capabilities,
    fake_linux_capabilities,
    fake_macos_capabilities,
    fake_windows_capabilities,
)

_OS_RESOLVERS = (
    eap.WindowsPointerResolver,
    eap.AXPointerResolver,
    eap.AtspiPointerResolver,
)


def test_null_resolver_returns_none() -> None:
    assert eap.NullPointerResolver().at(10, 20) is None


def test_resolver_returns_injected_element() -> None:
    sentinel = PointerElement(name="Crab", role="Image", bounds=(1, 2, 3, 4))
    resolver = eap.WindowsPointerResolver(query=lambda x, y: sentinel)
    assert resolver.at(5, 6) is sentinel


@pytest.mark.parametrize("cls", _OS_RESOLVERS)
def test_resolver_swallows_query_errors(cls) -> None:
    def boom(x: int, y: int) -> PointerElement:
        raise RuntimeError("native query failed")

    assert cls(query=boom).at(1, 1) is None


@pytest.mark.parametrize("cls", _OS_RESOLVERS)
def test_resolver_passes_coords_to_query(cls) -> None:
    seen: dict[str, tuple[int, int]] = {}

    def q(x: int, y: int) -> None:
        seen["xy"] = (x, y)
        return None

    cls(query=q).at(42, 99)
    assert seen["xy"] == (42, 99)


def test_factory_windows(monkeypatch) -> None:
    monkeypatch.setattr(eap, "detect_platform", lambda: "win32")
    monkeypatch.setattr(eap, "detect_capabilities", fake_windows_capabilities)
    assert isinstance(eap.make_pointer_resolver(), eap.WindowsPointerResolver)


def test_factory_macos(monkeypatch) -> None:
    monkeypatch.setattr(eap, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(eap, "detect_capabilities", fake_macos_capabilities)
    assert isinstance(eap.make_pointer_resolver(), eap.AXPointerResolver)


def test_factory_linux(monkeypatch) -> None:
    monkeypatch.setattr(eap, "detect_platform", lambda: "linux")
    monkeypatch.setattr(eap, "detect_capabilities", fake_linux_capabilities)
    assert isinstance(eap.make_pointer_resolver(), eap.AtspiPointerResolver)


def test_factory_null_when_no_ax_tree(monkeypatch) -> None:
    monkeypatch.setattr(eap, "detect_platform", lambda: "linux")
    monkeypatch.setattr(eap, "detect_capabilities", fake_headless_capabilities)
    assert isinstance(eap.make_pointer_resolver(), eap.NullPointerResolver)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA point query (live)")
def test_windows_live_does_not_raise() -> None:
    resolver = eap.make_pointer_resolver()
    result = resolver.at(100, 100)
    assert result is None or isinstance(result, PointerElement)
