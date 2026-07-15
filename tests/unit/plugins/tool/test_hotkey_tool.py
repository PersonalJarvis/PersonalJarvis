"""Combo-string tolerance for HotkeyTool.

Regression for the CU paste failure (2026-06-16, session 38134fab): the
screenshot-only Computer-Use loop tried to paste Elon-Musk posts into the
BridgeMind Discord and the model emitted the shortcut as a SINGLE token
``"ctrl+v"`` instead of the documented list form ``["ctrl", "v"]``. The tool
looked up a key literally named "ctrl+v", never found it, and failed three
times in a row -> the mission circled into the guard-hit cap and died.

The fix: accept a combined hotkey string and split it on '+' into its
component keys before resolving. The list form must keep working unchanged.
"""
from __future__ import annotations

from jarvis.plugins.tool import hotkey as hk
from jarvis.plugins.tool.hotkey import HotkeyTool


class _Ctx:
    user_utterance = "computer-use"


def _capture_native(monkeypatch):
    """Patch the Windows path so the test records the keys without real input."""
    sent: dict[str, list[str]] = {}

    def _fake(keys):
        sent["keys"] = list(keys)

    monkeypatch.setattr(hk.os, "name", "nt")
    monkeypatch.setattr(hk, "_send_hotkey_windows", _fake)
    return sent


async def test_combo_string_is_split_into_modifier_and_key(monkeypatch):
    sent = _capture_native(monkeypatch)

    res = await HotkeyTool().execute({"keys": ["ctrl+v"]}, _Ctx())

    assert res.success is True
    assert sent["keys"] == ["ctrl", "v"]


async def test_three_part_combo_string_is_split(monkeypatch):
    sent = _capture_native(monkeypatch)

    res = await HotkeyTool().execute({"keys": ["ctrl+shift+t"]}, _Ctx())

    assert res.success is True
    assert sent["keys"] == ["ctrl", "shift", "t"]


async def test_list_form_still_works_unchanged(monkeypatch):
    sent = _capture_native(monkeypatch)

    res = await HotkeyTool().execute({"keys": ["ctrl", "v"]}, _Ctx())

    assert res.success is True
    assert sent["keys"] == ["ctrl", "v"]


async def test_single_key_is_untouched(monkeypatch):
    sent = _capture_native(monkeypatch)

    res = await HotkeyTool().execute({"keys": ["enter"]}, _Ctx())

    assert res.success is True
    assert sent["keys"] == ["enter"]


async def test_literal_plus_token_is_not_destroyed(monkeypatch):
    """A lone '+' must not be split into empty parts and vanish."""
    _capture_native(monkeypatch)

    # "ctrl" + the literal plus key; only split points that resolve are taken.
    res = await HotkeyTool().execute({"keys": ["ctrl", "+"]}, _Ctx())

    # '+' is not a resolvable key on its own -> the tool rejects it cleanly
    # rather than silently dropping it.
    assert res.success is False
    assert "+" in (res.error or "")


async def test_unknown_combo_part_still_reports_clean_error(monkeypatch):
    _capture_native(monkeypatch)

    res = await HotkeyTool().execute({"keys": ["ctrl+nope"]}, _Ctx())

    # Not all parts resolve -> keep the token verbatim and surface the
    # existing "Unknown key" error instead of a confusing split.
    assert res.success is False
    assert "Unknown key" in (res.error or "")


async def test_macos_command_combo_reaches_posix_actuator(monkeypatch):
    sent: list[list[str]] = []

    class _Actuator:
        name = "fake-macos"

        def key_combo(self, keys):
            sent.append(list(keys))

    monkeypatch.setattr(hk.os, "name", "posix")
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: _Actuator())

    res = await HotkeyTool().execute({"keys": ["cmd+a"]}, _Ctx())

    assert res.success is True
    assert sent == [["cmd", "a"]]


async def test_macos_option_alias_reaches_posix_actuator(monkeypatch):
    sent: list[list[str]] = []

    class _Actuator:
        name = "fake-macos"

        def key_combo(self, keys):
            sent.append(keys)

    monkeypatch.setattr(hk.os, "name", "posix")
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: _Actuator())

    result = await HotkeyTool().execute({"keys": ["option", "left"]}, _Ctx())

    assert result.success is True
    assert sent == [["option", "left"]]


async def test_screenshot_bound_hotkey_refuses_changed_foreground(monkeypatch):
    sent: list[list[str]] = []

    class _Actuator:
        name = "fake-macos"

        def key_combo(self, keys):
            sent.append(list(keys))

    monkeypatch.setattr(hk.os, "name", "posix")
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: _Actuator())
    monkeypatch.setattr("jarvis.cu.target_guard.foreground_matches", lambda _: False)

    result = await HotkeyTool().execute(
        {
            "keys": ["cmd", "a"],
            "_expected_window_signature": ("handle", 7, (0, 0, 800, 600)),
        },
        _Ctx(),
    )

    assert result.success is False
    assert "foreground window changed" in (result.error or "")
    assert sent == []
