"""Regression for the 2026-07-06 'all subagent missions fail on claude' incident.

Forensic ground truth (`data/missions.db` missions 019f36e5 + 019f38b1 +
jarvis_desktop.log): the Claude Max OAuth access token in
``~/.claude/.credentials.json`` expired at 02:53 and nothing refreshes it
anymore, but `claude status` still reported connected=True (presence-only
check). Every ClaudeDirectWorker spawn then died in ~15 s with
"Failed to authenticate. API Error: 401 Invalid authentication credentials"
and the mission FAILED terminally — even though a healthy codex ChatGPT login
AND a healthy OpenRouter key were sitting right there (AP-22 violation).

This is the exact mirror of the 2026-06-08 codex incident
(test_codex_auth_fallback.py). The fix mirrors its shape:
(a) ``_claude_error_is_auth_failure`` classifies the error honestly;
(b) the worker marks ``claude_auth_dead`` so the worker factory routes the
    rest of the session cross-family;
(c) when codex is reachable, the worker falls back IN PLACE so the current
    mission still completes.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

import jarvis.claude_auth_state as cas
from jarvis.missions.workers import claude_direct_worker as cdw
from jarvis.missions.workers import codex_direct_worker as codex_mod
from jarvis.missions.workers.claude_direct_worker import (
    ClaudeDirectWorker,
    _claude_error_is_auth_failure,
)
from jarvis.missions.workers.provider_chain import _FallbackStep
from jarvis.missions.workers.stream_consumer import ClaudeResult

# The verbatim live failure text from missions 019f36e5 / 019f38b1.
_LIVE_401 = "Failed to authenticate. API Error: 401 Invalid authentication credentials"


@pytest.fixture(autouse=True)
def _reset_auth_flags():
    """claude_auth_dead + quota cooldown are process globals — reset around
    every test so one fallback test cannot pollute the rest of the suite."""
    from jarvis.claude_quota_state import clear_claude_quota_cooldown

    cas.clear_claude_auth_dead()
    clear_claude_quota_cooldown()
    yield
    cas.clear_claude_auth_dead()
    clear_claude_quota_cooldown()


# --- pure-unit: the auth-failure classifier --------------------------------


def test_auth_failure_marker_matches_the_live_401() -> None:
    assert _claude_error_is_auth_failure(_LIVE_401)


@pytest.mark.parametrize(
    "text",
    [
        "Not logged in · Please run /login",
        "Invalid API key · Fix external API key",
        "OAuth token has expired",
        "authentication_error: invalid x-api-key",
        "401 Unauthorized",
    ],
)
def test_auth_failure_markers_positive(text: str) -> None:
    assert _claude_error_is_auth_failure(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "You've hit your usage limit. Try again at 7:40 PM.",
        "Claude Fable 5 is currently unavailable",
        "subprocess produced no output within 120s startup timeout",
        "Compilation failed: missing semicolon",
    ],
)
def test_auth_failure_markers_negative(text: str) -> None:
    """Quota, model-unavailable, timeout and ordinary errors are NOT auth."""
    assert not _claude_error_is_auth_failure(text)


# --- integration: a 401 spawn marks auth dead + falls back to codex --------


class _FakeStream:
    def __init__(self, *, data: bytes = b"") -> None:
        self._data = data
        self._lines = data.splitlines(keepends=True)
        self._line_idx = 0
        self._sent = False

    async def read(self, n: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._data

    async def readline(self) -> bytes:
        if self._line_idx >= len(self._lines):
            return b""
        line = self._lines[self._line_idx]
        self._line_idx += 1
        return line

    def write(self, _b: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProc:
    def __init__(self, stdout: bytes, *, returncode: int = 1) -> None:
        self.pid = 4242
        self.returncode = returncode
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(data=stdout)
        self.stderr = _FakeStream(data=b"")
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


_401_STREAM = (
    b'{"type":"result","subtype":"error_during_execution","is_error":true,'
    b'"result":"Failed to authenticate. API Error: 401 Invalid authentication '
    b'credentials","session_id":"s-401"}\n'
)


def _pin_claude_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every external surface of ClaudeDirectWorker.spawn to a fake."""

    async def _fake_exec(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(_401_STREAM, returncode=1)

    monkeypatch.setattr(cdw, "create_worker_subprocess", _fake_exec)
    monkeypatch.setattr(cdw, "_resolve_claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(
        "jarvis.claude_auth.claude_cli_supports_safe_mode",
        lambda _prefix: False,
    )
    monkeypatch.setattr(
        cdw,
        "_resolve_provider_chain",
        lambda **_k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )


async def _drive(worker: ClaudeDirectWorker, tmp_path: Path) -> list[Any]:
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-dead"},
        job=_Job(),
        worker_id="cw",
        log_dir=tmp_path / "logs",
        allowed_tools="Read,Write",
        timeout_s=5.0,
        first_output_timeout_s=5.0,
    ):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_401_marks_claude_auth_dead_and_surfaces_honest_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No codex available: the 401 still marks claude auth dead (so the
    factory crosses families on the retry) and the error surfaces verbatim."""
    _pin_claude_spawn(monkeypatch)
    monkeypatch.setattr(codex_mod, "_codex_oauth_available", lambda: False)

    events = await _drive(ClaudeDirectWorker(), tmp_path)

    final = events[-1]
    assert isinstance(final, ClaudeResult)
    assert final.is_error is True
    assert "401" in (final.result or "")
    assert cas.claude_auth_dead() is True


@pytest.mark.asyncio
async def test_401_falls_back_to_codex_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex reachable: the mission completes on the codex worker in place —
    mirror of the codex→claude fallback direction."""
    _pin_claude_spawn(monkeypatch)
    monkeypatch.setattr(codex_mod, "_codex_oauth_available", lambda: True)

    fallback_calls: list[str] = []

    async def _fake_codex_spawn(
        self: Any, prompt: str, **kwargs: Any
    ) -> AsyncIterator[Any]:
        fallback_calls.append(prompt)
        assert kwargs.get("allow_backend_fallback") is False  # no ping-pong
        yield ClaudeResult(
            subtype="success",
            is_error=False,
            session_id="codex-ok",
            result="done on codex",
        )

    monkeypatch.setattr(codex_mod.CodexDirectWorker, "spawn", _fake_codex_spawn)

    events = await _drive(ClaudeDirectWorker(), tmp_path)

    final = events[-1]
    assert isinstance(final, ClaudeResult)
    assert final.is_error is False
    assert final.session_id == "codex-ok"
    assert fallback_calls, "codex fallback worker was never spawned"
    assert cas.claude_auth_dead() is True


@pytest.mark.asyncio
async def test_success_clears_claude_auth_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy claude run clears the flag (mirror of the quota clear)."""
    ok_stream = (
        b'{"type":"result","subtype":"success","is_error":false,'
        b'"result":"done","session_id":"s-ok"}\n'
    )

    async def _fake_exec(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(ok_stream, returncode=0)

    monkeypatch.setattr(cdw, "create_worker_subprocess", _fake_exec)
    monkeypatch.setattr(cdw, "_resolve_claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(
        "jarvis.claude_auth.claude_cli_supports_safe_mode",
        lambda _prefix: False,
    )
    monkeypatch.setattr(
        cdw,
        "_resolve_provider_chain",
        lambda **_k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )

    cas.mark_claude_auth_dead(fingerprint="fp-old")
    events = await _drive(ClaudeDirectWorker(), tmp_path)

    assert events[-1].is_error is False
    assert cas.claude_auth_dead() is False
