"""Accidental-console suppression for the desktop window.

When the desktop app is launched by the console-subsystem ``python.exe`` (a
scheduled task / shortcut / double-click that resolved to ``python.exe`` instead
of the windowless ``pythonw.exe``), Windows hands it a black terminal that fills
with loguru's stderr output and confuses users (forensic 2026-07-08: a test
laptop's autostart launched ``python.exe``). ``run_window_only`` hides that
console — but ONLY when the app exclusively owns it, so a developer's own
terminal and ``run.bat --debug`` (where ``cmd.exe`` stays attached) are left
visible.

These tests pin the pure decision (the sole-owner rule) and the orchestration
(probe → decide → hide) without needing a real Windows console — the win32
probes are injectable.
"""
from __future__ import annotations

from jarvis.ui import desktop_app
from jarvis.ui.desktop_app import _console_owned_exclusively, hide_accidental_console


# --- the pure sole-owner decision (cross-platform) -------------------------


def test_sole_owner_console_is_hidden() -> None:
    # A real console window (non-zero HWND) attached to exactly one process —
    # us — was allocated for this app alone → hide it.
    assert _console_owned_exclusively(console_hwnd=4242, attached_process_count=1) is True


def test_shared_console_is_left_alone() -> None:
    # A shell (cmd.exe / powershell) or run.bat --debug shares the console →
    # it belongs to the user → never hide it.
    assert _console_owned_exclusively(console_hwnd=4242, attached_process_count=2) is False


def test_no_console_window_is_a_noop() -> None:
    # pythonw.exe / genuinely windowless: GetConsoleWindow() == 0 → nothing to do.
    assert _console_owned_exclusively(console_hwnd=0, attached_process_count=1) is False


# --- the orchestration, with injected win32 probes -------------------------


def _force_win32(monkeypatch) -> None:
    """Make ``hide_accidental_console`` take the Windows branch off-Windows.

    Only the injected probes are exercised — no ``ctypes.windll`` is ever
    touched, so this is safe on the Linux CI runner too.
    """
    monkeypatch.setattr(desktop_app.sys, "platform", "win32")


def test_hides_console_when_sole_owner(monkeypatch) -> None:
    _force_win32(monkeypatch)
    hidden: list[int] = []

    result = hide_accidental_console(
        _get_console_window=lambda: 777,
        _count_attached_processes=lambda: 1,
        _hide_window=hidden.append,
    )

    assert result is True
    assert hidden == [777]  # the exact HWND was hidden


def test_does_not_hide_a_shared_console(monkeypatch) -> None:
    _force_win32(monkeypatch)
    hidden: list[int] = []

    result = hide_accidental_console(
        _get_console_window=lambda: 777,
        _count_attached_processes=lambda: 3,  # a shell shares it
        _hide_window=hidden.append,
    )

    assert result is False
    assert hidden == []


def test_does_not_probe_process_count_without_a_console(monkeypatch) -> None:
    _force_win32(monkeypatch)
    hidden: list[int] = []
    counted: list[bool] = []

    def _count() -> int:
        counted.append(True)
        return 1

    result = hide_accidental_console(
        _get_console_window=lambda: 0,  # no console at all
        _count_attached_processes=_count,
        _hide_window=hidden.append,
    )

    assert result is False
    assert hidden == []
    assert counted == []  # short-circuits before probing the process list


def test_noop_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(desktop_app.sys, "platform", "linux")
    hidden: list[int] = []

    result = hide_accidental_console(
        _get_console_window=lambda: 777,
        _count_attached_processes=lambda: 1,
        _hide_window=hidden.append,
    )

    assert result is False
    assert hidden == []  # never touches the window on macOS/Linux


def test_probe_failure_never_raises(monkeypatch) -> None:
    _force_win32(monkeypatch)

    def _boom() -> int:
        raise OSError("console probe blew up")

    # A failing probe must degrade to "did nothing", never crash the window boot.
    assert (
        hide_accidental_console(
            _get_console_window=_boom,
            _count_attached_processes=lambda: 1,
            _hide_window=lambda _hwnd: None,
        )
        is False
    )
