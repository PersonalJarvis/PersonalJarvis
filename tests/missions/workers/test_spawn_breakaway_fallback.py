"""Graceful CREATE_BREAKAWAY_FROM_JOB degradation on worker spawn.

Live mission 019ec602 (2026-06-14): every worker spawn died instantly with
``PermissionError: [WinError 5] Zugriff verweigert`` at
``_winapi.CreateProcess``. Root cause: the worker spawns with
``CREATE_BREAKAWAY_FROM_JOB`` so the per-mission Job Object can own the tree,
but when the app's pythonw.exe is itself inside a job that forbids breakaway,
``CreateProcess`` denies it with WinError 5. The native ``claude.exe`` install
(replacing the old ``node cli.js`` path on 2026-06-14) surfaced it.

Breakaway is only an optimization — without it the worker still runs, just
inside the parent's job. ``create_worker_subprocess`` retries without the
breakaway flag rather than failing the spawn (and the whole mission).
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.missions.workers import process_utils as pu

_BREAKAWAY = 0x01000000


@pytest.mark.asyncio
async def test_retries_without_breakaway_on_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_flags: list[int] = []

    async def _fake_exec(*_a, creationflags: int = 0, **_k):
        seen_flags.append(creationflags)
        if creationflags & _BREAKAWAY:
            raise PermissionError(5, "Zugriff verweigert")
        return object()  # a "proc"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "win32")
    monkeypatch.setattr(pu, "worker_creationflags", lambda: 0x08000000 | 0x200 | _BREAKAWAY)

    proc = await pu.create_worker_subprocess(["x"], cwd=".", env={})

    assert proc is not None
    assert len(seen_flags) == 2, "must attempt with breakaway, then without"
    assert seen_flags[0] & _BREAKAWAY
    assert not (seen_flags[1] & _BREAKAWAY), "retry must drop the breakaway flag"


@pytest.mark.asyncio
async def test_success_first_try_keeps_breakaway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_flags: list[int] = []

    async def _fake_exec(*_a, creationflags: int = 0, **_k):
        seen_flags.append(creationflags)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "win32")
    monkeypatch.setattr(pu, "worker_creationflags", lambda: 0x08000000 | _BREAKAWAY)

    await pu.create_worker_subprocess(["x"], cwd=".", env={})
    assert len(seen_flags) == 1, "no retry when the first spawn succeeds"
    assert seen_flags[0] & _BREAKAWAY


@pytest.mark.asyncio
async def test_file_not_found_propagates_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing binary is not a breakaway problem — propagate, do not retry."""
    calls = {"n": 0}

    async def _fake_exec(*_a, creationflags: int = 0, **_k):
        calls["n"] += 1
        raise FileNotFoundError(2, "not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "win32")
    monkeypatch.setattr(pu, "worker_creationflags", lambda: 0x08000000 | _BREAKAWAY)

    with pytest.raises(FileNotFoundError):
        await pu.create_worker_subprocess(["x"], cwd=".", env={})
    assert calls["n"] == 1, "FileNotFoundError must not trigger the breakaway retry"
