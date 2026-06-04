"""crdt_merge pure-function semantics: idempotent, commutative, tombstone-wins."""

from __future__ import annotations

import pytest

from skillbook.memory_layer.models import Rule
from skillbook.p2p_sync.crdt import crdt_merge


def _rule(id_: str, *, deleted: bool = False, priority: int = 0, ts: int = 1) -> Rule:
    return Rule(
        id=id_,
        trigger={"actor": "x"},
        strategy={"kind": "retry_with_delay"},
        source_peer="p",
        created_at_ns=ts,
        priority=priority,
        deleted=deleted,
    )


def test_merge_with_no_local_returns_remote() -> None:
    r = _rule("a")
    assert crdt_merge(None, r) == r


def test_merge_identical_is_idempotent() -> None:
    r = _rule("a")
    out = crdt_merge(r, r)
    assert out.deleted is False
    assert out.id == "a"


def test_merge_tombstone_wins_either_direction() -> None:
    alive = _rule("a", deleted=False)
    dead = _rule("a", deleted=True)
    assert crdt_merge(alive, dead).deleted is True
    assert crdt_merge(dead, alive).deleted is True


def test_merge_rejects_different_ids() -> None:
    with pytest.raises(ValueError):
        crdt_merge(_rule("a"), _rule("b"))


def test_merge_is_commutative_for_same_state() -> None:
    a = _rule("a", priority=3, ts=5)
    b = _rule("a", priority=3, ts=5)
    out1 = crdt_merge(a, b)
    out2 = crdt_merge(b, a)
    assert out1.model_dump() == out2.model_dump()
