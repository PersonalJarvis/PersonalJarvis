# Skillbook

Recursive, decentralized learning + memory layer for AI agents. Implements the **Agentic Context Engine (ACE)** loop with diagnostic guardrails (AgentDoG + LATS) and CRDT-based peer-to-peer sync between instances. Design source: `../Self-learning/KI-Fehlervermeidung und Wissensaustausch (1).md` (treated as research survey, not literal spec — engineering decisions live in `docs/adr/`).

## Hard Constraints

1. **All code lives under `skillbook/`.** Anything outside this directory is read-only — including `wiki/obsidian-vault/`, which is fully off-limits.
2. **Memory-layer storage is schema-isolated.** The skillbook database file and all tables/keys are namespaced and never reuse a user-data schema.
3. **No production stubs, TODOs, NotImplementedError, or "fix later" placeholders** in any path the capstone test exercises. Test mocks are allowed and expected.

## Module Boundaries

```
┌──────────────────────────────────────────────────────────────────┐
│ tests/test_capstone.py     (end-to-end ACE loop oracle)          │
└──────────────────────────────────────────────────────────────────┘
                                 │
   ┌─────────────────────────────┼─────────────────────────────┐
   ▼                             ▼                             ▼
┌──────────────┐         ┌──────────────────┐         ┌──────────────────┐
│ p2p_sync     │         │ ace_core         │         │ symcon_bridge    │
│ (CRDT delta) │◄────────│ Generator        │◄────────│ MQTT + JSON-RPC  │
└──────────────┘         │ Reflector(REPL)  │         └──────────────────┘
                         │ Curator          │
                         └────────┬─────────┘
                                  │
                  ┌───────────────┴──────────────┐
                  ▼                              ▼
           ┌──────────────┐              ┌──────────────────┐
           │ guardrails   │              │ memory_layer     │
           │ AgentDoG     │              │ Temporal KG +    │
           │ LATS         │              │ Skillbook store  │
           └──────────────┘              └──────────────────┘
```

**Dependency direction (strict, enforced by package layout):**

- `memory_layer` depends on stdlib + numpy only.
- `guardrails` depends on `memory_layer` (reads rules) and stdlib.
- `ace_core` depends on `memory_layer` + `guardrails` + stdlib.
- `symcon_bridge` depends on stdlib (and optionally `aiomqtt` for real broker use).
- `p2p_sync` depends on `memory_layer` (reads/writes deltas) + stdlib.
- Tests live in `tests/`; production modules never import from `tests/`.
- Circular imports are a build failure.

## Interface Types

Public surface uses `Protocol` (PEP 544) for swap-ability and `pydantic.BaseModel` for wire-format data. Internal helpers use `@dataclass(slots=True, frozen=True)`. No abstract base classes (`ABC`) unless inheritance is required.

Key interfaces:

- `MemoryStore` — `Protocol`, async; `put_fact`, `query_facts`, `put_rule`, `query_rules`, `delete_rule`.
- `Embedder` — `Protocol`, sync; `embed(text: str) -> np.ndarray`.
- `Actor` — `Protocol`, async; `call(name, params, timeout) -> ActorResult`. Mocked in tests.
- `LLM` — `Protocol`, async; `complete(prompt) -> str`. Deterministic mock when `ANTHROPIC_API_KEY` is unset.
- `Transport` — `Protocol`, async; `gossip(peer_id, payload)`, `subscribe(handler)`. Used by `p2p_sync`.

Concrete data models live in `skillbook.{module}.models` modules (pydantic v2).

## Tech-Stack Choices (one ADR per choice)

| Concern | Choice | ADR |
|---|---|---|
| Python runtime | 3.11+ (3.12 preferred, uv-managed) | ADR-0001 |
| Dependency manager | uv | ADR-0001 |
| Repo layout | `src/skillbook/{module}/`, `tests/`, `data/` | ADR-0001 |
| Persistent storage | SQLite under `data/`, file-per-instance | ADR-0002 |
| Embeddings | sentence-transformers if installed + cached, deterministic hash fallback | ADR-0003 |
| LLM provider | Anthropic SDK if `ANTHROPIC_API_KEY` present, deterministic mock otherwise | ADR-0004 |
| MQTT stack | `aiomqtt` for production; tests use injected mock bridge | ADR-0005 |
| P2P transport | OR-Set CRDT over async Transport protocol; default in-process queue, asyncio-TCP available | ADR-0006 |
| Reflector sandbox | Subprocess REPL with restricted globals + result-via-stdout JSON | ADR-0007 |
| Guardrails taxonomy | Enum-driven AgentDoG (Source/FailureMode/Consequence) | ADR-0008 |
| Survey deviations | See ADR-0009 | ADR-0009 |

