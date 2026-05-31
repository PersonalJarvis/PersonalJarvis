"""Curator: delta-update skillbook (ADR-0009 anti-context-collapse design).

The Curator never rewrites the skillbook wholesale. Each Verdict produces at
most one Rule insertion, and existing rules are kept verbatim. Deduplication
is computed against embeddings of the (trigger + strategy) text — under
HashEmbedder this approximates exact-text dedup, under
SentenceTransformersEmbedder this becomes true semantic dedup.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

import numpy as np

from skillbook.memory_layer.embedder import Embedder
from skillbook.memory_layer.models import Rule
from skillbook.memory_layer.store import MemoryStore

from .models import Verdict


@dataclass(slots=True)
class Curator:
    memory: MemoryStore
    embedder: Embedder
    peer_id: str
    similarity_threshold: float = 0.99

    async def curate(self, verdict: Verdict) -> Rule | None:
        if verdict.outcome != "failure" or verdict.rule is None:
            return None

        proposed = self._build_rule(verdict)
        proposed_vec = self._embed_rule(proposed)

        actor = proposed.trigger.get("actor")
        candidates = await self.memory.query_rules(actor=actor) if actor else await self.memory.query_rules()

        for existing in candidates:
            existing_vec = self._embed_rule(existing)
            cosine = float(proposed_vec @ existing_vec)
            if cosine >= self.similarity_threshold:
                return None

        await self.memory.put_rule(proposed)
        return proposed

    def _build_rule(self, verdict: Verdict) -> Rule:
        assert verdict.rule is not None  # narrowed by curate()
        return Rule(
            id=f"rule_{uuid.uuid4().hex}",
            trigger=verdict.rule["trigger"],
            strategy=verdict.rule["strategy"],
            source_peer=self.peer_id,
            created_at_ns=time.time_ns(),
            evidence=verdict.evidence,
        )

    def _embed_rule(self, rule: Rule) -> np.ndarray:
        canonical = json.dumps(
            {"trigger": rule.trigger, "strategy": rule.strategy},
            sort_keys=True,
            separators=(",", ":"),
        )
        return self.embedder.embed(canonical)
