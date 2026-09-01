"""``ensure_normal_process_priority`` — the BUG-204-amplifier repair.

The old autostart Scheduled Task registered without ``-Priority`` and Task
Scheduler's default (7) started the whole tree BelowNormal. The repair must
only ever RAISE BelowNormal/Idle to Normal, never demote, and must be a quiet
no-op off Windows (macOS/Linux login launches already start at normal
priority).
"""

from __future__ import annotations

import sys

import pytest

from jarvis.core import process_utils


class _FakeKernel32:
    """Capability fake for the two Win32 calls the repair makes."""

    def __init__(self, current_class: int, set_ok: bool = True) -> None:
        self.current_class = current_class
        self.set_ok = set_ok
        self.set_calls: list[int] = []

    def GetCurrentProcess(self) -> int:  # noqa: N802 — Win32 spelling
        return -1

    def GetPriorityClass(self, _handle: int) -> int:  # noqa: N802
        return self.current_class

    def SetPriorityClass(self, _handle: int, new_class: int) -> int:  # noqa: N802
        self.set_calls.append(new_class)
        return 1 if self.set_ok else 0


class _FakeCtypes:
    def __init__(self, kernel32: _FakeKernel32) -> None:
        self.windll = type("windll", (), {"kernel32": kernel32})()


def _run_with(monkeypatch: pytest.MonkeyPatch, kernel32: _FakeKernel32) -> str | None:
    monkeypatch.setattr(process_utils.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", _FakeCtypes(kernel32))
    return process_utils.ensure_normal_process_priority()


def test_off_windows_is_a_quiet_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_utils.sys, "platform", "linux")
    assert process_utils.ensure_normal_process_priority() is None


def test_raises_below_normal_to_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = _FakeKernel32(current_class=0x00004000)  # BELOW_NORMAL
    detail = _run_with(monkeypatch, kernel32)
    assert detail == "BelowNormal -> Normal"
    assert kernel32.set_calls == [0x00000020]  # NORMAL


def test_raises_idle_to_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = _FakeKernel32(current_class=0x00000040)  # IDLE
    detail = _run_with(monkeypatch, kernel32)
    assert detail == "Idle -> Normal"
    assert kernel32.set_calls == [0x00000020]


def test_never_touches_a_normal_or_higher_class(monkeypatch: pytest.MonkeyPatch) -> None:
    for cls in (0x00000020, 0x00008000, 0x00000080):  # NORMAL, ABOVE_NORMAL, HIGH
        kernel32 = _FakeKernel32(current_class=cls)
        assert _run_with(monkeypatch, kernel32) is None
        assert kernel32.set_calls == []


def test_failed_set_is_reported_as_nothing_done(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = _FakeKernel32(current_class=0x00004000, set_ok=False)
    assert _run_with(monkeypatch, kernel32) is None
