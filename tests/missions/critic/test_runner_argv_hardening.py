"""Critic argv hardening: enforced read-only intent + deterministic effort.

Two live gaps closed 2026-07-25 (mission-reliability deep-dive items):

* The claude-direct critic ran under ``bypassPermissions`` with its
  read-only-ness enforced only by prompt intent. The write/shell tools are
  now disallowed outright (denylist — drift-safe), while Read/Glob/Grep stay
  available for the ground-truth verification the recovery parser narrates.
* The codex-direct critic passed ``--ignore-user-config`` but no reasoning
  effort, so it ran on the upstream default (or a user config's ``xhigh``) —
  a slow critic eats the correction-iteration time reserve. It now pins the
  SAME effort tier the worker uses (shared constant).
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
    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProc:
    def __init__(self, stdout: bytes, *, pid: int = 4242, returncode: int = 0) -> None:
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


@pytest.mark.asyncio
async def test_claude_critic_disallows_write_and_shell_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_argv_prefix",
        lambda: ["claude"],
    )
    monkeypatch.setattr(
        "jarvis.claude_auth.claude_cli_supports_safe_mode",
        lambda _prefix: False,
    )
    captured: dict[str, Any] = {}

    async def fake(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["argv"] = list(args)
        return _FakeProc(_valid_verdict_json().encode("utf-8"))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner()._invoke_via_claude_direct(
        prompt="grade this", worktree=tmp_path, env={},
        model="claude-sonnet-4-6", iteration=0, adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"
    argv = captured["argv"]
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert disallowed == "Write,Edit,MultiEdit,NotebookEdit,Bash"
    # Read/Glob/Grep must NOT be disallowed — the critic verifies ground
    # truth against the worktree via read tools.
    for read_tool in ("Read", "Glob", "Grep"):
        assert read_tool not in disallowed.split(",")
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


@pytest.mark.asyncio
async def test_codex_critic_pins_the_worker_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from jarvis.missions.workers.codex_direct_worker import _MISSION_REASONING_EFFORT

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
    captured: dict[str, Any] = {}

    async def fake(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["argv"] = list(args)
        return _FakeProc(ndjson.encode("utf-8"))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner()._invoke_via_codex_direct(
        prompt="grade this", worktree=tmp_path, env={},
        model="", iteration=0, adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"
    argv = captured["argv"]
    assert f"model_reasoning_effort={_MISSION_REASONING_EFFORT}" in argv
    # The containment flags stay exactly as before.
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in argv
    assert "approval_policy=never" in argv
