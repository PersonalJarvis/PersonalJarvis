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

import asyncio
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


@pytest.fixture(autouse=True)
def _reset_codex_auth_marker():
    """The codex needs_reauth + the claude/codex quota-cooldown flags are
    process globals — reset all three around every test so a fallback test
    doesn't pollute the rest of the suite."""
    from jarvis.claude_quota_state import clear_claude_quota_cooldown
    from jarvis.codex_auth_state import clear_codex_needs_reauth
    from jarvis.codex_quota_state import clear_codex_quota_cooldown

    clear_codex_needs_reauth()
    clear_claude_quota_cooldown()
    clear_codex_quota_cooldown()
    yield
    clear_codex_needs_reauth()
    clear_claude_quota_cooldown()
    clear_codex_quota_cooldown()


@pytest.fixture
def _viable_claude_fallback(monkeypatch: pytest.MonkeyPatch):
    """Pin the codex->claude fallback gate to 'Claude IS viable'.

    The gate (2026-07-07 incident) consults binary presence + auth viability
    before the nested Claude spawn; tests that exercise the fallback must not
    depend on this machine's real ~/.claude state."""
    from jarvis.missions import init as mi

    monkeypatch.setattr(cdw_claude, "_resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(mi, "_claude_cli_auth_viable", lambda: True)


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


def test_codex_error_is_usage_limited() -> None:
    from jarvis.missions.workers.codex_direct_worker import _codex_error_is_usage_limited

    # The exact 2026-06-09 message (re-authed codex hit its ChatGPT cap).
    assert _codex_error_is_usage_limited(
        "You've hit your usage limit. Upgrade to Pro … purchase more credits or "
        "try again at 7:40 PM."
    )
    assert _codex_error_is_usage_limited("429 Too Many Requests")
    assert _codex_error_is_usage_limited("rate limit exceeded")
    # A dead login is NOT a usage cap (different fallback semantics).
    assert not _codex_error_is_usage_limited("Please log in again.")
    assert not _codex_error_is_usage_limited("Compilation failed")


# --- integration: spawn() does not crash + falls back ---------------------


class _FakeStream:
    def __init__(self, *, data: bytes = b"") -> None:
        self._data = data
        self._sent = False
        self._lines = data.splitlines(keepends=True)
        self._line_idx = 0

    async def read(self, n: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._data

    async def readline(self) -> bytes:
        # Line-by-line view of the same data (codex streams via readline now).
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
    """A subprocess whose communicate() returns a fixed (stdout, stderr)."""

    def __init__(
        self,
        stdout: bytes,
        *,
        returncode: int = 0,
        streaming: bytes | None = None,
        stderr: bytes = b"",
    ) -> None:
        self.pid = 4242
        self.returncode = returncode
        self.stdin = _FakeStream()
        # codex reads line-by-line from stdout (live streaming); the claude
        # fallback path reads a first chunk via stdout.read() then drains the
        # rest via communicate(). `streaming` overrides what stdout serves
        # (used by claude-path fakes); codex fakes serve the stdout bytes.
        self.stdout = _FakeStream(data=streaming if streaming is not None else stdout)
        # Both workers now drain stderr directly from proc.stderr (not via
        # communicate()), so serve the stderr bytes there — matching a real
        # asyncio subprocess whose .stderr is a StreamReader.
        self.stderr = _FakeStream(data=stderr)
        self._stdout_bytes = stdout
        self._stderr_bytes = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout_bytes, self._stderr_bytes

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
@pytest.mark.usefixtures("_viable_claude_fallback")
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


@pytest.mark.asyncio
async def test_codex_hardcap_timeout_preserves_work_and_flags_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live bug (mission 019eacb8): codex wrote a real deliverable then ran past
    its wall-clock cap. The worker must (1) set the structured `timed_out` flag,
    (2) keep "timeout" in the result, and (3) PRESERVE the partial stdout so the
    file_change tool_use survives parsing — instead of `stdout_bytes=b""` which
    discarded the 17 KB HTML and produced an opaque task_error."""
    ndjson = (
        b'{"type":"thread.started","thread_id":"t1"}\n'
        b'{"type":"item.completed","item":{"type":"file_change",'
        b'"changes":[{"path":"aktuelle-emails.html"}]}}\n'
        b'{"type":"item.completed","item":{"type":"agent_message",'
        b'"text":"Created the HTML."}}\n'
    )

    class _HangingStream(_FakeStream):
        """Serves its lines, then hangs forever (no EOF) — a stuck codex."""

        async def readline(self) -> bytes:
            line = await super().readline()
            if line:
                return line
            await asyncio.Event().wait()  # block until cancelled by wait_for
            return b""

    class _TimeoutProc:
        """Streams its lines (work done) then hangs until the hard cap."""

        def __init__(self) -> None:
            self.pid = 7
            self.returncode = -9
            self.stdin = _FakeStream()
            self.stdout = _HangingStream(data=ndjson)
            self.stderr = _FakeStream()

        async def communicate(self) -> tuple[bytes, bytes]:
            raise asyncio.TimeoutError  # legacy path — not used by streaming

        async def wait(self) -> int:
            return -9

        def kill(self) -> None:
            self.returncode = -9

    async def _fake_exec(*_a: Any, **_k: Any) -> _TimeoutProc:
        return _TimeoutProc()

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(cdw, "_HARDCAP_GRACE_S", 0.2)

    worker = CodexDirectWorker()
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="t",
        log_dir=tmp_path / "logs",
        timeout_s=1.0,
        first_output_timeout_s=1.0,
    ):
        events.append(ev)

    final = events[-1]
    # 1. structured timeout flag (the orchestrator keys off THIS, not the string)
    assert getattr(final, "timed_out", False) is True
    assert final.is_error is True
    # 2. "timeout" still in the result (belt-and-suspenders)
    assert "timeout" in (final.result or "").lower()
    # 3. the file_change tool_use survived → work NOT discarded
    tool_use_seen = any(
        getattr(ev, "type", None) == "assistant"
        and any(
            isinstance(b, dict) and b.get("type") == "tool_use"
            for b in (getattr(ev, "message", {}) or {}).get("content", [])
        )
        for ev in events
    )
    assert tool_use_seen, "file_change tool_use must survive the timeout (work preserved)"


# --- mirror direction: claude quota-limited -> codex fallback ---------------


@pytest.mark.asyncio
async def test_claude_session_limit_falls_back_to_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live mission 019eb2fd (2026-06-10 21:23): with sub_jarvis.provider =
    claude-api and the Claude Max five-hour window exhausted, every mission
    died in ~16 s with "You've hit your session limit · resets 11:10pm".
    codex (a separate subscription) was healthy the whole time. Mirror of
    the codex->claude fallback: a quota-limited claude run that did NO real
    work must transparently complete on the codex worker.
    """
    claude_limit_line = (
        b'{"type":"result","subtype":"success","is_error":true,'
        b'"result":"You\'ve hit your session limit \xc2\xb7 resets 11:10pm '
        b'(Europe/Berlin)","session_id":"s1"}\n'
    )
    codex_ndjson = (
        b'{"type":"thread.started","thread_id":"t9"}\n'
        b'{"type":"item.completed","item":{"type":"agent_message",'
        b'"text":"codex took over"}}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n'
    )

    calls: dict[str, Any] = {"n": 0}

    async def _fake_exec(*args: Any, **_k: Any) -> _FakeProc:
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(b"", returncode=1, streaming=claude_limit_line)
        return _FakeProc(b"", returncode=0, streaming=codex_ndjson)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        cdw_claude, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )
    monkeypatch.setattr(cdw_claude, "_resolve_claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(cdw, "_codex_oauth_available", lambda: True)

    worker = cdw_claude.ClaudeDirectWorker()
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="cl",
        log_dir=tmp_path / "logs",
        timeout_s=5.0,
    ):
        events.append(ev)

    final = events[-1]
    assert getattr(final, "is_error", None) is False, (
        f"expected codex-fallback success, got {final!r}"
    )
    assert "codex took over" in (final.result or "")
    assert calls["n"] == 2, "expected a second (codex) spawn after the claude limit"


@pytest.mark.asyncio
async def test_claude_limit_no_fallback_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anti-ping-pong: a nested fallback run (allow_backend_fallback=False)
    must surface the limit error honestly instead of bouncing back."""
    claude_limit_line = (
        b'{"type":"result","subtype":"success","is_error":true,'
        b'"result":"You\'ve hit your session limit \xc2\xb7 resets 11:10pm",'
        b'"session_id":"s1"}\n'
    )
    calls: dict[str, Any] = {"n": 0}

    async def _fake_exec(*_a: Any, **_k: Any) -> _FakeProc:
        calls["n"] += 1
        return _FakeProc(b"", returncode=1, streaming=claude_limit_line)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        cdw_claude, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )
    monkeypatch.setattr(cdw_claude, "_resolve_claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(cdw, "_codex_oauth_available", lambda: True)

    worker = cdw_claude.ClaudeDirectWorker()
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="cl",
        log_dir=tmp_path / "logs",
        timeout_s=5.0,
        allow_backend_fallback=False,
    ):
        events.append(ev)

    final = events[-1]
    assert final.is_error is True
    assert "session limit" in (final.result or "").lower()
    assert calls["n"] == 1, "nested fallback run must not spawn another backend"


# --- proactive quota cooldown flag (claude_quota_state) --------------------


@pytest.mark.asyncio
async def test_claude_quota_limit_arms_cooldown_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Claude quota limit arms the session cooldown so the factory can route
    subsequent missions straight to codex (no wasted probe)."""
    from jarvis.claude_quota_state import claude_in_quota_cooldown

    claude_limit_line = (
        b'{"type":"result","subtype":"success","is_error":true,'
        b'"result":"You\'ve hit your session limit \xc2\xb7 resets 11:10pm",'
        b'"session_id":"s1"}\n'
    )
    codex_ndjson = (
        b'{"type":"thread.started","thread_id":"t9"}\n'
        b'{"type":"item.completed","item":{"type":"agent_message",'
        b'"text":"done"}}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
    )
    calls: dict[str, Any] = {"n": 0}

    async def _fake_exec(*_a: Any, **_k: Any) -> _FakeProc:
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(b"", returncode=1, streaming=claude_limit_line)
        return _FakeProc(b"", returncode=0, streaming=codex_ndjson)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        cdw_claude, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )
    monkeypatch.setattr(cdw_claude, "_resolve_claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(cdw, "_codex_oauth_available", lambda: True)

    assert claude_in_quota_cooldown() is False
    async for _ in cdw_claude.ClaudeDirectWorker().spawn(
        "task", worktree=tmp_path, env={}, job=_Job(), worker_id="cl",
        log_dir=tmp_path / "logs", timeout_s=5.0,
    ):
        pass
    assert claude_in_quota_cooldown() is True, "quota limit must arm the cooldown"


@pytest.mark.asyncio
async def test_claude_success_clears_cooldown_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy Claude run clears the cooldown (the window recovered)."""
    from jarvis.claude_quota_state import (
        claude_in_quota_cooldown,
        mark_claude_quota_cooldown,
    )

    success_line = (
        b'{"type":"result","subtype":"success","is_error":false,'
        b'"result":"OK","session_id":"s1"}\n'
    )

    async def _fake_exec(*_a: Any, **_k: Any) -> _FakeProc:
        return _FakeProc(b"", returncode=0, streaming=success_line)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        cdw_claude, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-opus-4-8"),),
    )
    monkeypatch.setattr(cdw_claude, "_resolve_claude_argv_prefix", lambda: ["claude"])

    mark_claude_quota_cooldown()
    assert claude_in_quota_cooldown() is True
    async for _ in cdw_claude.ClaudeDirectWorker().spawn(
        "task", worktree=tmp_path, env={}, job=_Job(), worker_id="cl",
        log_dir=tmp_path / "logs", timeout_s=5.0,
    ):
        pass
    assert claude_in_quota_cooldown() is False, "a Claude success must clear cooldown"


# --- claude model-unavailable -> retry without --model (CLI default) -------


def test_claude_error_is_model_unavailable() -> None:
    from jarvis.missions.workers.claude_direct_worker import (
        _claude_error_is_model_unavailable as f,
    )

    assert f("Claude Fable 5 is currently unavailable. Learn more: ...")
    assert f("There's an issue with the selected model (claude-fable-5). "
             "It may not exist or you may not have access to it.")
    assert f("model not found")
    assert not f("Compilation failed")
    assert not f("")


@pytest.mark.asyncio
async def test_claude_unavailable_model_retries_without_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An approved-access model the subscription lacks (claude-fable-5, live
    mission 019ec615 2026-06-14) must transparently retry on the CLI default
    so the mission completes — not die as task_error."""
    unavailable = (
        b'{"type":"result","subtype":"success","is_error":true,'
        b'"result":"Claude Fable 5 is currently unavailable.","session_id":"s1"}\n'
    )
    success = (
        b'{"type":"result","subtype":"success","is_error":false,'
        b'"result":"DONE","session_id":"s2"}\n'
    )
    spawns: list[list[str]] = []

    async def _fake_exec(*args: Any, **_k: Any) -> _FakeProc:
        spawns.append(list(args))
        line = unavailable if len(spawns) == 1 else success
        return _FakeProc(b"", returncode=0 if len(spawns) > 1 else 1, streaming=line)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        cdw_claude, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-fable-5"),),
    )
    monkeypatch.setattr(cdw_claude, "_resolve_claude_argv_prefix", lambda: ["claude"])

    events: list[Any] = []
    async for ev in cdw_claude.ClaudeDirectWorker().spawn(
        "task", worktree=tmp_path, env={}, job=_Job(), worker_id="cl",
        log_dir=tmp_path / "logs", timeout_s=5.0,
    ):
        events.append(ev)

    final = events[-1]
    assert final.is_error is False, f"expected default-model retry success, got {final!r}"
    assert final.result == "DONE"
    assert len(spawns) == 2, "must retry exactly once"
    assert "--model" in spawns[0], "first attempt carries the configured model"
    assert "claude-fable-5" in spawns[0]
    assert "--model" not in spawns[1], "retry must omit --model (use CLI default)"


