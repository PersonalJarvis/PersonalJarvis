"""SQLiteMemoryStore contract: round-trip Rules and Traces with schema-isolation."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from skillbook.memory_layer.models import Rule, TraceStep
from skillbook.memory_layer.store import SQLiteMemoryStore


@pytest.fixture
async def store(tmp_path: Path):
    s = SQLiteMemoryStore(db_path=tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


async def test_rule_round_trip_preserves_trigger_and_strategy(store: SQLiteMemoryStore) -> None:
    rule = Rule(
        id="rule_001",
        trigger={"actor": "magic_home_controller"},
        strategy={"kind": "retry_with_delay", "delay_s": 3, "max_retries": 2},
        source_peer="peer_a",
        created_at_ns=time.time_ns(),
    )
    await store.put_rule(rule)
    rules = await store.query_rules()
    assert len(rules) == 1
    assert rules[0].id == "rule_001"
    assert rules[0].trigger == {"actor": "magic_home_controller"}
    assert rules[0].strategy["kind"] == "retry_with_delay"
    assert rules[0].strategy["delay_s"] == 3


async def test_rule_can_be_tombstoned(store: SQLiteMemoryStore) -> None:
    rule = Rule(
        id="rule_002",
        trigger={"actor": "x"},
        strategy={"kind": "skip"},
        source_peer="p",
        created_at_ns=time.time_ns(),
    )
    await store.put_rule(rule)
    await store.tombstone_rule("rule_002")
    active = await store.query_rules()
    assert active == []
    all_rules = await store.query_rules(include_tombstones=True)
    assert len(all_rules) == 1
    assert all_rules[0].deleted is True


async def test_trace_step_round_trip(store: SQLiteMemoryStore) -> None:
    step = TraceStep(
        task_id="task_42",
        step_idx=0,
        actor="magic_home_controller",
        params={"intensity": 0.7},
        result={"error": "timeout"},
        status="TIMEOUT",
        ts_ns=time.time_ns(),
    )
    await store.put_trace_step(step)
    steps = await store.query_trace_steps(task_id="task_42")
    assert len(steps) == 1
    assert steps[0].actor == "magic_home_controller"
    assert steps[0].status == "TIMEOUT"


async def test_schema_uses_skb_prefix_only(store: SQLiteMemoryStore) -> None:
    """DoD-4: every persisted table is namespaced with skb_ prefix."""
    tables = await store.list_tables()
    assert tables, "schema must contain at least one table"
    for t in tables:
        assert t.startswith("skb_"), f"table {t!r} violates DoD-4 schema-isolation"


async def test_query_rules_filters_by_actor_trigger(store: SQLiteMemoryStore) -> None:
    await store.put_rule(Rule(
        id="r1", trigger={"actor": "a"}, strategy={"kind": "skip"},
        source_peer="p", created_at_ns=1,
    ))
    await store.put_rule(Rule(
        id="r2", trigger={"actor": "b"}, strategy={"kind": "skip"},
        source_peer="p", created_at_ns=2,
    ))
    only_a = await store.query_rules(actor="a")
    assert {r.id for r in only_a} == {"r1"}
