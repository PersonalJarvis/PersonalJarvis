"""SyncEngine: pushes/pulls rule deltas between peer instances via Transport."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from skillbook.memory_layer.models import Rule
from skillbook.memory_layer.store import SQLiteMemoryStore
from skillbook.p2p_sync.engine import SyncEngine
from tests.fakes.transport import InProcessTransport


@pytest.fixture
async def two_memories(tmp_path: Path):
    a = SQLiteMemoryStore(db_path=tmp_path / "a.db")
    b = SQLiteMemoryStore(db_path=tmp_path / "b.db")
    await a.open()
    await b.open()
    yield a, b
    await a.close()
    await b.close()


def _rule(id_: str, *, actor: str = "x", deleted: bool = False) -> Rule:
    return Rule(
        id=id_,
        trigger={"actor": actor},
        strategy={"kind": "retry_with_delay", "delay_s": 3},
        source_peer="p",
        created_at_ns=time.time_ns(),
        deleted=deleted,
    )


async def test_sync_once_propagates_rule_from_a_to_b(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()
    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    eng_b = SyncEngine(memory=mem_b, transport=t_b, peer_id="B")
    await eng_a.start()
    await eng_b.start()

    await mem_a.put_rule(_rule("rule_001"))
    await eng_a.sync_once()

    rules_b = await mem_b.query_rules()
    assert {r.id for r in rules_b} == {"rule_001"}


async def test_sync_is_idempotent_across_multiple_cycles(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()
    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    eng_b = SyncEngine(memory=mem_b, transport=t_b, peer_id="B")
    await eng_a.start()
    await eng_b.start()

    await mem_a.put_rule(_rule("r"))
    for _ in range(5):
        await eng_a.sync_once()
        await eng_b.sync_once()

    rules_a = await mem_a.query_rules(include_tombstones=True)
    rules_b = await mem_b.query_rules(include_tombstones=True)
    assert len(rules_a) == 1
    assert len(rules_b) == 1


async def test_tombstone_propagates(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()
    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    eng_b = SyncEngine(memory=mem_b, transport=t_b, peer_id="B")
    await eng_a.start()
    await eng_b.start()

    await mem_a.put_rule(_rule("r"))
    await eng_a.sync_once()
    assert (await mem_b.query_rules())[0].id == "r"

    await mem_a.tombstone_rule("r")
    await eng_a.sync_once()
    assert await mem_b.query_rules() == []
    all_b = await mem_b.query_rules(include_tombstones=True)
    assert all_b[0].deleted is True


async def test_two_peers_with_distinct_rules_converge(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()
    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    eng_b = SyncEngine(memory=mem_b, transport=t_b, peer_id="B")
    await eng_a.start()
    await eng_b.start()

    await mem_a.put_rule(_rule("from_a", actor="alpha"))
    await mem_b.put_rule(_rule("from_b", actor="beta"))

    await eng_a.sync_once()
    await eng_b.sync_once()

    ids_a = {r.id for r in await mem_a.query_rules()}
    ids_b = {r.id for r in await mem_b.query_rules()}
    assert ids_a == ids_b == {"from_a", "from_b"}


async def test_engine_ignores_own_loopback() -> None:
    """If a SyncEngine somehow receives its own payload, it should not process it."""
    mem = SQLiteMemoryStore(db_path=":memory:")
    await mem.open()
    try:
        t_a, t_b = InProcessTransport.pair()
        eng = SyncEngine(memory=mem, transport=t_a, peer_id="A")
        await eng.start()
        # Synthesize a payload that pretends to come from A itself.
        import json
        await t_b.gossip(json.dumps({"peer": "A", "rules": [
            {"id": "intruder", "trigger": {"actor": "x"}, "strategy": {},
             "source_peer": "A", "created_at_ns": 1, "priority": 0, "deleted": False, "evidence": ""}
        ]}).encode("utf-8"))
        # The own-loopback guard should suppress this insert.
        assert await mem.query_rules() == []
    finally:
        await mem.close()
