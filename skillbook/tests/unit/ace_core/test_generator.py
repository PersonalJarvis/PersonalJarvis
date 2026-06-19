"""Generator: consults skillbook, applies retry-with-delay strategy, drives LATSEngine."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from skillbook.ace_core.generator import Generator
from skillbook.ace_core.models import Task, TaskStatus
from skillbook.guardrails.diagnostics import AgentDoG, FailureMode
from skillbook.guardrails.lats import LATSEngine
from skillbook.memory_layer.models import Rule
from skillbook.memory_layer.store import SQLiteMemoryStore


class _StubActor:
    def __init__(self, name: str, *, failures_until_ok: int = 0) -> None:
        self.name = name
        self._failures_remaining = failures_until_ok
        self.call_count = 0

    async def call(self, params: dict) -> dict:
        self.call_count += 1
        if self._failures_remaining > 0:
            self._failures_remaining -= 1
            raise TimeoutError(f"{self.name} simulated timeout #{self.call_count}")
        return {"actor": self.name, "params": params, "call": self.call_count}


async def _zero_sleep(_: float) -> None:
    return None


@pytest.fixture
async def memory(tmp_path: Path):
    s = SQLiteMemoryStore(db_path=tmp_path / "m.db")
    await s.open()
    yield s
    await s.close()


async def test_task_succeeds_first_try_returns_ok(memory: SQLiteMemoryStore) -> None:
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("ok"))

    res = await gen.run_task(Task(id="t1", intent="run", actor="ok"))

    assert res.status is TaskStatus.OK
    assert res.result is not None
    assert res.result["actor"] == "ok"


async def test_task_without_rule_fails_on_timeout(memory: SQLiteMemoryStore) -> None:
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("flaky", failures_until_ok=1))

    res = await gen.run_task(Task(id="t2", intent="run", actor="flaky"))

    assert res.status is TaskStatus.BLOCKED_BY_GUARDRAIL
    assert any(d.failure_mode is FailureMode.TIMEOUT for d in res.diagnostics)


async def test_task_with_retry_rule_eventually_succeeds(memory: SQLiteMemoryStore) -> None:
    await memory.put_rule(Rule(
        id="rule_retry_flaky",
        trigger={"actor": "flaky"},
        strategy={"kind": "retry_with_delay", "delay_s": 0, "max_retries": 2},
        source_peer="p_a",
        created_at_ns=time.time_ns(),
        priority=10,
    ))
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    actor = _StubActor("flaky", failures_until_ok=1)
    gen.register_actor(actor)

    res = await gen.run_task(Task(id="t3", intent="run", actor="flaky"))

    assert res.status is TaskStatus.OK
    assert actor.call_count == 2  # one fail, one success
    assert res.rule_applied == "rule_retry_flaky"


async def test_task_records_trace_step_per_attempt(memory: SQLiteMemoryStore) -> None:
    await memory.put_rule(Rule(
        id="rule_retry_x",
        trigger={"actor": "x"},
        strategy={"kind": "retry_with_delay", "delay_s": 0, "max_retries": 2},
        source_peer="p_a",
        created_at_ns=time.time_ns(),
    ))
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("x", failures_until_ok=1))

    res = await gen.run_task(Task(id="t4", intent="run", actor="x"))
    steps = await memory.query_trace_steps(task_id="t4")

    assert res.status is TaskStatus.OK
    assert len(steps) == 2
    assert steps[0].status == "BLOCKED_BY_GUARDRAIL"
    assert steps[1].status == "OK"


async def test_task_exhausting_retries_returns_blocked(memory: SQLiteMemoryStore) -> None:
    await memory.put_rule(Rule(
        id="rule_retry_dead",
        trigger={"actor": "dead"},
        strategy={"kind": "retry_with_delay", "delay_s": 0, "max_retries": 1},
        source_peer="p_a",
        created_at_ns=time.time_ns(),
    ))
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("dead", failures_until_ok=99))

    res = await gen.run_task(Task(id="t5", intent="run", actor="dead"))

    assert res.status is TaskStatus.BLOCKED_BY_GUARDRAIL
    assert len(res.diagnostics) == 2  # one initial attempt + one retry, both timed out
