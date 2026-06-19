"""Curator: delta-update skillbook with embedding-similarity dedup."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillbook.ace_core.curator import Curator
from skillbook.ace_core.models import Verdict
from skillbook.memory_layer.embedder import HashEmbedder
from skillbook.memory_layer.store import SQLiteMemoryStore


@pytest.fixture
async def memory(tmp_path: Path):
    s = SQLiteMemoryStore(db_path=tmp_path / "c.db")
    await s.open()
    yield s
    await s.close()


async def test_curate_inserts_rule_when_novel(memory: SQLiteMemoryStore) -> None:
    curator = Curator(memory=memory, embedder=HashEmbedder(), peer_id="p_a")
    verdict = Verdict(
        outcome="failure",
        evidence="actor 'foo' timed out",
        rule={"trigger": {"actor": "foo"}, "strategy": {"kind": "retry_with_delay", "delay_s": 3}},
    )

    rule = await curator.curate(verdict)

    assert rule is not None
    assert rule.trigger == {"actor": "foo"}
    assert rule.source_peer == "p_a"
    stored = await memory.query_rules(actor="foo")
    assert len(stored) == 1
    assert stored[0].id == rule.id


async def test_curate_skips_exact_duplicate(memory: SQLiteMemoryStore) -> None:
    curator = Curator(memory=memory, embedder=HashEmbedder(), peer_id="p_a")
    v = Verdict(
        outcome="failure",
        evidence="x",
        rule={"trigger": {"actor": "foo"}, "strategy": {"kind": "retry_with_delay", "delay_s": 3}},
    )
    first = await curator.curate(v)
    second = await curator.curate(v)

    assert first is not None
    assert second is None
    stored = await memory.query_rules(actor="foo")
    assert len(stored) == 1


async def test_curate_no_rule_means_no_insert(memory: SQLiteMemoryStore) -> None:
    curator = Curator(memory=memory, embedder=HashEmbedder(), peer_id="p_a")
    rule = await curator.curate(Verdict(outcome="no_action", evidence=""))
    assert rule is None
    assert await memory.query_rules() == []


async def test_curate_success_outcome_does_not_insert_failure_rule(memory: SQLiteMemoryStore) -> None:
    """If the verdict is 'success' there should be no failure-correction rule."""
    curator = Curator(memory=memory, embedder=HashEmbedder(), peer_id="p_a")
    v = Verdict(
        outcome="success",
        evidence="all good",
        rule={"trigger": {"actor": "foo"}, "strategy": {"kind": "retry_with_delay"}},
    )
    rule = await curator.curate(v)
    assert rule is None


async def test_curate_distinguishes_different_actor_triggers(memory: SQLiteMemoryStore) -> None:
    curator = Curator(memory=memory, embedder=HashEmbedder(), peer_id="p_a")
    await curator.curate(Verdict(
        outcome="failure", evidence="",
        rule={"trigger": {"actor": "foo"}, "strategy": {"kind": "retry_with_delay", "delay_s": 1}},
    ))
    await curator.curate(Verdict(
        outcome="failure", evidence="",
        rule={"trigger": {"actor": "bar"}, "strategy": {"kind": "retry_with_delay", "delay_s": 1}},
    ))
    all_rules = await memory.query_rules()
    assert {r.trigger["actor"] for r in all_rules} == {"foo", "bar"}
