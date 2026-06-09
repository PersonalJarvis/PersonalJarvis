"""Regression for the 2026-06-08 'all sub-missions fail on codex' incident.

Forensic ground truth (`data/missions.db` mission 019ea8db + jarvis_desktop.log):
the user's codex ChatGPT OAuth token expired ("Failed to refresh token. ...
Please log in again."), but `codex status` still reported connected=True. Two
stacked defects followed:

1. CodexDirectWorker CRASHED: the codex error event nests its message as a dict,
   and `ClaudeResult(result=<dict>)` (a str field) raised a Pydantic
   ValidationError mid-spawn → opaque `task_error`, hiding the real cause.
2. Even surfaced honestly, a dead codex token means every codex mission fails.

The fix: (a) coerce the codex error to a plain string (no crash, honest message);
(b) when the error means the ChatGPT login is dead AND codex did no real work,
fall back to the Claude Max OAuth worker so the mission still COMPLETES.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.workers import claude_direct_worker as cdw_claude
from jarvis.missions.workers import codex_direct_worker as cdw
from jarvis.missions.workers.codex_direct_worker import (
    CodexDirectWorker,
    _codex_error_is_auth_expired,
    _coerce_codex_error_text,
)
from jarvis.missions.workers.provider_chain import _FallbackStep


# --- pure-unit: the crash-proofing helpers --------------------------------


def test_coerce_codex_error_text_handles_nested_dict() -> None:
    """The exact shape that crashed the worker: message is a nested dict."""
    obj = {"type": "error", "message": {"message": "Failed to refresh token. Please log in again."}}
    out = _coerce_codex_error_text(obj)
    assert isinstance(out, str)
    assert "log in again" in out.lower()


def test_coerce_codex_error_text_plain_string_and_fallback() -> None:
    assert _coerce_codex_error_text({"type": "error", "message": "boom"}) == "boom"
    assert _coerce_codex_error_text({"type": "turn.failed", "error": "nope"}) == "nope"
    # No message/error at all -> never raises, returns a non-empty string.
    assert _coerce_codex_error_text({"type": "error"}) == "error"


def test_codex_error_is_auth_expired() -> None:
    assert _codex_error_is_auth_expired("Failed to refresh token. Please log in again.")
    assert _codex_error_is_auth_expired("401 Unauthorized")
    assert not _codex_error_is_auth_expired("Compilation failed: missing semicolon")
    assert not _codex_error_is_auth_expired("")


# --- integration: spawn() does not crash + falls back ---------------------


class _FakeStream:
    def __init__(self, *, data: bytes = b"") -> None:
        self._data = data
        self._sent = False

    async def read(self, n: int = -1) -> bytes:
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
    """A subprocess whose communicate() returns a fixed (stdout, stderr)."""

    def __init__(self, stdout: bytes, *, returncode: int = 0, streaming: bytes | None = None) -> None:
        self.pid = 4242
        self.returncode = returncode
        self.stdin = _FakeStream()
        # codex reads via communicate(); claude reads via stdout.read()
        self.stdout = _FakeStream(data=streaming or b"")
        self.stderr = _FakeStream()
        self._stdout_bytes = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout_bytes, b""

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _Job:
    def assign(self, _pid: int) -> None:
        pass


async def _drive(worker: CodexDirectWorker, tmp_path: Path) -> list[Any]:
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={"ANTHROPIC_OAUTH_TOKEN": "x"},
        job=_Job(),
        worker_id="cdx",
        log_dir=tmp_path / "logs",
        allowed_tools="Read,Write",
        timeout_s=5.0,
    ):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_codex_dict_error_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NON-auth codex error whose message is a dict must yield a string-result
    ClaudeResult, never raise a Pydantic ValidationError (the worker crash)."""
    stdout = b'{"type":"error","message":{"detail":"compilation blew up"}}\n'

    async def _fake_exec(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(stdout, returncode=1)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    events = await _drive(CodexDirectWorker(), tmp_path)
    final = events[-1]
    assert getattr(final, "is_error", None) is True
    assert isinstance(final.result, str)
    assert "compilation blew up" in final.result


@pytest.mark.asyncio
async def test_codex_auth_expired_falls_back_to_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead ChatGPT login must transparently fall back to the Claude Max worker
    so the mission COMPLETES (final event is claude's success), instead of failing."""
    codex_stdout = (
        b'{"type":"error","message":{"message":"Failed to refresh token. Please log in again."}}\n'
    )
    claude_result_line = (
        b'{"type":"result","subtype":"success","is_error":false,'
        b'"result":"OK","session_id":"s1"}\n'
    )

    calls: dict[str, Any] = {"n": 0, "claude_argv": None}

    async def _fake_exec(*args: Any, **_k: Any) -> _FakeProc:
        calls["n"] += 1
        if calls["n"] == 1:
            # codex spawn -> auth error
            return _FakeProc(codex_stdout, returncode=1)
        # claude fallback spawn -> streams a success result line
        calls["claude_argv"] = list(args)
        return _FakeProc(b"", returncode=0, streaming=claude_result_line)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    # Make the claude fallback hermetic.
    monkeypatch.setattr(
        cdw_claude, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )
    monkeypatch.setattr(cdw_claude, "_resolve_claude_argv_prefix", lambda: ["claude"])

    events = await _drive(CodexDirectWorker(), tmp_path)

    # The mission must SUCCEED via the claude fallback.
    final = events[-1]
    assert getattr(final, "is_error", None) is False, (
        f"expected claude-fallback success, got {final!r}"
    )
    assert final.result == "OK"
    # The fallback must actually have spawned the claude worker.
    assert calls["n"] == 2, "expected a second (claude) spawn after codex auth-fail"
    assert calls["claude_argv"] and calls["claude_argv"][0] == "claude"
