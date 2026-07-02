"""H2: switch_window must work on macOS (osascript) and Linux/X11 (wmctrl),
degrade cleanly on Wayland / headless / missing-tool with a clear English
message, and leave the Windows ctypes path untouched (AD-7).

Seam-level only: the platform is forced via detect_platform/probes and the
osascript/wmctrl subprocesses are faked — this proves the dispatch + parsing,
NOT that real osascript/wmctrl behave as assumed on real hardware.
"""
from __future__ import annotations

import subprocess
import types

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.switch_window import (
    SwitchWindowTool,
    _find_and_focus_linux,
    _find_and_focus_macos,
)


def _cp(returncode: int, stdout: str = "", stderr: str = ""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _ctx() -> ExecutionContext:
    # switch_window.execute does not read ctx; supply the required fields.
    return ExecutionContext(
        config={}, trace_id=None, user_utterance="", memory_read=None
    )


# --- macOS (osascript) -----------------------------------------------------


def test_macos_focus_success(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp(0, stdout="My Editor\n"))
    found, msg = _find_and_focus_macos("Editor")
    assert found is True
    assert msg == "My Editor"


def test_macos_accessibility_denied_message(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    err = "System Events got an error: osascript is not allowed assistive access. (-1719)"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp(1, stderr=err))
    found, msg = _find_and_focus_macos("Editor")
    assert found is False
    assert "Accessibility" in msg


def test_macos_missing_osascript(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    found, msg = _find_and_focus_macos("Editor")
    assert found is False
    assert "osascript" in msg.lower()


def test_macos_no_match(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp(0, stdout="\n"))
    found, msg = _find_and_focus_macos("Nope")
    assert found is False
    assert "Nope" in msg


def test_macos_escapes_applescript_injection(monkeypatch):
    # A crafted title with a newline must NOT break out of the contains "..."
    # literal and inject a new AppleScript statement (review HIGH).
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    captured: dict[str, str] = {}

    def fake_run(cmd, *a, **k):
        captured["script"] = cmd[2]  # ["osascript", "-e", <script>]
        return _cp(0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _find_and_focus_macos('x"\ndo shell script "evil"')
    script = captured["script"]
    # No raw newline may precede the injected statement — it must be escaped.
    assert "\ndo shell script" not in script
    # The substring is still present, only as an escaped literal (not dropped).
    assert "do shell script" in script


def test_macos_matching_is_case_insensitive(monkeypatch):
    # Parity with the Linux path: the generated AppleScript lowercases both
    # sides so "editor" matches "My Editor" (review MEDIUM).
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    captured: dict[str, str] = {}

    def fake_run(cmd, *a, **k):
        captured["script"] = cmd[2]
        return _cp(0, stdout="My Editor\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _find_and_focus_macos("EdItOr")
    script = captured["script"]
    assert "lowercase of" in script
    assert '"editor"' in script  # needle lowercased before interpolation


# --- Linux (wmctrl) --------------------------------------------------------


def test_linux_focus_success(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")

    def fake_run(cmd, *a, **k):
        if "-l" in cmd:
            listing = (
                "0x0123 0 host1 My Editor — file.py\n"
                "0x0456 0 host1 Terminal\n"
            )
            return _cp(0, stdout=listing)
        if "-a" in cmd:
            assert "0x0123" in cmd  # activates the matched id, not the other one
            return _cp(0)
        return _cp(1, stderr="unexpected")

    monkeypatch.setattr(subprocess, "run", fake_run)
    found, msg = _find_and_focus_linux("editor")
    assert found is True
    assert "My Editor" in msg


def test_linux_missing_wmctrl(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    found, msg = _find_and_focus_linux("Editor")
    assert found is False
    assert "wmctrl" in msg.lower()


def test_linux_no_match(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, *a, **k: _cp(0, stdout="0x0456 0 host1 Terminal\n")
    )
    found, msg = _find_and_focus_linux("Editor")
    assert found is False
    assert "Editor" in msg


# --- execute() dispatch + degrade ------------------------------------------


async def test_execute_routes_to_macos_with_english_readback(monkeypatch):
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "darwin")
    monkeypatch.setattr(
        "jarvis.plugins.tool.switch_window._find_and_focus_macos",
        lambda t: (True, "My Editor"),
    )
    res = await SwitchWindowTool().execute({"title_contains": "Editor"}, _ctx())
    assert res.success is True
    assert res.output == "Focused window: My Editor"


async def test_execute_wayland_degrades_in_english(monkeypatch):
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "linux")
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.is_wayland", lambda: True)
    res = await SwitchWindowTool().execute({"title_contains": "Editor"}, _ctx())
    assert res.success is False
    assert "Wayland" in res.error


async def test_execute_headless_linux_degrades(monkeypatch):
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "linux")
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.is_wayland", lambda: False)
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.display_present", lambda: False)
    res = await SwitchWindowTool().execute({"title_contains": "Editor"}, _ctx())
    assert res.success is False
    assert "display" in res.error.lower()


async def test_execute_windows_path_unchanged(monkeypatch):
    # AD-7: the Windows readback keeps its established wording.
    monkeypatch.setattr("jarvis.plugins.tool.switch_window.detect_platform", lambda: "win32")
    monkeypatch.setattr(
        "jarvis.plugins.tool.switch_window._find_and_focus_windows",
        lambda t: (True, "Notepad"),
    )
    res = await SwitchWindowTool().execute({"title_contains": "Notepad"}, _ctx())
    assert res.success is True
    assert res.output == "Focused window: Notepad"
