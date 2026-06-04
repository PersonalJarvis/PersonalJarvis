# ADR-0006: P2P sync — OR-Set CRDT over an injectable Transport (libp2p deferred)

**Status:** Accepted
**Date:** 2026-05-26

## Context

The survey advocates **libp2p GossipSub** with **CRDT deltas** and **SHIMI** (Semantic Hierarchical Memory Index) for Merkle-DAG + Bloom-filter-assisted set reconciliation between peers. libp2p in Python (`py-libp2p`) is research-grade and brings significant operational weight. The hard requirement is: "syncs via P2P to simulated second instance" — the capstone simulates the second peer in-process.

## Decision

- Define a `Transport` `Protocol` with two methods: `gossip(payload: bytes) -> None` and `subscribe(handler: Callable[[bytes], Awaitable[None]]) -> None`. This abstracts libp2p, websockets, ZeroMQ, or in-process queues uniformly.
- Ship two implementations:
  1. **`InProcessTransport`** — pair of asyncio.Queues, used in tests and in single-host multi-instance deployments. This is what the capstone uses.
  2. **`TcpTransport`** — plain asyncio TCP with length-prefixed framing, for two-host smoke tests. Not on the capstone critical path; included for completeness.
- The **CRDT layer** is a custom **OR-Set** (Observed-Remove Set) on rule IDs. Each rule carries a unique tag (uuid4 + timestamp). Delete is a tombstone insert. Merge: union of adds, subtract tombstones. This is the textbook design — see Shapiro et al. "Conflict-Free Replicated Data Types" (2011). Provably converges; merge is associative, commutative, idempotent.
- **Set reconciliation** uses a Bloom filter (m=8192 bits, k=4 hashes — sized for a few thousand rules with low FPR). Peers exchange Bloom filters; missing items are requested explicitly. This is the SHIMI design boiled down to one level — see ADR-0009 for the deferred hierarchical version.
- **No** libp2p dependency. The `Transport` Protocol means it can be added later without touching `p2p_sync.crdt` or `p2p_sync.bloom`.

## Consequences

- No native dependencies; the capstone runs in `python:3.11-slim` with no audio/no GUI/no extras.
- The convergence property is testable in isolation: random delta sequences applied in random orders to N peers must converge to identical state.
- Bloom-filter false positives mean a peer occasionally misses a rule it should have — the next gossip cycle catches it. Tests verify eventual consistency, not first-round delivery.

## Alternatives considered

- **py-libp2p**: large, alpha-quality, not pip-friendly on Windows. Rejected for now.
- **Raft/Paxos**: strong consistency overkill for an eventually-consistent skillbook; CRDT is the right shape per the survey.
- **Centralized gossip server**: violates the survey's "no central server" mandate.
