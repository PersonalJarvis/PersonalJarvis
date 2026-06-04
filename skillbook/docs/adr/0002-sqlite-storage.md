# ADR-0002: SQLite as the single storage primitive

**Status:** Accepted
**Date:** 2026-05-26

## Context

The architecture survey discusses Mem0, Letta, Zep, Graphiti — frameworks built on diverse backends (Postgres, Neo4j, Pinecone, Qdrant). The goal pre-decides: "Storage: SQLite under skillbook/data/, no DB servers". This excludes graph databases, vector databases, and message brokers as storage layers.

## Decision

Use **a single SQLite file per agent instance** under `skillbook/data/`. All persistent state — temporal facts, knowledge-graph nodes/edges, skillbook rules, P2P sync metadata — lives in this one file under tables prefixed `skb_` to maintain the schema-isolation hard constraint from the goal.

Schema:

- `skb_entities(id, kind, attrs_json, created_at_ns, valid_from_ns, valid_to_ns NULL)` — knowledge-graph nodes with bi-temporal validity.
- `skb_relations(id, src_id, dst_id, kind, attrs_json, valid_from_ns, valid_to_ns NULL)` — directed edges with the same temporal model.
- `skb_rules(id, trigger_json, strategy_json, embedding_blob, version, source_peer, deleted INT NOT NULL DEFAULT 0, created_at_ns)` — skillbook deltas. OR-Set semantics: `id` is the unique tag (timestamp-uuid), `deleted` is the tombstone for CRDT semantics (see ADR-0006).
- `skb_traces(id, task_id, step_idx, actor, params_json, result_json, status, ts_ns)` — Generator execution trace; consumed by the Reflector.
- `skb_meta(key TEXT PRIMARY KEY, value TEXT)` — instance UUID, schema version, peer registry pointer.

Vector storage: embedding bytes stored as `BLOB` (numpy `float32` array); semantic search done in-process by computing cosine vs all rows for the small N expected in agent skillbooks (< 10k rules). When N grows, sqlite-vec (an FTS-style vector extension) can be added behind the same `MemoryStore` protocol — see ADR-0009 for the deferred-optimization note.

## Consequences

- Zero external services. Tests boot in milliseconds.
- Each test gets its own temp DB; isolation is trivial.
- Cross-instance "shared state" is *only* via the P2P sync delta protocol, never via a shared DB. This matches the "no central server" decentralization mandate of the survey.

## Alternatives considered

- **DuckDB + vector extension**: more performant for analytics but adds a heavy native dependency; SQLite ships with Python stdlib.
- **Neo4j embedded**: closer match to the knowledge-graph framing but JVM dependency forbidden by the goal's "no DB servers".
- **Plain JSON files**: too easy to corrupt under concurrent writes; SQLite's transactional guarantees prevent CRDT delta loss.
