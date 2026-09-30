"""``wait_procs`` survives psutil's pidfd EINVAL race (Linux installer smoke 2026-09-30)."""

from __future__ import annotations

import errno

import psutil
import pytest

from jarvis.core import process_utils


class FakeProc:
    """Stands in for ``psutil.Process``: gone after ``live_polls`` checks."""

    def __init__(self, live_polls: int, *, zombie: bool = False) -> None:
        self.live_polls = live_polls
        self.zombie = zombie

    def is_running(self) -> bool:
        if self.live_polls <= 0:
            return False
        self.live_polls -= 1
        return True

    def status(self) -> str:
        return psutil.STATUS_ZOMBIE if self.zombie else psutil.STATUS_RUNNING


class VanishedProc:
    def is_running(self) -> bool:
        raise psutil.NoSuchProcess(4242)

    def status(self) -> str:  # pragma: no cover - never reached
        return psutil.STATUS_RUNNING


def _einval(*_args: object, **_kwargs: object) -> object:
    raise OSError(errno.EINVAL, "Invalid argument")


def test_the_einval_race_falls_back_to_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "wait_procs", _einval)
    quick, zombie, vanished = FakeProc(2), FakeProc(5, zombie=True), VanishedProc()
    stuck = FakeProc(10_000)

    gone, alive = process_utils.wait_procs([quick, zombie, vanished, stuck], timeout=0.3)

    assert alive == [stuck]
    assert gone == [quick, zombie, vanished]


def test_other_os_errors_still_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def _eperm(*_args: object, **_kwargs: object) -> object:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(psutil, "wait_procs", _eperm)
    with pytest.raises(OSError):
        process_utils.wait_procs([FakeProc(1)], timeout=0.1)


def test_the_normal_path_is_psutil_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    def _psutil(procs: list[object], timeout: float) -> tuple[list[object], list[object]]:
        seen.append(timeout)
        return list(procs), []

    monkeypatch.setattr(psutil, "wait_procs", _psutil)
    proc = FakeProc(1)
    assert process_utils.wait_procs([proc], timeout=3) == ([proc], [])
    assert seen == [3]
