"""Regression for the 2026-06-08 sub-mission instant-fail bug.

Forensic ground truth (`data/missions.db`, missions ``019ea82e-3fac`` /
``019ea82e-8cc5`` / ``019ea830-072f``): every mission died in ~3 s with
``WorkerKilled reason=user`` and ``MissionFailed reason=task_error``. The
verbatim worker error in ``data/jarvis_desktop.log`` was::

    ClaudeDirectWorker: primary provider is grok, expected claude-api
    ClaudeDirectWorker: primary provider is openai-codex, expected claude-api

Root cause: post-Welle-4 the OpenClaw-backed ``SubJarvisWorker`` was removed
(the ~92% nested-claude hang), so ``jarvis.missions.init._worker_factory``
routes EVERY ``[brain.sub_jarvis].provider`` that is not ``openai-codex`` /
``chatgpt`` (grok, gemini, openai, openrouter, openclaw-claude, AND the unset
default — whose chain resolver falls back to the ``("grok", "grok-4.3")`` stub)
to ``ClaudeDirectWorker``. But the worker still carried a guard that *refused*
to run unless the resolved provider was ``claude-api`` — a fall-through to a
worker that no longer exists. The result: with ``sub_jarvis.provider`` set to
anything but ``claude-api`` / codex, EVERY mission failed instantly.

The fix: ``ClaudeDirectWorker`` is the universal heavy-worker fallback; it now
always runs on the Claude Max OAuth ``claude`` CLI with a *valid claude model*
(never a foreign slug like ``grok-4.3``), only LOGGING when the configured
provider differs (anti-silent-fallback — legible, never a hidden swap).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.workers import claude_direct_worker as cdw
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.provider_chain import _FallbackStep


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


class _OkProc:
    """A subprocess that streams one successful result line then exits 0."""

    def __init__(self, result_line: bytes) -> None:
        self.pid = 4242
        self.returncode: int | None = 0
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(data=result_line)
        self.stderr = _FakeStream()

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"", b"")


class _Job:
    def assign(self, _pid: int) -> None:
        pass


_RESULT_LINE = (
    b'{"type":"result","subtype":"success","is_error":false,'
    b'"result":"OK","session_id":"s1"}\n'
)


@pytest.mark.parametrize("provider,model", [
    ("grok", "grok-4.3"),       # unset sub_jarvis -> chain stub default
    ("gemini", "gemini-3.1-pro-preview"),
    ("openai", "gpt-5.5-pro"),
    ("openrouter", "some/model"),
])
@pytest.mark.asyncio
async def test_non_claude_provider_runs_on_claude_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, model: str
) -> None:
    """A non-claude sub_jarvis provider routed here MUST run on Claude, never
    refuse with 'primary provider is X, expected claude-api'."""
    monkeypatch.setattr(
        cdw, "_resolve_provider_chain",
        lambda *a, **k: (_FallbackStep(provider, model),),
    )

    captured: dict[str, Any] = {}

    async def _fake_exec(*a: Any, **_k: Any) -> _OkProc:
        captured["argv"] = list(a)
        return _OkProc(_RESULT_LINE)

    monkeypatch.setattr(cdw.asyncio, "create_subprocess_exec", _fake_exec)

    worker = ClaudeDirectWorker()
    events: list[Any] = []
    async for ev in worker.spawn(
        "do the task",
        worktree=tmp_path,
        env={},
        job=_Job(),
        worker_id="fb",
        log_dir=tmp_path / "logs",
        first_output_timeout_s=5.0,
        timeout_s=5.0,
    ):
        events.append(ev)

    final = events[-1]
    # 1. It must NOT refuse with the provider-mismatch error.
    assert not (
        getattr(final, "is_error", False)
        and "expected claude-api" in (getattr(final, "result", "") or "")
    ), f"worker refused instead of falling back to Claude: {getattr(final, 'result', None)!r}"
    # 2. It must have actually reached the subprocess spawn (no early bail).
    assert "argv" in captured, "worker bailed before create_subprocess_exec"
    argv = captured["argv"]
    # 3. --model must be a real claude model, never the foreign provider slug.
    assert model not in argv, f"foreign provider model leaked into claude argv: {argv}"
    mi = argv.index("--model")
    model_arg = argv[mi + 1]
    assert model_arg.startswith("claude") or model_arg in {"opus", "sonnet", "haiku"}, (
        f"--model must be a claude model, got {model_arg!r}"
    )
    # 4. The run completes normally.
    assert getattr(final, "is_error", None) is False
    assert final.result == "OK"


def test_resolve_claude_model_never_returns_foreign_slug() -> None:
    """_resolve_claude_model must map any non-claude primary to a claude model,
    honour a claude-api primary verbatim, and survive a None primary."""
    # Foreign provider -> a claude model, never the grok slug.
    m = cdw._resolve_claude_model(_FallbackStep("grok", "grok-4.3"))
    assert m != "grok-4.3"
    assert m.startswith("claude") or m in {"opus", "sonnet", "haiku"}
    # claude-api primary with a model -> honoured verbatim.
    assert (
        cdw._resolve_claude_model(_FallbackStep("claude-api", "claude-opus-4-8"))
        == "claude-opus-4-8"
    )
    # None primary -> safe claude default, never a crash.
    none_model = cdw._resolve_claude_model(None)
    assert none_model.startswith("claude") or none_model in {"opus", "sonnet", "haiku"}


def test_default_model_is_reachable_opus_never_fable(monkeypatch) -> None:
    """Maintainer decision 2026-06-14 (supersedes the 2026-06-10 fable mandate):
    ``claude-fable-5`` is approved-access-only and the Claude Max subscription
    cannot reach it via the CLI ("Claude Fable 5 is currently unavailable",
    live mission 019ec615) — so the last-resort default must be a model the
    subscription CAN reach (``claude-opus-4-8``), never the unreachable fable.

    The module constant is the last line of defense when neither
    ``[brain.sub_jarvis].model`` nor ``[brain.providers.claude-api].deep_model``
    is configured; the worker's model-unavailable retry is the safety net on top.
    """
    assert cdw._DEFAULT_CLAUDE_MODEL == "claude-opus-4-8"
    # With config access broken entirely, the resolver must land on the
    # reachable opus default — never the unreachable fable slug, never a crash.
    # (load_config is imported function-locally, so patch it at its source.)
    def _boom(*_a, **_k):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("jarvis.core.config.load_config", _boom)
    resolved = cdw._resolve_claude_model(None)
    assert resolved == "claude-opus-4-8"
    assert resolved != "claude-fable-5", (
        f"unreachable fable default resurfaced: {resolved!r}"
    )
