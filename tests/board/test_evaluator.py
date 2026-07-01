"""Tests for the AchievementEvaluator (Phase B)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from jarvis.board.evaluator import AchievementEvaluator
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AchievementUnlocked,
    ActionExecuted,
    HarnessCompleted,
    JarvisAgentTaskCompleted,
    TaskCompleted,
)
from jarvis.core.protocols import HarnessResult


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _action(tool: str, success: bool = True, trace: UUID | None = None) -> ActionExecuted:
    return ActionExecuted(
        trace_id=trace or uuid4(),
        tool_name=tool,
        success=success,
        duration_ms=10,
    )


def _mcp_ok() -> HarnessCompleted:
    return HarnessCompleted(
        harness="mcp-remote",
        result=HarnessResult(exit_code=0, is_final=True),
    )


def _sub_ok(duration_s: float = 60.0) -> JarvisAgentTaskCompleted:
    return JarvisAgentTaskCompleted(success=True, duration_s=duration_s)


def _task() -> TaskCompleted:
    return TaskCompleted(task_id="t1", duration_ms=100)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_first_mcp_unlocks_on_mcp_success(tmp_path: Path) -> None:
    ev = AchievementEvaluator(tmp_path / "personal.db")
    unlocks = ev.evaluate_sync(_mcp_ok())
    ids = {spec.id for spec, _ in unlocks}
    assert "first_mcp" in ids


def test_evaluator_is_idempotent(tmp_path: Path) -> None:
    """Plan §5-B: same event twice must NOT lead to a double unlock."""
    ev = AchievementEvaluator(tmp_path / "personal.db")
    first = ev.evaluate_sync(_mcp_ok())
    second = ev.evaluate_sync(_mcp_ok())
    ids_first = {spec.id for spec, _ in first}
    ids_second = {spec.id for spec, _ in second}
    assert "first_mcp" in ids_first
    assert "first_mcp" not in ids_second

    rows = ev._connect().execute(
        "SELECT COUNT(*) AS c FROM achievements WHERE id='first_mcp'"
    ).fetchone()
    assert rows["c"] == 1


def test_openclaw_summoner(tmp_path: Path) -> None:
    ev = AchievementEvaluator(tmp_path / "personal.db")
    unlocks = ev.evaluate_sync(_sub_ok())
    ids = {spec.id for spec, _ in unlocks}
    assert "openclaw_summoner" in ids


def test_tool_ladder(tmp_path: Path) -> None:
    """Nach 5 distinct tools unlockt ``tool_dabbler``, nach 15 ``journeyman``."""
    ev = AchievementEvaluator(tmp_path / "personal.db")
    unlocked: set[str] = set()
    for i in range(20):
        tool = f"tool_{i:02d}"
        res = ev.evaluate_sync(_action(tool))
        for spec, _ in res:
            unlocked.add(spec.id)
    assert "tool_dabbler" in unlocked
    assert "tool_journeyman" in unlocked
    # Master needs 30 — not reached with 20.
    assert "tool_master" not in unlocked


def test_triple_combo_requires_same_trace(tmp_path: Path) -> None:
    ev = AchievementEvaluator(tmp_path / "personal.db")
    trace = uuid4()
    # Drei verschiedene Tools unter derselben trace_id → unlock.
    ev.evaluate_sync(_action("bash", trace=trace))
    ev.evaluate_sync(_action("search_web", trace=trace))
    res = ev.evaluate_sync(_action("write_file", trace=trace))
    ids = {spec.id for spec, _ in res}
    assert "triple_combo" in ids


def test_triple_combo_not_unlocked_across_traces(tmp_path: Path) -> None:
    ev = AchievementEvaluator(tmp_path / "personal.db")
    # Drei verschiedene Tools — aber in DREI verschiedenen trace_ids.
    got: set[str] = set()
    for tool in ("bash", "search_web", "write_file"):
        for spec, _ in ev.evaluate_sync(_action(tool, trace=uuid4())):
            got.add(spec.id)
    assert "triple_combo" not in got


def test_centennial_requires_100_tasks(tmp_path: Path) -> None:
    ev = AchievementEvaluator(tmp_path / "personal.db")
    for _ in range(99):
        ev.evaluate_sync(_task())
    assert "centennial" not in {
        r["id"] for r in ev._connect().execute("SELECT id FROM achievements")
    }
    ev.evaluate_sync(_task())
    assert "centennial" in {
        r["id"] for r in ev._connect().execute("SELECT id FROM achievements")
    }


def test_restart_restores_counters(tmp_path: Path) -> None:
    """The evaluator rehydrates its state from ``aggregator_meta``."""
    db = tmp_path / "personal.db"
    ev = AchievementEvaluator(db)
    for i in range(5):
        ev.evaluate_sync(_action(f"t{i}"))
    ev.close()

    ev2 = AchievementEvaluator(db)
    ev2.attach()
    assert ev2._ctx is not None
    assert len(ev2._ctx.ever_seen_tools()) == 5


def test_action_execution_with_failure_not_counted(tmp_path: Path) -> None:
    ev = AchievementEvaluator(tmp_path / "personal.db")
    for i in range(6):
        ev.evaluate_sync(_action(f"t{i}", success=False))
    # No tool had success=True → tool_dabbler stays locked.
    ids = {r["id"] for r in ev._connect().execute("SELECT id FROM achievements")}
    assert "tool_dabbler" not in ids


@pytest.mark.asyncio
async def test_bus_integration_publishes_unlock(tmp_path: Path) -> None:
    """End-to-End: Event auf Bus → Evaluator hoert → AchievementUnlocked publisht."""
    bus = EventBus()
    received: list[AchievementUnlocked] = []

    async def _listener(event: object) -> None:
        if isinstance(event, AchievementUnlocked):
            received.append(event)

    bus.subscribe_all(_listener)

    ev = AchievementEvaluator(tmp_path / "personal.db", bus=bus)
    ev.attach()
    try:
        await bus.publish(_sub_ok())
        await asyncio.sleep(0.05)
    finally:
        ev.close()

    ids = {e.achievement_id for e in received}
    assert "openclaw_summoner" in ids


def test_evaluator_does_not_block_on_evaluator_exception(tmp_path: Path) -> None:
    """If a single evaluator callback crashes, no state leak may occur."""
    ev = AchievementEvaluator(tmp_path / "personal.db")

    # Monkey-patch: a broken evaluator in the chain.
    from jarvis.board import achievements

    original = achievements.ACHIEVEMENTS
    try:
        boom = achievements.AchievementSpec(
            id="boom",
            title="", description="", tier="mastery",
            evaluator=lambda *_: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        achievements.ACHIEVEMENTS = [boom, *original]  # type: ignore[attr-defined]
        unlocks = ev.evaluate_sync(_mcp_ok())
        # Despite the boom evaluator, first_mcp must still unlock.
        assert any(spec.id == "first_mcp" for spec, _ in unlocks)
    finally:
        achievements.ACHIEVEMENTS = original  # type: ignore[attr-defined]
