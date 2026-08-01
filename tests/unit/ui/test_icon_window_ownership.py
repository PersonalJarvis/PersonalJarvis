"""Regression guards for process-safe desktop window branding."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from jarvis.ui import icon_utils


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only window identity")
def test_title_icon_setter_rejects_a_foreign_same_title_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A repository terminal may have the exact desktop-app title."""

    import ctypes
    from ctypes import wintypes

    ico = tmp_path / "app.ico"
    ico.write_bytes(b"synthetic icon")
    foreign_pid = os.getpid() + 1

    class _User32:
        def __init__(self) -> None:
            self.FindWindowW = _Callable(lambda *_args: 12345)
            self.GetWindowThreadProcessId = _Callable(self._write_foreign_pid)

        @staticmethod
        def _write_foreign_pid(_hwnd, pid_pointer) -> int:  # noqa: ANN001
            ctypes.cast(
                pid_pointer, ctypes.POINTER(wintypes.DWORD)
            ).contents.value = foreign_pid
            return 1

    applied: list[int] = []
    monkeypatch.setattr(ctypes.windll, "user32", _User32())
    monkeypatch.setattr(
        icon_utils,
        "_apply_icon_to_hwnd",
        lambda hwnd, _path: applied.append(hwnd) or True,
    )

    assert not icon_utils.set_window_icon_by_title(
        "Personal Jarvis", ico, expected_pid=os.getpid()
    )
    assert applied == []


class _Callable:
    """Callable fake that accepts ctypes ``argtypes``/``restype`` attributes."""

    def __init__(self, callback) -> None:  # noqa: ANN001
        self._callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):  # noqa: ANN002, ANN204
        return self._callback(*args)
