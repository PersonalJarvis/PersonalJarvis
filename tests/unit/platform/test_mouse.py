"""Tests for the cross-platform cursor-position backend (AI Pointer step 2).

Mirrors the hotkey-backend seam tests: a Protocol, a per-OS implementation, a
``sys.platform`` factory, and a logged null-fallback that returns ``None`` (never
raises) on a headless host.
"""

from __future__ import annotations

import sys

from jarvis.platform import mouse, probes
from tests.fakes.fake_capabilities import (
    fake_headless_capabilities,
    fake_linux_capabilities,
)


def test_null_backend_position_is_none() -> None:
    assert mouse.NullCursorBackend().position() is None


def test_factory_returns_backend_with_position() -> None:
    backend = mouse.make_cursor_backend()
    assert callable(backend.position)


def test_factory_on_windows_returns_windows_backend(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "detect_platform", lambda: "win32")
    assert isinstance(mouse.make_cursor_backend(), mouse.WindowsCursorBackend)


def test_factory_linux_x11_with_cursor_returns_pynput(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "detect_platform", lambda: "linux")
    monkeypatch.setattr(
        mouse, "detect_capabilities", lambda: fake_linux_capabilities(has_cursor=True)
    )
    assert isinstance(mouse.make_cursor_backend(), mouse.PynputCursorBackend)


def test_factory_selects_null_when_no_cursor(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "detect_platform", lambda: "linux")
    monkeypatch.setattr(mouse, "detect_capabilities", fake_headless_capabilities)
    backend = mouse.make_cursor_backend()
    assert isinstance(backend, mouse.NullCursorBackend)
    assert backend.position() is None


def test_has_cursor_probe_returns_bool() -> None:
    assert isinstance(probes.has_cursor(), bool)


def test_has_cursor_true_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(probes, "detect_platform", lambda: "win32")
    assert probes.has_cursor() is True


def test_has_cursor_false_when_no_display(monkeypatch) -> None:
    monkeypatch.setattr(probes, "detect_platform", lambda: "linux")
    monkeypatch.setattr(probes, "display_present", lambda: False)
    assert probes.has_cursor() is False


import pytest  # noqa: E402


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cursor backend (live)")
def test_windows_backend_returns_int_tuple() -> None:
    pos = mouse.WindowsCursorBackend().position()
    assert pos is not None
    x, y = pos
    assert isinstance(x, int) and isinstance(y, int)
