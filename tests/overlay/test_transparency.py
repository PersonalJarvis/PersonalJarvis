"""ctypes wrappers: mock user32, verify flag combinations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from overlay import transparency
from overlay.transparency import (
    GWL_EXSTYLE,
    WDA_EXCLUDEFROMCAPTURE,
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TRANSPARENT,
    apply_click_through,
    apply_mascot_styles,
    exclude_from_capture,
)


@pytest.fixture
def fake_user32():
    """Faked user32 — capture last GetWindowLongW return + SetWindowLongW args."""
    user32 = MagicMock()
    user32.GetWindowLongW.return_value = 0  # baseline: no flags set
    user32.SetWindowLongW.return_value = 0
    user32.SetWindowDisplayAffinity.return_value = 1  # success
    return user32


def test_apply_click_through_sets_layered_and_transparent(fake_user32, monkeypatch) -> None:
    monkeypatch.setattr(transparency, "_is_windows", lambda: True)
    monkeypatch.setattr(transparency, "get_user32", lambda: fake_user32)

    apply_click_through(hwnd=0x12345)

    fake_user32.GetWindowLongW.assert_called_once()
    args, _ = fake_user32.SetWindowLongW.call_args
    _hwnd, idx, new_style = args
    assert idx == GWL_EXSTYLE
    assert new_style & WS_EX_LAYERED
    assert new_style & WS_EX_TRANSPARENT


def test_apply_click_through_idempotent(fake_user32, monkeypatch) -> None:
    """When both flags are already set, SetWindowLongW is NOT called."""
    monkeypatch.setattr(transparency, "_is_windows", lambda: True)
    monkeypatch.setattr(transparency, "get_user32", lambda: fake_user32)
    fake_user32.GetWindowLongW.return_value = WS_EX_LAYERED | WS_EX_TRANSPARENT

    apply_click_through(hwnd=0x12345)

    fake_user32.SetWindowLongW.assert_not_called()


def test_apply_mascot_styles_sets_three_flags(fake_user32, monkeypatch) -> None:
    monkeypatch.setattr(transparency, "_is_windows", lambda: True)
    monkeypatch.setattr(transparency, "get_user32", lambda: fake_user32)

    apply_mascot_styles(hwnd=0xABCDEF)

    args, _ = fake_user32.SetWindowLongW.call_args
    _hwnd, idx, new_style = args
    assert idx == GWL_EXSTYLE
    assert new_style & WS_EX_LAYERED
    assert new_style & WS_EX_NOACTIVATE
    assert new_style & WS_EX_TOOLWINDOW
    # Mascot must NOT be click-through.
    assert not (new_style & WS_EX_TRANSPARENT)


def test_exclude_from_capture_calls_with_correct_constant(fake_user32, monkeypatch) -> None:
    monkeypatch.setattr(transparency, "_is_windows", lambda: True)
    monkeypatch.setattr(transparency, "get_user32", lambda: fake_user32)

    ok = exclude_from_capture(hwnd=0x42)

    assert ok is True
    args, _ = fake_user32.SetWindowDisplayAffinity.call_args
    _hwnd, dwAffinity = args
    assert dwAffinity.value == WDA_EXCLUDEFROMCAPTURE


def test_noop_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(transparency, "_is_windows", lambda: False)
    # Must not crash, must not load user32.
    apply_click_through(hwnd=0x1)
    apply_mascot_styles(hwnd=0x1)
    assert exclude_from_capture(hwnd=0x1) is False


def test_set_per_monitor_dpi_awareness_swallows_errors(monkeypatch) -> None:
    monkeypatch.setattr(transparency, "_is_windows", lambda: True)
    fake_shcore = MagicMock()
    fake_shcore.SetProcessDpiAwareness.side_effect = OSError("E_ACCESSDENIED")
    fake_windll = MagicMock(shcore=fake_shcore)
    with patch("overlay.transparency.ctypes") as mock_ctypes:
        mock_ctypes.windll = fake_windll
        # Must not raise.
        transparency.set_per_monitor_dpi_awareness()
