"""BrainManager two-turn voice/chat confirmation: arm (turn N) + resume (turn N+1).

Turn N defers a consequential tool and speaks a question; the manager arms a
pending state. Turn N+1's "ja"/"nein" is classified deterministically and the
stashed action is executed via the ToolExecutor (never re-decided by the LLM).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _FakeExecutor:
    def __init__(self, *, success: bool = True) -> None:
        self.confirmed: list[UUID] = []
        self.cancelled: list[UUID] = []
        self._success = success

    async def execute_confirmed(self, trace_id, *, user_utterance="", **_):  # type: ignore[no-untyped-def]
        self.confirmed.append(trace_id)
        return SimpleNamespace(success=self._success, output="sent", error=None)

    async def cancel_pending(self, trace_id):  # type: ignore[no-untyped-def]
        self.cancelled.append(trace_id)
        return True


def _manager(executor: _FakeExecutor) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    return BrainManager(
        config=cfg, bus=EventBus(), tools={}, tool_executor=executor,
    )


def test_config_flag_enables_by_default_and_can_be_disabled() -> None:
    assert _manager(_FakeExecutor())._voice_confirm_enabled is True
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    cfg.brain.voice_confirm = False
    mgr = BrainManager(
        config=cfg, bus=EventBus(), tools={}, tool_executor=_FakeExecutor(),
    )
    assert mgr._voice_confirm_enabled is False


def _arm(mgr: BrainManager, tid: UUID, tool: str = "gmail") -> None:
    mgr._arm_voice_confirm(
        {"trace_id": str(tid), "tool_name": tool},
        "schick die E-Mail an Tom",
    )


def test_arm_records_pending_with_trace_and_language() -> None:
    mgr = _manager(_FakeExecutor())
    tid = uuid4()
    _arm(mgr, tid)
    assert mgr._pending_voice_confirm is not None
    assert mgr._pending_voice_confirm.trace_id == tid
    assert mgr._pending_voice_confirm.lang == "de"
    assert mgr._pending_voice_confirm.tool_name == "gmail"


def test_has_pending_voice_confirm_reflects_state() -> None:
    """The speech pipeline consults this to keep the session open while a
    yes/no is awaited — otherwise the turn finalizes and (in single-turn mode)
    the session hangs up before the user can answer (forensic 2026-06-26)."""
    mgr = _manager(_FakeExecutor())
    assert mgr.has_pending_voice_confirm() is False
    _arm(mgr, uuid4())
    assert mgr.has_pending_voice_confirm() is True


@pytest.mark.asyncio
async def test_resume_confirm_executes_and_reports_done() -> None:
    executor = _FakeExecutor(success=True)
    mgr = _manager(executor)
    tid = uuid4()
    _arm(mgr, tid)
    out = await mgr._resume_voice_confirm("ja, mach das")
    assert executor.confirmed == [tid]
    assert "erledigt" in out.lower()
    # pending cleared after a terminal outcome.
    assert mgr._pending_voice_confirm is None


@pytest.mark.asyncio
async def test_resume_veto_cancels_and_acknowledges() -> None:
    executor = _FakeExecutor()
    mgr = _manager(executor)
    tid = uuid4()
    _arm(mgr, tid)
    out = await mgr._resume_voice_confirm("nein, lass das")
    assert executor.cancelled == [tid]
    assert executor.confirmed == []
    assert out.strip() != ""
    assert mgr._pending_voice_confirm is None


@pytest.mark.asyncio
async def test_resume_ambiguous_keeps_pending_and_reasks() -> None:
    executor = _FakeExecutor()
    mgr = _manager(executor)
    tid = uuid4()
    _arm(mgr, tid)
    out = await mgr._resume_voice_confirm("vielleicht")
    # Still waiting — neither executed nor cancelled yet.
    assert executor.confirmed == []
    assert executor.cancelled == []
    assert mgr._pending_voice_confirm is not None
    assert mgr._pending_voice_confirm.reasks == 1
    assert out.strip() != ""


@pytest.mark.asyncio
async def test_resume_unknown_drops_pending_and_falls_through() -> None:
    """An unrelated utterance means the user moved on — drop the action (safe,
    not executed) and let the turn be processed normally (return None)."""
    executor = _FakeExecutor()
    mgr = _manager(executor)
    tid = uuid4()
    _arm(mgr, tid)
    out = await mgr._resume_voice_confirm("wie spät ist es")
    assert out is None
    assert executor.confirmed == []
    assert executor.cancelled == [tid]  # the consequential action is dropped
    assert mgr._pending_voice_confirm is None


@pytest.mark.asyncio
async def test_resume_reports_failure_when_action_fails() -> None:
    executor = _FakeExecutor(success=False)
    mgr = _manager(executor)
    tid = uuid4()
    _arm(mgr, tid)
    out = await mgr._resume_voice_confirm("ja")
    assert executor.confirmed == [tid]
    assert out.strip() != ""
    assert "erledigt" not in out.lower()  # not a success phrase
    assert mgr._pending_voice_confirm is None


@pytest.mark.asyncio
async def test_resume_without_pending_returns_none() -> None:
    mgr = _manager(_FakeExecutor())
    assert mgr._pending_voice_confirm is None
    out = await mgr._resume_voice_confirm("ja")
    assert out is None


@pytest.mark.asyncio
async def test_ambiguous_exhaustion_drops_after_max_reasks() -> None:
    executor = _FakeExecutor()
    mgr = _manager(executor)
    tid = uuid4()
    _arm(mgr, tid)
    # Three ambiguous answers: 1st + 2nd re-ask, 3rd exhausts and drops.
    await mgr._resume_voice_confirm("vielleicht")
    await mgr._resume_voice_confirm("warte")
    out = await mgr._resume_voice_confirm("hmm")
    assert mgr._pending_voice_confirm is None
    assert executor.cancelled == [tid]
    assert out.strip() != ""
