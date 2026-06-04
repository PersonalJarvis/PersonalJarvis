"""Generator route to MCTS: 'search_alternatives' strategy drives LATSEngine.search_and_execute."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from skillbook.ace_core.generator import Generator
from skillbook.ace_core.models import Task, TaskStatus
from skillbook.guardrails.diagnostics import AgentDoG
from skillbook.guardrails.lats import LATSEngine
from skillbook.memory_layer.models import Rule
from skillbook.memory_layer.store import SQLiteMemoryStore


class _ParamMatchingActor:
    """Succeeds only when params == winning_params; otherwise raises TimeoutError."""

    def __init__(self, name: str, winning_params: dict) -> None:
        self.name = name
        self._winning = winning_params
        self.call_count = 0

    async def call(self, params: dict) -> dict:
        self.call_count += 1
        if params == self._winning:
            return {"ok": True, "params": params}
        raise TimeoutError(f"params {params} are losing")


async def _zero_sleep(_: float) -> None:
    return None


@pytest.fixture
async def memory(tmp_path: Path):
    s = SQLiteMemoryStore(db_path=tmp_path / "mcts.db")
    await s.open()
    yield s
    await s.close()


async def test_generator_uses_mcts_when_rule_says_search_alternatives(memory) -> None:
    """When the active rule's strategy.kind == 'search_alternatives', the Generator
    routes through LATSEngine.search_and_execute and returns the winning candidate."""
    winning = {"kind": "winner"}
    await memory.put_rule(
        Rule(
            id="rule_search_x",
            trigger={"actor": "x"},
            strategy={
                "kind": "search_alternatives",
                "candidate_params": [
                    {"kind": "loser_a"},
                    winning,
                    {"kind": "loser_b"},
                ],
                "iterations": 8,
            },
            source_peer="p",
            created_at_ns=time.time_ns(),
        )
    )
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    actor = _ParamMatchingActor("x", winning_params=winning)
    gen.register_actor(actor)

    res = await gen.run_task(Task(id="t_mcts", intent="x", actor="x"))

    assert res.status is TaskStatus.OK
    assert res.result is not None
    assert res.result["params"] == winning
    assert res.rule_applied == "rule_search_x"


async def test_generator_mcts_writes_kg_relation_for_winning_candidate(memory) -> None:
    """KG hygiene: the search_and_execute path also populates skb_relations."""
    winning = {"v": 42}
    await memory.put_rule(
        Rule(
            id="rule_search_y",
            trigger={"actor": "y"},
            strategy={
                "kind": "search_alternatives",
                "candidate_params": [{"v": 1}, {"v": 2}, winning],
                "iterations": 8,
            },
            source_peer="p",
            created_at_ns=time.time_ns(),
        )
    )
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_ParamMatchingActor("y", winning_params=winning))

    await gen.run_task(Task(id="t_kg", intent="y", actor="y"))

    relations = await memory.query_relations(src_id="task:t_kg")
    assert len(relations) == 1
    assert relations[0].kind == "invoked"
    assert relations[0].attrs["status"] == "OK"


async def test_generator_mcts_returns_blocked_when_all_candidates_fail(memory) -> None:
    """No winning candidate: Generator returns BLOCKED_BY_GUARDRAIL with the
    last diagnostic from the MCTS search."""
    await memory.put_rule(
        Rule(
            id="rule_search_dead",
            trigger={"actor": "dead"},
            strategy={
                "kind": "search_alternatives",
                "candidate_params": [{"a": 1}, {"a": 2}, {"a": 3}],
                "iterations": 4,
            },
            source_peer="p",
            created_at_ns=time.time_ns(),
        )
    )
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_ParamMatchingActor("dead", winning_params={"impossible": True}))

    res = await gen.run_task(Task(id="t_dead", intent="dead", actor="dead"))

    assert res.status is TaskStatus.BLOCKED_BY_GUARDRAIL
    assert res.rule_applied == "rule_search_dead"
    assert len(res.diagnostics) >= 1
    assert res.diagnostics[0].failure_mode.value == "timeout"