@pytest.mark.asyncio
async def test_claude_unavailable_model_on_stderr_only_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GAP-2: the claude CLI can write the model-rejection to STDERR while
    stdout carries no `result` record at all (so the assembled result text is
    just 'claude exited with code 1'). The unavailable-model detector must read
    stderr too, otherwise the rejection slips past and the mission fails instead
    of retrying on the CLI default."""
    success = (
        b'{"type":"result","subtype":"success","is_error":false,'
        b'"result":"DONE","session_id":"s2"}\n'
    )
    spawns: list[list[str]] = []

    async def _fake_exec(*args: Any, **_k: Any) -> _FakeProc:
        spawns.append(list(args))
        if len(spawns) == 1:
            # empty stdout, error ONLY on stderr, non-zero exit
            return _FakeProc(
                b"", returncode=1, streaming=b"",
                stderr=b"There's an issue with the selected model "
                       b"(claude-fable-5). It may not exist or you may not "
                       b"have access to it.",
            )
        return _FakeProc(b"", returncode=0, streaming=success)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        cdw_claude, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep("claude-api", "claude-fable-5"),),
    )
    monkeypatch.setattr(cdw_claude, "_resolve_claude_argv_prefix", lambda: ["claude"])

    events: list[Any] = []
    async for ev in cdw_claude.ClaudeDirectWorker().spawn(
        "task", worktree=tmp_path, env={}, job=_Job(), worker_id="cl",
        log_dir=tmp_path / "logs", timeout_s=5.0,
    ):
        events.append(ev)

    final = events[-1]
    assert final.is_error is False, f"stderr-only rejection must retry, got {final!r}"
    assert final.result == "DONE"
    assert len(spawns) == 2, "must retry exactly once on a stderr-only rejection"
    assert "--model" not in spawns[1], "retry must omit --model (use CLI default)"
