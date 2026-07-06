"""A tool-incapable worker model must fail the mission EARLY with an
actionable message — not via empty-diff critic exhaustion (fresh-machine
Bug 10, forensics). Covers both:

1. the pre-gate on ``brain.can_call_tools()`` before the turn loop even
   starts (``complete()`` must never be reached), and
2. the wrap of the FIRST ``brain.complete()`` round-trip, converting an
   OpenRouter-style "no endpoints support tool use" provider error into the
   same honest message instead of a raw/opaque failure.

AP-21: gate on CAPABILITY, and only on an explicit "no" — absent or raising
probes are UNKNOWN and must PROCEED.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker


class _NoToolsBrain:
    """Explicitly reports it cannot call tools — the gate must catch this
    BEFORE ``complete()`` is ever awaited."""

    def can_call_tools(self) -> bool:
        return False

    async def complete(self, req: BrainRequest):  # pragma: no cover - must never run
        raise AssertionError("complete() called despite can_call_tools()==False")
        yield  # pragma: no cover


class _RaisingProbeBrain:
    """A capability probe that raises is UNKNOWN, not "no" — must proceed
    (AP-21) and reach ``complete()`` normally."""

    def can_call_tools(self) -> bool:
        raise RuntimeError("capability catalog unavailable")

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="end_turn")


class _NoProbeBrain:
    """No ``can_call_tools`` attribute at all — absent is also UNKNOWN and
    must proceed, mirroring the FakeBrain used by the sibling worker tests."""

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="end_turn")


class _NoEndpointsBrain:
    """Reports itself tool-capable, but the FIRST live round-trip fails with
    an OpenRouter-shaped 'no endpoints support tool use' error — the gate
    can't catch this in advance; only the first-call wrap can."""

    def can_call_tools(self) -> bool:
        return True

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        raise RuntimeError(
            "Error code: 404 - No endpoints found that support tool use."
        )
        yield  # pragma: no cover


def _patch_brain(monkeypatch: pytest.MonkeyPatch, brain: object) -> None:
    import jarvis.missions.workers.api_agent_worker as mod

    monkeypatch.setattr(mod, "_build_brain", lambda provider, model: brain, raising=False)


async def _drain(worker: ApiAgentWorker, **kw):  # noqa: ANN003
    events = []
    async for ev in worker.spawn(**kw):
        events.append(ev)
    return events


async def test_tool_incapable_model_fails_early(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_brain(monkeypatch, _NoToolsBrain())
    worker = ApiAgentWorker("openrouter")
    events = await _drain(
        worker, prompt="build x", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs",
    )

    errors = [e for e in events if getattr(e, "is_error", False)]
    assert errors, "expected an early error result"
    msg = (errors[-1].result or "").lower()
    assert "cannot call tools" in msg
    assert "jarvis-agents" in msg
    # the gate fires before the turn loop — no assistant/user turn was emitted
    kinds = [type(e).__name__ for e in events]
    assert "ClaudeAssistantMessage" not in kinds
    assert kinds[-1] == "ClaudeResult"


async def test_raising_capability_probe_is_unknown_and_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_brain(monkeypatch, _RaisingProbeBrain())
    worker = ApiAgentWorker("openrouter")
    events = await _drain(
        worker, prompt="build x", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs",
    )
    assert events[-1].is_error is False


async def test_missing_capability_probe_is_unknown_and_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_brain(monkeypatch, _NoProbeBrain())
    worker = ApiAgentWorker("openrouter")
    events = await _drain(
        worker, prompt="build x", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs",
    )
    assert events[-1].is_error is False


async def test_first_round_trip_no_endpoints_error_is_translated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_brain(monkeypatch, _NoEndpointsBrain())
    worker = ApiAgentWorker("openrouter")
    events = await _drain(
        worker, prompt="build x", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs",
    )

    errors = [e for e in events if getattr(e, "is_error", False)]
    assert errors, "expected the raw provider error to be translated"
    msg = (errors[-1].result or "").lower()
    assert "cannot call tools" in msg
    assert "jarvis-agents" in msg
