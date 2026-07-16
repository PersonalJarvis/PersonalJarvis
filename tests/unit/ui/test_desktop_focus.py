"""Windows foreground recovery used by the desktop visibility endpoint."""

from __future__ import annotations

from jarvis.ui.desktop_app import _force_foreground_hwnd


class _Kernel32:
    @staticmethod
    def GetCurrentThreadId() -> int:
        return 10


class _User32:
    def __init__(self) -> None:
        self.foreground = 100
        self.set_calls = 0
        self.attachments: list[tuple[int, int, bool]] = []

    def ShowWindow(self, _hwnd: int, _mode: int) -> bool:
        return True

    def SetForegroundWindow(self, hwnd: int) -> bool:
        self.set_calls += 1
        if self.set_calls >= 2:
            self.foreground = hwnd
            return True
        return False

    def SetActiveWindow(self, _hwnd: int) -> int:
        return 1

    def GetForegroundWindow(self) -> int:
        return self.foreground

    @staticmethod
    def GetWindowThreadProcessId(hwnd: int, _pid: object) -> int:
        return {100: 20, 200: 30}.get(hwnd, 0)

    def AttachThreadInput(self, source: int, target: int, attach: bool) -> bool:
        self.attachments.append((source, target, attach))
        return True

    @staticmethod
    def BringWindowToTop(_hwnd: int) -> bool:
        return True

    @staticmethod
    def SetWindowPos(*_args: object) -> bool:
        return True


def test_foreground_lock_recovery_attaches_and_always_detaches() -> None:
    user32 = _User32()

    assert _force_foreground_hwnd(200, user32, _Kernel32()) is True
    assert user32.attachments == [
        (10, 20, True),
        (10, 30, True),
        (10, 20, False),
        (10, 30, False),
    ]
