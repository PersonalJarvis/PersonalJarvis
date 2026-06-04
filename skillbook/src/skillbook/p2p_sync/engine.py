"""SyncEngine: CRDT-correct rule exchange over a Transport with Bloom anti-entropy.

Protocol v2 (current): two-phase Bloom-assisted exchange.

  1. ``sync_once()`` on peer A gossips an ``OFFER`` envelope containing
     A's full rule set plus a Bloom filter over A's rule ids.
  2. Peer B receives the OFFER, CRDT-merges every rule in the payload into
     its local store, computes ``exclusive = {r in B.rules : r.id not in
     A.bloom}``, and gossips back a ``RESPONSE`` envelope containing those
     exclusive rules plus B's own Bloom filter.
  3. Peer A receives the RESPONSE and CRDT-merges the rules. Type==RESPONSE
     does NOT trigger a third message, so the exchange terminates in two
     hops regardless of network topology.

Closes the FORENSICS Q3 wiring gap noted as "Bloom code exists but
engine.py never imports it".
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from skillbook.memory_layer.models import Rule
from skillbook.memory_layer.store import MemoryStore

from .bloom import BloomFilter
from .crdt import crdt_merge
from .transport import Transport

_PROTOCOL_VERSION = 2
_BLOOM_M = 8192
_BLOOM_K = 4


class _EnvelopeType(StrEnum):
    OFFER = "offer"
    RESPONSE = "response"


@dataclass(slots=True)
class SyncEngine:
    memory: MemoryStore
    transport: Transport
    peer_id: str
    _started: bool = field(default=False, init=False)

    async def start(self) -> None:
        if self._started:
            return
        self.transport.subscribe(self._on_message)
        self._started = True

    async def sync_once(self) -> None:
        rules = await self.memory.query_rules(include_tombstones=True)
        envelope = self._build_envelope(rules, _EnvelopeType.OFFER, include_all_rules=True)
        await self.transport.gossip(json.dumps(envelope).encode("utf-8"))

    async def _on_message(self, payload: bytes) -> None:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        if data.get("v") != _PROTOCOL_VERSION:
            return
        if data.get("peer") == self.peer_id:
            return

        for raw in data.get("rules", []):
            try:
                remote = Rule.model_validate(raw)
            except Exception:
                continue
            local = await self._fetch_rule(remote.id)
            merged = crdt_merge(local, remote)
            await self.memory.put_rule(merged)

        if data.get("type") != _EnvelopeType.OFFER.value:
            return

        peer_bloom = self._extract_bloom(data)
        if peer_bloom is None:
            return

        my_rules = await self.memory.query_rules(include_tombstones=True)
        exclusive = [r for r in my_rules if r.id not in peer_bloom]
        response = self._build_envelope(
            exclusive,
            _EnvelopeType.RESPONSE,
            include_all_rules=False,
            all_rules_for_bloom=my_rules,
        )
        await self.transport.gossip(json.dumps(response).encode("utf-8"))

    def _build_envelope(
        self,
        rules_in_payload: list[Rule],
        env_type: _EnvelopeType,
        *,
        include_all_rules: bool,
        all_rules_for_bloom: list[Rule] | None = None,
    ) -> dict[str, Any]:
        rules_for_bloom = (
            rules_in_payload if include_all_rules else (all_rules_for_bloom or [])
        )
        bloom = BloomFilter.from_items(
            (r.id for r in rules_for_bloom), m=_BLOOM_M, k=_BLOOM_K
        )
        bloom_b64 = base64.b64encode(bloom.serialize()).decode("ascii")
        return {
            "v": _PROTOCOL_VERSION,
            "peer": self.peer_id,
            "type": env_type.value,
            "bloom_b64": bloom_b64,
            "rules": [r.model_dump(mode="json") for r in rules_in_payload],
        }

    @staticmethod
    def _extract_bloom(data: dict[str, Any]) -> BloomFilter | None:
        b64 = data.get("bloom_b64")
        if not isinstance(b64, str):
            return None
        try:
            return BloomFilter.deserialize(base64.b64decode(b64.encode("ascii")))
        except (ValueError, Exception):
            return None

    async def _fetch_rule(self, rule_id: str) -> Rule | None:
        rules = await self.memory.query_rules(include_tombstones=True)
        for r in rules:
            if r.id == rule_id:
                return r
        return None
