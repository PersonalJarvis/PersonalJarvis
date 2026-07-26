"""Unit tests for the cross-platform native clipboard writer."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import jarvis.platform.clipboard as clipboard


def _capabilities(display_present: bool) -> SimpleNamespace:
    return SimpleNamespace(display_present=display_present)


def test_headless_host_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clipboard, "detect_capabilities", lambda: _capabilities(False)
    )
    called = False

    def _unexpected(_text: str) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(clipboard, "_write_windows", _unexpected)
    assert clipboard.write_text("hello") is False
    assert called is False


def test_macos_uses_pbcopy_with_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clipboard, "detect_capabilities", lambda: _capabilities(True)
    )
    monkeypatch.setattr(clipboard, "detect_platform", lambda: "darwin")
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        clipboard,
        "_run_command",
        lambda command, text: (calls.append((list(command), text)), True)[1],
    )

    assert clipboard.write_text("line one\nline two") is True
    assert calls == [(["/usr/bin/pbcopy"], "line one\nline two")]


def test_windows_dispatches_to_native_unicode_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clipboard, "detect_capabilities", lambda: _capabilities(True)
    )
    monkeypatch.setattr(clipboard, "detect_platform", lambda: "win32")
    calls: list[str] = []
    monkeypatch.setattr(
        clipboard,
        "_write_windows",
        lambda text: (calls.append(text), True)[1],
    )

    assert clipboard.write_text("Unicode: café") is True
    assert calls == ["Unicode: café"]


def test_linux_prefers_wayland_clipboard_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: "/usr/bin/wl-copy" if name == "wl-copy" else None,
    )
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        clipboard,
        "_run_command",
        lambda command, text: (calls.append((list(command), text)), True)[1],
    )

    assert clipboard._write_linux("hello") is True
    assert calls == [
        (["/usr/bin/wl-copy", "--type", "text/plain;charset=utf-8"], "hello")
    ]


def test_command_writer_passes_text_only_through_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(clipboard.subprocess, "run", _run)
    clipboard_text = "sensitive multiline text"

    assert clipboard._run_command(["pbcopy"], clipboard_text) is True
    assert captured["command"] == ["pbcopy"]
    assert captured["input"] == clipboard_text
    assert clipboard_text not in captured["command"]


# --- read_text -------------------------------------------------------------
#
# The desktop WebView cannot use ``navigator.clipboard.readText`` (the embedded
# browser withholds that permission), so a right-click "Paste" reads through
# this helper instead. ``None`` means "this host cannot read a clipboard";
# an empty string means "the clipboard is genuinely empty" — the caller must be
# able to tell those apart.


def test_headless_host_cannot_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clipboard, "detect_capabilities", lambda: _capabilities(False)
    )
    called = False

    def _unexpected() -> str | None:
        nonlocal called
        called = True
        return "leaked"

    monkeypatch.setattr(clipboard, "_read_windows", _unexpected)
    assert clipboard.read_text() is None
    assert called is False


def test_macos_reads_through_pbpaste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clipboard, "detect_capabilities", lambda: _capabilities(True)
    )
    monkeypatch.setattr(clipboard, "detect_platform", lambda: "darwin")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        clipboard,
        "_read_command",
        lambda command: (calls.append(list(command)), "from pbpaste")[1],
    )

    assert clipboard.read_text() == "from pbpaste"
    assert calls == [["/usr/bin/pbpaste"]]


def test_windows_reads_through_the_native_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clipboard, "detect_capabilities", lambda: _capabilities(True)
    )
    monkeypatch.setattr(clipboard, "detect_platform", lambda: "win32")
    monkeypatch.setattr(clipboard, "_read_windows", lambda: "Unicode: café")

    assert clipboard.read_text() == "Unicode: café"


def test_linux_prefers_wayland_for_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: "/usr/bin/wl-paste" if name == "wl-paste" else None,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        clipboard,
        "_read_command",
        lambda command: (calls.append(list(command)), "wayland text")[1],
    )

    assert clipboard._read_linux() == "wayland text"
    assert calls == [["/usr/bin/wl-paste", "--no-newline"]]


def test_linux_without_any_clipboard_tool_reads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    assert clipboard._read_linux() is None


def test_read_command_failure_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="no display")

    monkeypatch.setattr(clipboard.subprocess, "run", _run)
    assert clipboard._read_command(["xclip", "-o"]) is None


def test_empty_clipboard_reads_as_empty_string_not_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(clipboard.subprocess, "run", _run)
    assert clipboard._read_command(["pbpaste"]) == ""
