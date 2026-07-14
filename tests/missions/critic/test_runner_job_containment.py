"""AP-10: critic subprocesses (claude-direct / codex-direct) must be placed
in the per-mission containment job, exactly like the heavy worker.

Evidence: both `_invoke_via_claude_direct` and `_invoke_via_codex_direct`
called `create_worker_subprocess()` WITHOUT a `job` — the subprocess actively
escapes any ambient job (`CREATE_BREAKAWAY_FROM_JOB`) and never gets one of
its own, so a crash/cancel of the orchestrator does not reap it. The worker
path (`ClaudeDirectWorker.spawn`) does this correctly via
`job.assign(proc.pid)` inside an `async with job:` block the orchestrator
opens. `CriticRunner` now accepts an optional `job_factory` (bootstrap wires
in the SAME factory the orchestrator uses) and mirrors that pattern; a
missing factory (the default) keeps the pre-fix graceful no-op behaviour.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.critic.runner import CriticRunner
from jarvis.missions.critic.verdict import REQUIRED_AXES


def _valid_verdict_json(verdict: str = "approve") -> str:
    return json.dumps({
        "verdict": verdict,
        "axes": {
            ax: {"status": "pass", "evidence": ["src/x.py:1"]}
            for ax in REQUIRED_AXES
        },
        "issues": [],
        "correction_instruction": "",
        "summary": "ok",
        "summary_de": "ok",  # i18n-allow (German value under summary_de field)
        "confidence": 0.9,
        "suggested_next_action": "accept",
    })


class _FakeStdin:
    def __init__(self) -> None:
        self.written: bytes = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProc:
    def __init__(self, stdout: bytes, *, pid: int, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode
        self.stdin = _FakeStdin()
        self.pid = pid

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


class _FakeJob:
    """Records assign() calls + __aenter__/__aexit__ lifecycle."""

    def __init__(self) -> None:
        self.assigned_pids: list[int] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeJob:
        self.entered = True
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.exited = True

    def assign(self, pid: int) -> None:
        self.assigned_pids.append(pid)


@pytest.mark.asyncio
async def test_claude_direct_assigns_spawned_pid_to_the_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    async def fake(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(_valid_verdict_json().encode("utf-8"), pid=9001)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    fake_job = _FakeJob()
    runner = CriticRunner(job_factory=lambda: fake_job)

    verdict = await runner._invoke_via_claude_direct(
        prompt="grade this", worktree=tmp_path, env={},
        model="claude-sonnet-4-6", iteration=0, adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"
    assert fake_job.assigned_pids == [9001]
    assert fake_job.entered is True
    assert fake_job.exited is True


@pytest.mark.asyncio
async def test_claude_direct_without_factory_stays_a_graceful_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Backward compat: CriticRunner() with no job_factory (existing test
    callers, e.g. tests/missions/critic/test_runner_claude_direct.py) must
    keep working unchanged."""
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    async def fake(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(_valid_verdict_json().encode("utf-8"), pid=9002)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner()._invoke_via_claude_direct(
        prompt="grade this", worktree=tmp_path, env={},
        model="claude-sonnet-4-6", iteration=0, adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"


@pytest.mark.asyncio
async def test_codex_direct_assigns_spawned_pid_to_the_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._resolve_codex_binary",
        lambda: "codex",
    )
    ndjson = (
        json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": _valid_verdict_json()},
        })
        + "\n"
        + json.dumps({"type": "turn.completed"})
        + "\n"
    )

    async def fake(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(ndjson.encode("utf-8"), pid=9101)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    fake_job = _FakeJob()
    runner = CriticRunner(job_factory=lambda: fake_job)

    verdict = await runner._invoke_via_codex_direct(
        prompt="grade this", worktree=tmp_path, env={},
        model="", iteration=0, adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"
    assert fake_job.assigned_pids == [9101]
    assert fake_job.entered is True
    assert fake_job.exited is True


@pytest.mark.asyncio
async def test_codex_direct_without_factory_stays_a_graceful_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._resolve_codex_binary",
        lambda: "codex",
    )
    ndjson = (
        json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": _valid_verdict_json()},
        })
        + "\n"
        + json.dumps({"type": "turn.completed"})
        + "\n"
    )

    async def fake(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(ndjson.encode("utf-8"), pid=9102)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner()._invoke_via_codex_direct(
        prompt="grade this", worktree=tmp_path, env={},
        model="", iteration=0, adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"


@pytest.mark.asyncio
async def test_claude_direct_timeout_still_closes_the_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The job must be closed (reaping any grandchild) even on the timeout
    path, which re-raises CriticTimeout."""
    from jarvis.missions.critic.runner import CriticTimeout

    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    class _HangingProc(_FakeProc):
        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(3600)
            return b"", b""  # pragma: no cover

    async def fake(*_a: Any, **_k: Any) -> _HangingProc:
        return _HangingProc(b"", pid=9003)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    fake_job = _FakeJob()
    runner = CriticRunner(timeout_seconds=0.05, job_factory=lambda: fake_job)

    with pytest.raises(CriticTimeout):
        await runner._invoke_via_claude_direct(
            prompt="grade this", worktree=tmp_path, env={},
            model="claude-sonnet-4-6", iteration=0, adversarial_reframe=False,
        )

    assert fake_job.assigned_pids == [9003]
    assert fake_job.exited is True
