"""Regression for the 2026-05-28 sub-agent mass-failure (Claude Max OAuth
contention). When several claude-direct workers/critics run at once the CLI
throttles and blocks BEFORE emitting a single byte; the old single
``communicate()`` cap then burned the full 630s and the mission FAILED with a
0-byte stream and a mislabeled ``reason=user`` kill.

``ClaudeDirectWorker`` now has a first-output ("startup") gate: if ``claude``
produces zero bytes within ``first_output_timeout_s`` it is killed and the
worker yields an ``is_error`` result whose text contains "timeout", so the
orchestrator labels the kill ``timeout`` and retries on a fresh, serialised
spawn. A task that HAS started streaming is never cut off by this gate.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.workers import claude_direct_worker as cdw
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.provider_chain import _FallbackStep


class _FakeStream:
    def __init__(self, *, hang: bool = False, data: bytes = b"") -> None:
        self._hang = hang
        self._data = data
        self._sent = False

    async def read(self, n: int = -1) -> bytes:
        if self._hang:
            await asyncio.sleep(30)  # longer than the gate; wait_for cancels it
            return b""
        if self._sent:
            return b""
        self._sent = True
        return self._data

    async def readline(self) -> bytes:
        # The worker now streams via readline(); a hang fake blocks past the
        # gate, otherwise it yields the (single-line) data once then EOF.
        if self._hang:
            await asyncio.sleep(30)  # longer than the gate; wait_for cancels it
            return b""
        if self._sent:
            return b""
        self._sent = True
        return self._data

    def write(self, _b: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProc:
    def __init__(self, *, hang: bool) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(hang=hang)
        self.stderr = _FakeStream()

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        self.returncode = -9
        return -9

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"", b"")


class _Job:
    def assign(self, _pid: int) -> None:
        pass


@pytest.mark.asyncio
async def test_first_output_gate_yields_timeout_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero output within the gate -> killed -> is_error result saying 'timeout'."""
    monkeypatch.setattr(
        cdw, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )

    async def _fake_exec(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(hang=True)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    worker = ClaudeDirectWorker()
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="t1",
        log_dir=tmp_path / "logs",
        first_output_timeout_s=0.2,
        timeout_s=5.0,
    ):
        events.append(ev)

    final = events[-1]
    assert getattr(final, "is_error", None) is True
    assert "timeout" in (final.result or "").lower()


@pytest.mark.asyncio
async def test_streaming_output_is_not_killed_by_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker that emits a terminal result promptly must NOT be flagged as a
    timeout — the gate only fires on a silent startup."""
    monkeypatch.setattr(
        cdw, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )
    result_line = (
        b'{"type":"result","subtype":"success","is_error":false,'
        b'"result":"OK","session_id":"s1"}\n'
    )

    class _StreamingProc(_FakeProc):
        def __init__(self) -> None:
            super().__init__(hang=False)
            self.stdout = _FakeStream(data=result_line)

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"")

    async def _fake_exec(*_a: Any, **_k: Any) -> _StreamingProc:
        p = _StreamingProc()
        p.returncode = 0
        return p

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    worker = ClaudeDirectWorker()
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="t2",
        log_dir=tmp_path / "logs",
        first_output_timeout_s=0.2,
        timeout_s=5.0,
    ):
        events.append(ev)

    final = events[-1]
    assert getattr(final, "is_error", None) is False
    assert final.result == "OK"


@pytest.mark.asyncio
async def test_mcp_config_with_secret_is_deleted_after_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security 2026-05-28: the per-worker MCP config inlines resolved plugin
    secrets (e.g. a GitHub PAT in the github server's env). It must be deleted
    the moment the subprocess exits — never left as a plaintext secret-at-rest
    in the mission logs (a real ghp_ token was found lingering across 6 dirs)."""
    monkeypatch.setattr(
        cdw, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )
    result_line = (
        b'{"type":"result","subtype":"success","is_error":false,'
        b'"result":"OK","session_id":"s1"}\n'
    )

    class _OkProc(_FakeProc):
        def __init__(self) -> None:
            super().__init__(hang=False)
            self.stdout = _FakeStream(data=result_line)

    async def _fake_exec(*_a: Any, **_k: Any) -> _OkProc:
        p = _OkProc()
        p.returncode = 0
        return p

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    log_dir = tmp_path / "logs"
    secret = "ghp_FAKEPLACEHOLDERTOKEN1234567890ABCD"  # noqa: S105 — test fixture
    worker = ClaudeDirectWorker(
        mcp_servers={
            "github": {
                "command": "docker",
                "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "img"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": secret},
            }
        }
    )
    async for _ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="t3",
        log_dir=log_dir,
        first_output_timeout_s=5.0,
        timeout_s=5.0,
    ):
        pass

    cfg = log_dir / ".jarvis-mcp.json"
    assert not cfg.exists(), "MCP config with inlined secret must be deleted after the run"
    # Belt-and-suspenders: the secret must not survive anywhere in the log dir.
    leaked = [
        p for p in log_dir.rglob("*")
        if p.is_file() and secret.encode() in p.read_bytes()
    ]
    assert leaked == [], f"secret leaked into: {leaked}"
