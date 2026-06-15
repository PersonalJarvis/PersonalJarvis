"""Live-streaming contract for ClaudeDirectWorker (parity with the Codex worker).

The Codex worker was refactored on 2026-06-10 (missions 019eb27f/019eb288) to
consume stdout line-by-line, tee every raw line to ``stream.jsonl`` immediately,
and yield translated events WHILE the subprocess is still running — so a
long-but-healthy worker shows visible progress instead of an opaque spinner and
the forensics file survives any exit.

``ClaudeDirectWorker`` was left on the OLD ``first-chunk read + communicate()``
path, which collects ALL stdout until process exit: during the whole run there
are NO incremental events and NO on-disk ``stream.jsonl``. These tests pin the
same live-streaming contract for the Claude path.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.workers import claude_direct_worker as cdw
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.provider_chain import _FallbackStep


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
        raise AssertionError("ClaudeDirectWorker must stream, not communicate()")

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


def _assistant_line(text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
                "session_id": "s1",
            }
        ).encode("utf-8")
        + b"\n"
    )


def _result_line(text: str = "OK") -> bytes:
    return (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": text,
                "session_id": "s1",
            }
        ).encode("utf-8")
        + b"\n"
    )


def _pin_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cdw,
        "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )


def _spawn(worker: ClaudeDirectWorker, tmp_path: Path, **kw: Any):
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
    """An assistant message must reach the orchestrator BEFORE the process ends."""
    _pin_claude(monkeypatch)
    stdout = _LiveStream()

    async def _fake_exec(*_a: Any, **_k: Any) -> _LiveProc:
        return _LiveProc(stdout)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    agen = _spawn(ClaudeDirectWorker(), tmp_path)
    try:
        init = await agen.__anext__()
        assert getattr(init, "type", "") == "system"

        stdout.queue.put_nowait(_assistant_line("first progress"))
        # With the old communicate() collector this hangs until EOF.
        ev = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
        assert getattr(ev, "type", "") == "assistant"
        assert "first progress" in json.dumps(ev.message)

        stdout.queue.put_nowait(_result_line())  # terminal result
        stdout.queue.put_nowait(b"")  # EOF
        tail = [e async for e in agen]
        assert tail and getattr(tail[-1], "type", "") == "result"
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_stream_jsonl_written_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every raw line lands in stream.jsonl immediately, not at process end."""
    _pin_claude(monkeypatch)
    stdout = _LiveStream()

    async def _fake_exec(*_a: Any, **_k: Any) -> _LiveProc:
        return _LiveProc(stdout)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    agen = _spawn(ClaudeDirectWorker(), tmp_path)
    try:
        await agen.__anext__()  # init
        stdout.queue.put_nowait(_assistant_line("hello disk"))
        await asyncio.wait_for(agen.__anext__(), timeout=2.0)

        stream_path = tmp_path / "logs" / "stream.jsonl"
        assert stream_path.exists(), "stream.jsonl must exist while running"
        assert "hello disk" in stream_path.read_text(encoding="utf-8")

        stdout.queue.put_nowait(_result_line())
        stdout.queue.put_nowait(b"")
        async for _ in agen:
            pass
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_partial_stream_survives_hard_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker killed by the wall-clock hard cap (no terminal result line)
    leaves its partial stream.jsonl behind and reports a structured timeout."""
    _pin_claude(monkeypatch)
    monkeypatch.setattr(cdw, "_HARDCAP_GRACE_S", 0.2)
    stdout = _LiveStream()

    async def _fake_exec(*_a: Any, **_k: Any) -> _LiveProc:
        return _LiveProc(stdout)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    # One assistant line of real progress, then the worker goes silent past the
    # hard cap (no result, no EOF) — the deadline fires.
    stdout.queue.put_nowait(_assistant_line("work before the cap"))
    events: list[Any] = []
    async for ev in _spawn(
        ClaudeDirectWorker(),
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
