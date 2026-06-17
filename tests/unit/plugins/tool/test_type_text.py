"""Transport-selection guard for TypeTextTool.

Regression for the CU typo bug (2026-06-15): on Windows the tool must prefer the
native KEYEVENTF_UNICODE SendInput path (layout-independent, exact codepoints)
over pyautogui's layout-dependent virtual-key typing, which garbled characters
into a Tauri/webview terminal under a German QWERTZ layout. pyautogui stays a
fallback when the native path fails (and the primary on non-Windows).
"""
from __future__ import annotations

import sys
import types

from jarvis.plugins.tool import type_text as tt
from jarvis.plugins.tool.type_text import TypeTextTool


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
