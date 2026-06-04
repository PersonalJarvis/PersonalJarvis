"""SyncEngine v2: Bloom-assisted anti-entropy semantics."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from skillbook.memory_layer.models import Rule
from skillbook.memory_layer.store import SQLiteMemoryStore
from skillbook.p2p_sync.bloom import BloomFilter
from skillbook.p2p_sync.engine import SyncEngine
from tests.fakes.transport import InProcessTransport


def _rule(id_: str, *, actor: str = "x") -> Rule:
    return Rule(
        id=id_,
        trigger={"actor": actor},
        strategy={"kind": "retry_with_delay", "delay_s": 3},
        source_peer="p",
        created_at_ns=time.time_ns(),
    )


@pytest.fixture
async def two_memories(tmp_path: Path):
    a = SQLiteMemoryStore(db_path=tmp_path / "a.db")
    b = SQLiteMemoryStore(db_path=tmp_path / "b.db")
    await a.open()
    await b.open()
    yield a, b
    await a.close()
    await b.close()


async def test_offer_envelope_carries_bloom_b64(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()
    captured: list[bytes] = []

    async def sniff(payload: bytes) -> None:
        captured.append(payload)

    t_b.subscribe(sniff)

    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    await eng_a.start()

    await mem_a.put_rule(_rule("rule_42"))
    await eng_a.sync_once()

    assert captured
    env = json.loads(captured[0].decode())
    assert env["v"] == 2
    assert env["type"] == "offer"
    assert "bloom_b64" in env
    bloom = BloomFilter.deserialize(base64.b64decode(env["bloom_b64"]))
    assert "rule_42" in bloom
    assert "rule_999" not in bloom


async def test_response_does_not_trigger_third_message(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()

    count_a, count_b = [0], [0]
    orig_a_gossip = t_a.gossip
    orig_b_gossip = t_b.gossip

    async def count_a_gossip(p: bytes) -> None:
        count_a[0] += 1
        await orig_a_gossip(p)

    async def count_b_gossip(p: bytes) -> None:
        count_b[0] += 1
        await orig_b_gossip(p)

    t_a.gossip = count_a_gossip
    t_b.gossip = count_b_gossip

    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    eng_b = SyncEngine(memory=mem_b, transport=t_b, peer_id="B")
    await eng_a.start()
    await eng_b.start()

    await mem_a.put_rule(_rule("r1"))
    await mem_b.put_rule(_rule("r2"))

    await eng_a.sync_once()

    assert count_a[0] == 1
    assert count_b[0] == 1


async def test_response_carries_exclusive_rules_only(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()

    await mem_a.put_rule(_rule("shared"))
    await mem_b.put_rule(_rule("shared"))
    await mem_b.put_rule(_rule("only_on_b"))

    captured: list[dict] = []

    async def sniff(payload: bytes) -> None:
        captured.append(json.loads(payload.decode()))

    t_a.subscribe(sniff)

    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    eng_b = SyncEngine(memory=mem_b, transport=t_b, peer_id="B")
    await eng_a.start()
    await eng_b.start()
    await eng_a.sync_once()

    responses = [e for e in captured if e.get("type") == "response"]
    assert len(responses) == 1
    resp = responses[0]
    rule_ids = {r["id"] for r in resp["rules"]}
    assert rule_ids == {"only_on_b"}


async def test_convergence_after_single_sync_once(two_memories) -> None:
    mem_a, mem_b = two_memories
    t_a, t_b = InProcessTransport.pair()
    eng_a = SyncEngine(memory=mem_a, transport=t_a, peer_id="A")
    eng_b = SyncEngine(memory=mem_b, transport=t_b, peer_id="B")
    await eng_a.start()
    await eng_b.start()

    await mem_a.put_rule(_rule("a_only"))
    await mem_b.put_rule(_rule("b_only"))
    await eng_a.sync_once()

    a_ids = {r.id for r in await mem_a.query_rules()}
    b_ids = {r.id for r in await mem_b.query_rules()}
    assert a_ids == {"a_only", "b_only"}
    assert b_ids == {"a_only", "b_only"}
