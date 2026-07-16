"""Transport-selection guard for TypeTextTool.

Regression for the CU typo bug (2026-06-15): on Windows the tool must prefer the
native KEYEVENTF_UNICODE SendInput path (layout-independent, exact codepoints)
over pyautogui's layout-dependent virtual-key typing, which garbled characters
into a Tauri/webview terminal under a German QWERTZ layout. pyautogui stays a
fallback when the native path fails (and the primary on non-Windows).
"""
from __future__ import annotations

import ctypes
import sys
import types

import pytest

from jarvis.plugins.tool import type_text as tt
from jarvis.plugins.tool.type_text import TypeTextTool

try:  # ``ctypes.wintypes`` is importable cross-platform, but guard for safety.
    from ctypes import wintypes as _wt  # noqa: F401

    _HAS_WINTYPES = True
except Exception:  # pragma: no cover - exotic builds without wintypes
    _HAS_WINTYPES = False


class _Ctx:
    user_utterance = "type hello"


def _install_fake_pyautogui(monkeypatch, calls):
    fake = types.ModuleType("pyautogui")

    def _typewrite(text, interval=0.0):
        calls["pyautogui_text"] = text

    fake.typewrite = _typewrite
    monkeypatch.setitem(sys.modules, "pyautogui", fake)


async def test_windows_prefers_native_unicode_over_pyautogui(monkeypatch):
    calls = {"native_text": None, "pyautogui_text": None}

    def _fake_native(text, delay_s):
        calls["native_text"] = text

    monkeypatch.setattr(tt.os, "name", "nt")
    monkeypatch.setattr(tt, "_send_text_windows", _fake_native)
    _install_fake_pyautogui(monkeypatch, calls)

    res = await TypeTextTool().execute({"text": "hello hello hello"}, _Ctx())

    assert res.success is True
    assert calls["native_text"] == "hello hello hello"
    assert calls["pyautogui_text"] is None  # native won; pyautogui untouched
    assert "Unicode" in (res.output or "")


async def test_windows_falls_back_to_pyautogui_when_native_fails(monkeypatch):
    calls = {"pyautogui_text": None}

    def _boom(text, delay_s):
        raise OSError("SendInput returned 0")

    monkeypatch.setattr(tt.os, "name", "nt")
    monkeypatch.setattr(tt, "_send_text_windows", _boom)
    _install_fake_pyautogui(monkeypatch, calls)

    res = await TypeTextTool().execute({"text": "abc"}, _Ctx())

    assert res.success is True
    assert calls["pyautogui_text"] == "abc"


async def test_empty_text_is_rejected(monkeypatch):
    res = await TypeTextTool().execute({"text": ""}, _Ctx())
    assert res.success is False


async def test_screenshot_bound_typing_refuses_changed_foreground(monkeypatch):
    typed: list[str] = []

    class _Actuator:
        name = "fake-macos"

        def type_text(self, text, *, delay_s=0.02):
            typed.append(text)

    monkeypatch.setattr(tt.os, "name", "posix")
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: _Actuator())
    monkeypatch.setattr("jarvis.cu.target_guard.foreground_matches", lambda _: False)

    result = await TypeTextTool().execute(
        {
            "text": "secret target text",
            "_expected_window_signature": ("handle", 7, (0, 0, 800, 600)),
        },
        _Ctx(),
    )

    assert result.success is False
    assert "foreground window changed" in (result.error or "")
    assert typed == []


# ---------------------------------------------------------------------------
# RC#1 regression (Google-Flights typing bug, 2026-06-22): the SendInput INPUT
# union must be sized to its LARGEST member (MOUSEINPUT). If it only carries
# ``ki`` (KEYBDINPUT) the struct is too small (32 vs 40 bytes on x64), Windows
# rejects every keystroke with ERROR_INVALID_PARAMETER ("Falscher Parameter"),
# and typing silently degrades to the layout-dependent pyautogui fallback that
# does not register in web inputs ("typed into the right field, nothing
# appeared").
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_WINTYPES, reason="ctypes.wintypes unavailable")
def test_input_union_is_sized_to_largest_member():
    t = tt._build_windows_input_types()
    # MOUSEINPUT is larger than KEYBDINPUT, so a union that omits it is undersized.
    assert ctypes.sizeof(t.MOUSEINPUT) > ctypes.sizeof(t.KEYBDINPUT)
    # The union (and therefore INPUT.cbSize) must match the largest member.
    assert ctypes.sizeof(t.INPUT_UNION) == ctypes.sizeof(t.MOUSEINPUT)


@pytest.mark.skipif(not _HAS_WINTYPES, reason="ctypes.wintypes unavailable")
def test_input_struct_size_matches_windows_cbsize():
    t = tt._build_windows_input_types()
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(t.INPUT) == expected
