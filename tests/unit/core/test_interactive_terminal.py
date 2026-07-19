"""Cross-platform tests for the visible interactive-terminal capability."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.core import interactive_terminal as terminal


def test_macos_opens_terminal_app_with_shell_quoted_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(terminal.sys, "platform", "darwin")
    monkeypatch.setattr(
        terminal.shutil,
        "which",
        lambda name: "/usr/bin/osascript" if name == "osascript" else None,
    )

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    launch = terminal.launch_interactive_terminal(
        ["/Users/Test User/.local/bin/claude", "auth", "login", "--claudeai"],
        title="Claude sign-in",
        cwd=Path("/Users/Test User"),
    )

    assert launch.method == "macos-terminal"
    assert launch.pid is None
    script = captured["argv"][2]  # type: ignore[index]
    assert 'tell application "Terminal"' in script
    assert "'/Users/Test User/.local/bin/claude' auth login --claudeai" in script
    assert "cd '/Users/Test User'" in script


def test_macos_launch_failure_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terminal.sys, "platform", "darwin")
    monkeypatch.setattr(terminal.shutil, "which", lambda _name: "/usr/bin/osascript")
    monkeypatch.setattr(
        terminal.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr="denied"),
    )

    with pytest.raises(terminal.InteractiveTerminalUnavailable, match="could not be opened"):
        terminal.launch_interactive_terminal(["claude"], title="Claude sign-in")


def test_windows_native_binary_gets_a_fresh_visible_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(terminal.sys, "platform", "win32")

    def fake_popen(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(terminal.subprocess, "Popen", fake_popen)
    launch = terminal.launch_interactive_terminal(
        [r"C:\Users\Test\.local\bin\claude.exe", "auth", "login"],
        title="Claude sign-in",
    )

    assert launch == terminal.InteractiveTerminalLaunch(42, "windows-console")
    assert captured["argv"] == [
        r"C:\Users\Test\.local\bin\claude.exe",
        "auth",
        "login",
    ]
    assert int(captured["kwargs"]["creationflags"]) & 0x00000010  # type: ignore[index]


def test_windows_cmd_shim_runs_through_comspec(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(terminal.sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    def fake_popen(argv, **_kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = argv
        return SimpleNamespace(pid=7)

    monkeypatch.setattr(terminal.subprocess, "Popen", fake_popen)
    terminal.launch_interactive_terminal(
        [r"C:\Users\Test\AppData\Roaming\npm\gemini.cmd"],
        title="Google sign-in",
    )

    argv = captured["argv"]
    assert argv[:3] == [r"C:\Windows\System32\cmd.exe", "/d", "/k"]  # type: ignore[index]
    assert "gemini.cmd" in argv[3]  # type: ignore[index]


def test_linux_uses_available_graphical_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(terminal.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        terminal.shutil,
        "which",
        lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None,
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=91)

    monkeypatch.setattr(terminal.subprocess, "Popen", fake_popen)
    launch = terminal.launch_interactive_terminal(
        ["/home/test/.local/bin/agy"],
        title="Google sign-in",
        cwd=Path("/home/test"),
    )

    assert launch == terminal.InteractiveTerminalLaunch(91, "gnome-terminal")
    assert captured["argv"] == [
        "/usr/bin/gnome-terminal",
        "--title",
        "Google sign-in",
        "--",
        "/home/test/.local/bin/agy",
    ]
    assert captured["kwargs"]["start_new_session"] is True  # type: ignore[index]


def test_headless_linux_refuses_an_invisible_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(terminal.InteractiveTerminalUnavailable, match="headless Linux"):
        terminal.launch_interactive_terminal(["agy"], title="Google sign-in")


def test_empty_command_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires an executable"):
        terminal.launch_interactive_terminal([], title="Login")
