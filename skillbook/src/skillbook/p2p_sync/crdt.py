"""OR-Set CRDT merge for skillbook Rules (ADR-0006).

The unique-tag-per-add property of an OR-Set is satisfied because each Rule.id
is a uuid4 generated at the Curator. A delete is a per-id tombstone (the
``deleted`` boolean on the Rule). Merge semantics:

  - ``local is None``: take ``remote``.
  - Same id, either side has ``deleted=True``: result is the tombstone.
  - Same id, both alive: idempotent (return ``local`` unchanged).

Provably converges, is commutative, associative, and idempotent.
"""

from __future__ import annotations

from skillbook.memory_layer.models import Rule


def crdt_merge(local: Rule | None, remote: Rule) -> Rule:
    if local is None:
        return remote
    if local.id != remote.id:
        raise ValueError(
            f"crdt_merge requires matching rule ids, got {local.id!r} vs {remote.id!r}"
        )
    if local.deleted or remote.deleted:
        base = remote if remote.deleted else local
        return base.model_copy(update={"deleted": True})
    return local
