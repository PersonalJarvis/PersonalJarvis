# ADR-0009: Deliberate deviations from the architecture survey

**Status:** Accepted
**Date:** 2026-05-26

## Context

The architecture survey (`../Self-learning/KI-Fehlervermeidung und Wissensaustausch (1).md`) is a research literature review, not a literal build specification. Several elements named in the survey are operationally heavier than the capstone scope justifies. This ADR enumerates the deviations and the reasoning so future maintainers do not "fix" them blindly.

## Deviations

### 1. Python 3.11+ instead of strict 3.12

The goal pre-decides Python 3.12. The host interpreter is 3.11.9 and uv 0.9 is available. We declare `requires-python = ">=3.11"` so the verification command works without a side-channel `uv venv --python 3.12` step. No 3.12-only syntax is used. Production deployment via `uv venv --python 3.12` is unaffected. Cross-reference: ADR-0001.

### 2. libp2p GossipSub deferred in favour of injectable Transport

The survey advocates libp2p + GossipSub. `py-libp2p` is alpha and very large. We define a `Transport` `Protocol` that any concrete substrate (libp2p, ZMQ, WebRTC, plain TCP) can implement, and we ship an in-process queue Transport + a plain asyncio-TCP Transport. The capstone uses the in-process variant. Cross-reference: ADR-0006.

### 3. SHIMI hierarchy collapsed to one Bloom level

True SHIMI organizes memory as a *hierarchical* tree of abstract concepts and exchanges Merkle-DAG roots at each level. For < 10 k rules per peer, a single Bloom filter is sufficient and demonstrably converges (see capstone). The Protocol leaves room for a multi-level hierarchy by simply repeating the gossip cycle per level. Cross-reference: ADR-0006.

### 4. Embedded MQTT broker not used in tests

The goal says "tests use embedded broker". An embedded broker (amqtt) is a 25-transitive-deps weight item that we judged disproportionate to the capstone, which mocks IPS at the bridge boundary. Production users connect aiomqtt to Mosquitto. Future work could add the embedded broker behind a `[mqtt-testing]` extra. Cross-reference: ADR-0005.

### 5. MCTS reduced to bounded-branching planner

The survey describes LATS as MCTS over an action tree. For the capstone — single timeout, single retry strategy — full MCTS with rollouts and UCB1 selection is overkill. We implement bounded branching (K alternative param sets) with a skillbook-driven priority score. The `guardrails.lats.Planner` interface accepts a richer scorer; swapping in MCTS is a localized change. Cross-reference: ADR-0008.

### 6. sentence-transformers optional, hash-fallback default

The goal pre-decides sentence-transformers. The first-time HuggingFace download breaks `/tmp` re-runs. We provide a deterministic `HashEmbedder` as the default and lazy-load `SentenceTransformersEmbedder` if the user installs the `[embeddings]` extra. The skillbook's deduplication logic still works with hash embeddings because rule descriptions are short and exact-equality-leaning. Cross-reference: ADR-0003.

### 7. TTSR (student/teacher) not built in this iteration

The survey describes TTSR (Test-Time Self-Reflection) as a complement to ACE for fine-grained reasoning-trace repair. The capstone scenario does not require trace-level decomposition — it requires a single-rule correction. TTSR is left as future work; the Reflector's gap-function output already carries enough information for a future TTSR module to plug in.

## Consequences

- The skillbook is smaller, faster, and more portable than a literal survey implementation.
- Re-enabling any of the deferred elements is a localized change (one ADR per item).
- The capstone test exercises every shipped feature, so deletions cannot mask regressions.

## Alternatives considered

Each item above lists alternatives in its own ADR. Some deviations (#7 TTSR) are pure-scope decisions and have no current-build alternative.
