"""Tests fuer WindowsJobObject — Win32-only mit psutil-Verifikation.

Skip-Marker auf Nicht-Windows. Auf Windows spawnen wir einen langlebigen
Python-Subprocess, assignen ihn dem Job, schliessen das Handle und
verifizieren via psutil dass der Prozess weg ist.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import pytest

from jarvis.missions.isolation.job_object import (
    AlwaysOpenJobObject,
    WindowsJobObject,
)

_IS_WIN = sys.platform == "win32"

# CREATE_BREAKAWAY_FROM_JOB — der Test-Runner selbst koennte schon in einem
# Job sein (z.B. unter VS Code / Windows Terminal), daher MUSS der Worker mit
# Breakaway gespawnt werden, sonst schlaegt AssignProcessToJobObject fehl mit
# ERROR_ACCESS_DENIED. Die Konstante kommt erst ab Python 3.7 in subprocess
# vor — wir nehmen sie aus subprocess wenn vorhanden, sonst Hex-Literal.
_CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


# --- No-Op-Branch (alle Plattformen) -----------------------------------------


def test_no_op_implementation_is_safe_to_use() -> None:
    """AlwaysOpenJobObject (No-Op) hat dieselbe API und tut nichts."""
    job = AlwaysOpenJobObject("test")
    assert not job.closed
    job.assign(12345)  # darf nicht raisen, auch mit fake-PID
    assert job.handle is None


async def test_no_op_async_context_manager_works() -> None:
    async with AlwaysOpenJobObject("ctx") as job:
        assert not job.closed
        job.assign(99999)
    assert job.closed


# --- Echte Win32-Tests --------------------------------------------------------


@pytest.mark.skipif(not _IS_WIN, reason="Job Objects sind Windows-only")
async def test_factory_returns_real_impl_on_windows() -> None:
    """WindowsJobObject() liefert auf Win32 den Win32-Impl, nicht den No-Op."""
    job = WindowsJobObject("factory-test")
    try:
        assert type(job).__name__ == "_Win32JobObjectImpl"
        assert job.handle is not None
    finally:
        await job.close()


@pytest.mark.skipif(not _IS_WIN, reason="Job Objects sind Windows-only")
async def test_close_kills_assigned_process() -> None:
    """Spawn → assign → close → process ist weg (per psutil)."""
    psutil = pytest.importorskip("psutil")

    # Langlebiger Sleeper — laeuft 60s wenn nicht gekillt.
    proc = subprocess.Popen(  # noqa: S603 — kontrollierte args
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=(
            _CREATE_BREAKAWAY_FROM_JOB | _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        ),
    )
    try:
        # Warten bis der Subprocess wirklich existiert
        await asyncio.sleep(0.1)
        assert psutil.pid_exists(proc.pid), "Subprocess sollte gestartet sein"

        job = WindowsJobObject("kill-on-close-test")
        job.assign(proc.pid)
        # Schliessen sollte den Prozess atomar killen
        await job.close()

        # Bis zu 2s warten bis OS reaped — normalerweise <100ms
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.05)

        assert proc.poll() is not None, (
            "Process haette von Job-Close gekillt werden muessen"
        )
    finally:
        # Sicherheitsnetz falls Test-Logik fehlschlug
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.skipif(not _IS_WIN, reason="Job Objects sind Windows-only")
async def test_assign_after_close_raises() -> None:
    """assign() nach close() wirft RuntimeError statt silent zu schlucken."""
    job = WindowsJobObject("closed-test")
    await job.close()
    with pytest.raises(RuntimeError, match="schon geschlossen"):
        job.assign(1234)


@pytest.mark.skipif(not _IS_WIN, reason="Job Objects sind Windows-only")
async def test_close_is_idempotent() -> None:
    job = WindowsJobObject("idempotent-test")
    await job.close()
    await job.close()  # darf nicht raisen
    assert job.closed


@pytest.mark.skipif(not _IS_WIN, reason="Job Objects sind Windows-only")
async def test_async_context_manager_closes_on_exit() -> None:
    psutil = pytest.importorskip("psutil")
    proc = subprocess.Popen(  # noqa: S603
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

        # Nach dem with-Block: Prozess muss tot sein
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
