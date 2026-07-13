"""Tests for WindowsJobObject — Win32-only with psutil verification.

Skip marker on non-Windows. On Windows we spawn a long-lived
Python subprocess, assign it to the job, close the handle, and
verify via psutil that the process is gone.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import pytest

from jarvis.missions.isolation import job_object as job_module
from jarvis.missions.isolation.job_object import (
    AlwaysOpenJobObject,
    WindowsJobObject,
)

_IS_WIN = sys.platform == "win32"

# CREATE_BREAKAWAY_FROM_JOB — the test runner itself might already be in a
# job (e.g. under VS Code / Windows Terminal), so the worker MUST be spawned
# with breakaway, otherwise AssignProcessToJobObject fails with
# ERROR_ACCESS_DENIED. The constant only ships in subprocess from Python 3.7
# onward — we take it from subprocess when present, otherwise the hex literal.
_CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


# --- No-op branch (all platforms) --------------------------------------------


def test_no_op_implementation_is_safe_to_use() -> None:
    """AlwaysOpenJobObject (no-op) has the same API and does nothing."""
    job = AlwaysOpenJobObject("test")
    assert not job.closed
    job.assign(12345)  # must not raise, even with a fake PID
    assert job.handle is None


async def test_no_op_async_context_manager_works() -> None:
    async with AlwaysOpenJobObject("ctx") as job:
        assert not job.closed
        job.assign(99999)
    assert job.closed


# --- Real Win32 tests ---------------------------------------------------------


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_factory_returns_real_impl_on_windows() -> None:
    """WindowsJobObject() returns the Win32 impl on Win32, not the no-op."""
    job = WindowsJobObject("factory-test")
    try:
        assert type(job).__name__ == "_Win32JobObjectImpl"
        assert job.handle is not None
    finally:
        await job.close()


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_close_kills_assigned_process() -> None:
    """Spawn → assign → close → process is gone (per psutil)."""
    psutil = pytest.importorskip("psutil")

    # Long-lived sleeper — runs 60s if not killed.
    proc = subprocess.Popen(  # noqa: ASYNC220, S603 — controlled args
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=(
            _CREATE_BREAKAWAY_FROM_JOB | _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        ),
    )
    try:
        # Wait until the subprocess actually exists
        await asyncio.sleep(0.1)
        assert psutil.pid_exists(proc.pid), "Subprocess should have started"

        job = WindowsJobObject("kill-on-close-test")
        job.assign(proc.pid)
        # Closing should atomically kill the process
        await job.close()

        # Wait up to 2s for the OS to reap — usually <100ms
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.05)

        assert proc.poll() is not None, (
            "Process should have been killed by job close"
        )
    finally:
        # Safety net in case the test logic failed
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_assign_after_close_raises() -> None:
    """assign() after close() raises RuntimeError instead of swallowing it silently."""
    job = WindowsJobObject("closed-test")
    await job.close()
    with pytest.raises(RuntimeError, match="already closed"):
        job.assign(1234)


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_close_is_idempotent() -> None:
    job = WindowsJobObject("idempotent-test")
    await job.close()
    await job.close()  # must not raise
    assert job.closed


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_async_context_manager_closes_on_exit() -> None:
    pytest.importorskip("psutil")
    proc = subprocess.Popen(  # noqa: ASYNC220, S603
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=(
            _CREATE_BREAKAWAY_FROM_JOB | _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        ),
    )
    try:
        await asyncio.sleep(0.1)
        async with WindowsJobObject("ctx-mgr-test") as job:
            job.assign(proc.pid)
            assert not job.closed

        # After the with block: process must be dead
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.05)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_ctypes_fallback_kills_assigned_process_without_pywin32() -> None:
    """Base Windows installs retain kernel-enforced tree containment."""
    proc = subprocess.Popen(  # noqa: ASYNC220, S603 - controlled test argv
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=(
            _CREATE_BREAKAWAY_FROM_JOB | _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        ),
    )
    try:
        job = job_module._Win32CtypesJobObjectImpl(
            "ctypes-fallback-test", allow_breakaway=False
        )
        job.assign(proc.pid)
        await job.close()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and proc.poll() is None:  # noqa: ASYNC110
            await asyncio.sleep(0.05)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_strict_job_disables_descendant_breakaway() -> None:
    win32job = pytest.importorskip("win32job")

    job = WindowsJobObject("strict-flags-test", allow_breakaway=False)
    try:
        info = win32job.QueryInformationJobObject(
            job.handle, win32job.JobObjectExtendedLimitInformation
        )
        flags = info["BasicLimitInformation"]["LimitFlags"]
        assert flags & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert not flags & win32job.JOB_OBJECT_LIMIT_BREAKAWAY_OK
    finally:
        await job.close()


@pytest.mark.skipif(not _IS_WIN, reason="Job objects are Windows-only")
async def test_factory_uses_ctypes_when_pywin32_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing_pywin32(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise ImportError("simulated missing pywin32")

    monkeypatch.setattr(job_module, "_Win32JobObjectImpl", _missing_pywin32)
    job = WindowsJobObject("ctypes-factory-test", allow_breakaway=False)
    try:
        assert type(job).__name__ == "_Win32CtypesJobObjectImpl"
        assert job.handle is not None
    finally:
        await job.close()
