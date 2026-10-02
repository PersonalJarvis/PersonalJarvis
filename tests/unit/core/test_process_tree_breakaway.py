"""Windows process-tree breakaway policy for contained child trees."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.core.process_tree as process_tree


class _FakeFunction:
    def __init__(self, implementation: Callable[..., Any]) -> None:
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args: Any) -> Any:
        return self.implementation(*args)


@pytest.mark.parametrize(
    ("allow_breakaway", "expected_flags"),
    [
        (
            True,
            process_tree._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | process_tree._JOB_OBJECT_LIMIT_BREAKAWAY_OK,
        ),
        (False, process_tree._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE),
    ],
)
def test_windows_job_breakaway_policy(monkeypatch, allow_breakaway, expected_flags):
    configured_flags = []

    def set_information(_handle, _class, info_pointer, _size):
        info = ctypes.cast(
            info_pointer, ctypes.POINTER(process_tree._JobObjectExtendedLimitInformation)
        ).contents
        configured_flags.append(info.BasicLimitInformation.LimitFlags)
        return 1

    kernel32 = SimpleNamespace(
        CreateJobObjectW=_FakeFunction(lambda *_args: 1),
        SetInformationJobObject=_FakeFunction(set_information),
        OpenProcess=_FakeFunction(lambda *_args: 1),
        AssignProcessToJobObject=_FakeFunction(lambda *_args: 1),
        CloseHandle=_FakeFunction(lambda *_args: 1),
    )
    monkeypatch.setattr(process_tree, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        process_tree.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False
    )

    tree = process_tree.make_process_tree("browser", allow_breakaway=allow_breakaway)
    tree.close()

    assert configured_flags == [expected_flags]
