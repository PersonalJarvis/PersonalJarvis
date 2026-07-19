"""Live-streaming contract for CodexDirectWorker (2026-06-10 root cause).

Live incident chain (missions 019eb27f + 019eb288, jarvis_desktop.log
19:24:12): the worker collected ALL stdout via ``communicate()`` until
process exit, so during the whole run there was NO stream.jsonl on disk,
NO translated events upstream, and NO visible progress. A gpt-5.5 xhigh
worker legitimately "thinks" for many minutes between NDJSON lines; that
silence was indistinguishable from a hang, the user pressed the app's
Restart button mid-run, the orphaned missions surfaced 30 minutes later
as opaque crash_recovery/ERROR cards with zero artifacts.

The contract pinned here: codex stdout is consumed line-by-line, every
raw line is tee'd to ``stream.jsonl`` IMMEDIATELY, and translated events
are yielded WHILE the subprocess is still running — so progress is
observable and the forensics file exists no matter how the run ends.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.workers import codex_direct_worker as cdw
from jarvis.missions.workers.codex_direct_worker import CodexDirectWorker


class _LiveStream:
    """stdout fake driven by a queue: lines appear over time, b'' = EOF."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.queue.get()

    async def read(self, n: int = -1) -> bytes:
        # Only used if the worker regresses to bulk reads — then it blocks
        # until EOF is queued, which the live tests will catch via wait_for.
        chunks: list[bytes] = []
        while True:
            chunk = await self.queue.get()
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


class _SilentStream:
    """stderr/stdin fake: empty EOF reads, no-op writes."""

    async def read(self, n: int = -1) -> bytes:
        return b""

    async def readline(self) -> bytes:
        return b""

    def write(self, _b: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _LiveProc:
    def __init__(self, stdout: _LiveStream) -> None:
        self.pid = 4711
        self.returncode: int | None = None
        self.stdin = _SilentStream()
        self.stdout = stdout
        self.stderr = _SilentStream()

    async def communicate(self) -> tuple[bytes, bytes]:
        # A regression back to communicate() must not silently pass.
        raise AssertionError("CodexDirectWorker must stream, not communicate()")

    async def wait(self) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9
        # Unblock any pending readline so the worker's read loop sees EOF.
        self.stdout.queue.put_nowait(b"")


class _Job:
    def assign(self, _pid: int) -> None:
        pass


def _agent_line(text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": text},
            }
        ).encode("utf-8")
        + b"\n"
    )


def _spawn(worker: CodexDirectWorker, tmp_path: Path, **kw: Any):
    return worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="live",
        log_dir=tmp_path / "logs",
        **kw,
    )


@pytest.mark.asyncio
async def test_events_yielded_while_process_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent_message must reach the orchestrator BEFORE the process ends."""
    stdout = _LiveStream()

    async def _fake_exec(*_a: Any, **_k: Any) -> _LiveProc:
        return _LiveProc(stdout)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    agen = _spawn(CodexDirectWorker(), tmp_path)
    try:
        init = await agen.__anext__()
        assert getattr(init, "type", "") == "system"

        stdout.queue.put_nowait(_agent_line("first progress"))
        # With the old communicate() collector this hangs until EOF.
        ev = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
        assert getattr(ev, "type", "") == "assistant"
        assert "first progress" in json.dumps(ev.message)

        stdout.queue.put_nowait(b"")  # EOF -> terminal result
        tail = [e async for e in agen]
        assert tail and getattr(tail[-1], "type", "") == "result"
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_stream_jsonl_written_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every raw line lands in stream.jsonl immediately, not at process end."""
    stdout = _LiveStream()

    async def _fake_exec(*_a: Any, **_k: Any) -> _LiveProc:
        return _LiveProc(stdout)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    agen = _spawn(CodexDirectWorker(), tmp_path)
    try:
        await agen.__anext__()  # init
        stdout.queue.put_nowait(_agent_line("hello disk"))
        await asyncio.wait_for(agen.__anext__(), timeout=2.0)

        stream_path = tmp_path / "logs" / "stream.jsonl"
        assert stream_path.exists(), "stream.jsonl must exist while running"
        assert "hello disk" in stream_path.read_text(encoding="utf-8")

        stdout.queue.put_nowait(b"")
        async for _ in agen:
            pass
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_stream_jsonl_is_isolated_per_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new worker attempt must replace evidence from the prior iteration."""
    stdout = _LiveStream()

    async def _fake_exec(*_a: Any, **_k: Any) -> _LiveProc:
        return _LiveProc(stdout)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    stream_path = tmp_path / "logs" / "stream.jsonl"
    stream_path.parent.mkdir(parents=True)
    stream_path.write_text('{"stale":"prior spawn"}\n', encoding="utf-8")

    agen = _spawn(CodexDirectWorker(), tmp_path)
    try:
        await agen.__anext__()  # init
        stdout.queue.put_nowait(_agent_line("current spawn"))
        await asyncio.wait_for(agen.__anext__(), timeout=2.0)

        stream_text = stream_path.read_text(encoding="utf-8")
        assert "prior spawn" not in stream_text
        assert "current spawn" in stream_text

        stdout.queue.put_nowait(b"")
        async for _ in agen:
            pass
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_spawn_failure_does_not_leave_prior_stream_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing Codex binary must not expose the preceding attempt's log."""

    async def _missing_exec(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("codex")

    monkeypatch.setattr(cdw, "create_worker_subprocess", _missing_exec)
    stream_path = tmp_path / "logs" / "stream.jsonl"
    stream_path.parent.mkdir(parents=True)
    stream_path.write_text('{"stale":"prior spawn"}\n', encoding="utf-8")

    events = [event async for event in _spawn(CodexDirectWorker(), tmp_path)]

    assert getattr(events[-1], "is_error", False) is True
    assert stream_path.read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_partial_stream_survives_startup_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker killed by the hard cap leaves its partial stream.jsonl behind."""
    stdout = _LiveStream()

    async def _fake_exec(*_a: Any, **_k: Any) -> _LiveProc:
        return _LiveProc(stdout)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(cdw, "_HARDCAP_GRACE_S", 0.2)

    stdout.queue.put_nowait(_agent_line("work before the cap"))
    events: list[Any] = []
    async for ev in _spawn(
        CodexDirectWorker(),
        tmp_path,
        timeout_s=0.5,
        first_output_timeout_s=0.5,
    ):
        events.append(ev)

    final = events[-1]
    assert final.timed_out is True
    assert final.is_error is True
    stream_path = tmp_path / "logs" / "stream.jsonl"
    assert stream_path.exists()
    assert "work before the cap" in stream_path.read_text(encoding="utf-8")
