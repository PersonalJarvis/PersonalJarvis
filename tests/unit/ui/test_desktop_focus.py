"""Windows foreground recovery used by the desktop visibility endpoint."""

from __future__ import annotations

from jarvis.ui.desktop_app import _force_foreground_hwnd, _window_answers


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


class _ProbeUser32:
    def __init__(self, *, hung: bool = False, answers: bool = True, raises: bool = False):
        self.hung = hung
        self.answers = answers
        self.raises = raises
        self.sent: list[tuple[int, int, int]] = []

    def IsHungAppWindow(self, _hwnd: int) -> bool:
        if self.raises:
            raise OSError("probe unavailable")
        return self.hung

    def SendMessageTimeoutW(self, hwnd, msg, _wparam, _lparam, flags, timeout, _result) -> int:
        self.sent.append((hwnd, msg, timeout))
        return 1 if self.answers else 0


def test_a_window_windows_reports_as_hung_gets_no_message() -> None:
    user32 = _ProbeUser32(hung=True)

    assert _window_answers(200, user32) is False
    assert user32.sent == []


def test_a_window_that_misses_the_deadline_is_not_touched() -> None:
    """2026-09-30: a closing window held a second launch for 92 s inside the
    synchronous ShowWindow calls. The probe has a deadline; those calls do not."""
    user32 = _ProbeUser32(answers=False)

    assert _window_answers(200, user32, timeout_ms=250) is False
    assert user32.sent == [(200, 0, 250)]


def test_a_responsive_window_is_raised_as_before() -> None:
    assert _window_answers(200, _ProbeUser32()) is True


def test_a_probe_that_cannot_run_does_not_claim_a_hang() -> None:
    assert _window_answers(200, _ProbeUser32(raises=True)) is True


def test_foreground_lock_recovery_attaches_and_always_detaches() -> None:
    user32 = _User32()

    assert _force_foreground_hwnd(200, user32, _Kernel32()) is True
    assert user32.attachments == [
        (10, 20, True),
        (10, 30, True),
        (10, 20, False),
        (10, 30, False),
    ]