## Repo Layout

```
skillbook/
├── CLAUDE.md                      ← this file (binding)
├── CHANGELOG.md                   ← per-commit log
├── pyproject.toml                 ← uv-managed deps + pytest config
├── conftest.py                    ← --seeds option, src on path, shared fixtures
├── README.md                      ← short orientation
├── docs/
│   └── adr/                       ← architecture decision records
│       ├── 0001-repo-layout-and-toolchain.md
│       ├── 0002-sqlite-storage.md
│       ├── 0003-embeddings-strategy.md
│       ├── 0004-llm-provider-strategy.md
│       ├── 0005-mqtt-stack.md
│       ├── 0006-p2p-transport.md
│       ├── 0007-reflector-sandbox.md
│       ├── 0008-guardrails-taxonomy.md
│       └── 0009-survey-deviations.md
├── src/
│   └── skillbook/
│       ├── __init__.py
│       ├── ace_core/              ← Generator, RecursiveReflector, Curator
│       ├── symcon_bridge/         ← async MQTT + JSON-RPC client
│       ├── memory_layer/          ← temporal facts + KG + skillbook persistence
│       ├── guardrails/            ← AgentDoG + LATS
│       └── p2p_sync/              ← CRDT OR-Set + Merkle-tree gossip
├── tests/
│   ├── unit/                      ← module-level unit tests
│   ├── integration/               ← cross-module
│   └── test_capstone.py           ← end-to-end ACE loop oracle
└── data/                          ← per-instance SQLite files (gitignored)
```

## Test Pyramid

- **Unit tests** (`tests/unit/{module}/`): one behavior per file, mock at module boundary.
- **Integration tests** (`tests/integration/`): two-module flows (e.g. Reflector → Curator → memory).
- **Capstone** (`tests/test_capstone.py`): full 7-step scenario from the goal.
- Every test is deterministic under `--seeds=N`. A `seed` fixture parametrizes over `range(N)`. Tests that consume `seed` run N times; tests that don't run once. Failure under any seed = failure.

## Definition of Done

All five must be demonstrably true in code (referenced by line number) before this skillbook is "done":

1. **DoD-1 (capstone passes under stress):** `pytest skillbook/ -x -q --seeds=5` from the repo root exits 0 with no skip/xfail/patch-around. Verified by re-run in a clean `/tmp` checkout.
2. **DoD-2 (no production stubs):** `grep -rn "TODO\|FIXME\|NotImplementedError" skillbook/src` returns no matches. Test files (`skillbook/tests/`) may use `pytest.skip` *only* if a real environmental precondition is unmet — none of the capstone-path tests do.
3. **DoD-3 (no escape from `skillbook/`):** Every file modified by skillbook code is under `skillbook/`. Verified by inspecting `git diff --name-only` of the skillbook branch — every path begins with `skillbook/`.
4. **DoD-4 (schema-isolated memory):** Every SQLite write goes through `memory_layer.store.MemoryStore`, which uses tables prefixed `skb_` and a database file under `skillbook/data/`. Verified by `grep -rn "CREATE TABLE" skillbook/src` showing only `skb_*` tables and `grep -rn "sqlite3.connect\|aiosqlite.connect" skillbook/src` showing only `data/` paths.
5. **DoD-5 (closed loop on both peers):** After P2P sync, peer B's skillbook contains the corrective rule emitted by peer A's Curator, and a follow-up actor call that would have timed out on the naive path completes successfully on both peers. Verified by `tests/test_capstone.py` assertions.

## Pre-existing State

The parent repository (Personal Jarvis) may carry unrelated in-progress working-tree changes. That state is **grandfathered** per the parent's CLAUDE.md doctrine: skillbook commits never touch those files, and `git status` may continue to show them as modified/deleted until the parent team commits or discards them. The "git status clean" criterion in the goal applies to the skillbook scope: no uncommitted skillbook-owned files at completion.
