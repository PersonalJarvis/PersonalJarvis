"""Generator knowledge-graph writes: entities + relations per task interaction."""

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
        self._failures = failures_until_ok

    async def call(self, params: dict) -> dict:
        if self._failures > 0:
            self._failures -= 1
            raise TimeoutError(f"{self.name} mock timeout")
        return {"echo": params}


async def _zero_sleep(_: float) -> None:
    return None


@pytest.fixture
async def memory(tmp_path: Path):
    s = SQLiteMemoryStore(db_path=tmp_path / "kg.db")
    await s.open()
    yield s
    await s.close()


async def test_successful_run_writes_actor_and_task_entities(memory) -> None:
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("magic_home_controller"))

    await gen.run_task(Task(id="task_001", intent="trigger_scene", actor="magic_home_controller"))

    actor_entities = await memory.query_entities(kind="actor")
    task_entities = await memory.query_entities(kind="task")

    assert any(e.id == "actor:magic_home_controller" for e in actor_entities)
    assert any(e.id == "task:task_001" for e in task_entities)
    task_e = next(e for e in task_entities if e.id == "task:task_001")
    assert task_e.attrs["intent"] == "trigger_scene"
    assert task_e.attrs["actor"] == "magic_home_controller"


async def test_successful_run_writes_invoked_relation(memory) -> None:
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("act_a"))

    await gen.run_task(Task(id="t_ok", intent="x", actor="act_a"))

    relations = await memory.query_relations(src_id="task:t_ok")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "invoked"
    assert rel.src_id == "task:t_ok"
    assert rel.dst_id == "actor:act_a"
    assert rel.attrs["step_idx"] == 0
    assert rel.attrs["status"] == "OK"


async def test_failed_attempt_writes_invoked_failed_relation_with_failure_mode(memory) -> None:
    """Capstone-style: actor fails first try; we want the KG to show invoked_failed."""
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("flaky", failures_until_ok=99))  # always fails

    await gen.run_task(Task(id="t_fail", intent="x", actor="flaky"))

    relations = await memory.query_relations(src_id="task:t_fail")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "invoked_failed"
    assert rel.attrs["status"] == "BLOCKED_BY_GUARDRAIL"
    assert rel.attrs["failure_mode"] == FailureMode.TIMEOUT.value
    assert "flaky" in rel.attrs["evidence"]


async def test_retry_run_writes_multiple_relations_per_task(memory) -> None:
    """With a retry rule, an actor that fails-then-succeeds yields 2 relations."""
    await memory.put_rule(
        Rule(
            id="rule_retry_x",
            trigger={"actor": "x"},
            strategy={"kind": "retry_with_delay", "delay_s": 0, "max_retries": 2},
            source_peer="p",
            created_at_ns=time.time_ns(),
        )
    )
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("x", failures_until_ok=1))

    await gen.run_task(Task(id="t_retry", intent="x", actor="x"))

    relations = await memory.query_relations(src_id="task:t_retry")
    assert len(relations) == 2
    kinds = [r.kind for r in relations]
    assert kinds == ["invoked_failed", "invoked"]


async def test_actor_entity_upsert_idempotent_across_runs(memory) -> None:
    """Multiple tasks against the same actor should NOT spawn duplicate actor entities."""
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("shared"))

    await gen.run_task(Task(id="t1", intent="x", actor="shared"))
    await gen.run_task(Task(id="t2", intent="x", actor="shared"))
    await gen.run_task(Task(id="t3", intent="x", actor="shared"))

    actor_entities = await memory.query_entities(kind="actor")
    assert len([e for e in actor_entities if e.id == "actor:shared"]) == 1


async def test_capstone_no_longer_leaves_kg_tables_empty(memory) -> None:
    """FORENSICS regression: after a real task run, neither table is at zero rows."""
    gen = Generator(memory=memory, engine=LATSEngine(dog=AgentDoG()), sleep_fn=_zero_sleep)
    gen.register_actor(_StubActor("controller"))

    await gen.run_task(Task(id="capstone_like", intent="run", actor="controller"))

    entities = await memory.query_entities()
    relations = await memory.query_relations()
    assert len(entities) >= 2, "KG entities must be populated, was 0 in FORENSICS"
    assert len(relations) >= 1, "KG relations must be populated, was 0 in FORENSICS"
