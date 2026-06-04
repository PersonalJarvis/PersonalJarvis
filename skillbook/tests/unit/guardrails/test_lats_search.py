"""LATSEngine.search_and_execute: MCTS-driven candidate selection."""

from __future__ import annotations

import pytest

from skillbook.guardrails.diagnostics import AgentDoG
from skillbook.guardrails.lats import LATSEngine, StepStatus


async def test_search_with_single_succeeding_candidate_returns_ok() -> None:
    engine = LATSEngine(dog=AgentDoG())

    async def call(params: dict) -> dict:
        return {"echo": params}

    out = await engine.search_and_execute(
        task_id="t1",
        actor="x",
        candidate_params=[{"v": 1}],
        call=call,
    )
    assert out.status is StepStatus.OK
    assert out.result == {"echo": {"v": 1}}


async def test_search_finds_winner_among_three_candidates() -> None:
    engine = LATSEngine(dog=AgentDoG())

    async def call(params: dict) -> dict:
        if params.get("kind") == "winner":
            return {"ok": True}
        raise TimeoutError(f"loser params {params}")

    out = await engine.search_and_execute(
        task_id="t2",
        actor="x",
        candidate_params=[{"kind": "loser_a"}, {"kind": "winner"}, {"kind": "loser_b"}],
        call=call,
        iterations=8,
    )
    assert out.status is StepStatus.OK
    assert out.result == {"ok": True}
    assert out.params == {"kind": "winner"}


async def test_search_returns_last_failure_when_all_candidates_fail() -> None:
    engine = LATSEngine(dog=AgentDoG())

    async def call(params: dict) -> dict:
        raise TimeoutError("always fails")

    out = await engine.search_and_execute(
        task_id="t3",
        actor="x",
        candidate_params=[{"a": 1}, {"a": 2}, {"a": 3}],
        call=call,
        iterations=5,
    )
    assert out.status is StepStatus.BLOCKED_BY_GUARDRAIL
    assert out.diagnostic is not None
    assert out.diagnostic.failure_mode.value == "timeout"


async def test_search_with_empty_candidates_returns_structured_block() -> None:
    engine = LATSEngine(dog=AgentDoG())

    async def call(params: dict) -> dict:
        return {}

    out = await engine.search_and_execute(
        task_id="t4",
        actor="x",
        candidate_params=[],
        call=call,
    )
    assert out.status is StepStatus.BLOCKED_BY_GUARDRAIL
    assert out.diagnostic is not None
    assert "no candidates" in out.diagnostic.evidence.lower()


async def test_search_stops_early_on_first_ok_no_extra_calls() -> None:
    engine = LATSEngine(dog=AgentDoG())
    calls: list[dict] = []

    async def call(params: dict) -> dict:
        calls.append(dict(params))
        return {"hit": params.get("idx")}

    await engine.search_and_execute(
        task_id="t5",
        actor="x",
        candidate_params=[{"idx": 0}, {"idx": 1}, {"idx": 2}],
        call=call,
        iterations=10,
    )
    assert len(calls) == 1
