# Skillbook Forensics

**Date:** 2026-05-26
**Mode:** observe-only. No edits, no fixes, no suggestions.
**Note:** Reconstructed 2026-05-27 from session transcript — the original
working-tree file was never committed and was lost when the parallel session
churned the branch. All observed numbers and quotes are verbatim from the
original forensic run; this is not a fresh inspection.

## 1. Real production LOC

Per-file non-comment, non-blank line counts at the time of inspection:

```
=== src/skillbook/ace_core/curator.py ===       54
=== src/skillbook/ace_core/generator.py ===    101
=== src/skillbook/ace_core/llm.py ===           53
=== src/skillbook/ace_core/models.py ===        31
=== src/skillbook/ace_core/reflector.py ===     94
=== src/skillbook/agent.py ===                 113
=== src/skillbook/errors.py ===                 27
=== src/skillbook/guardrails/diagnostics.py === 77
=== src/skillbook/guardrails/lats.py ===       103
=== src/skillbook/memory_layer/embedder.py ===  45
=== src/skillbook/memory_layer/models.py ===    48
=== src/skillbook/memory_layer/store.py ===    306
=== src/skillbook/p2p_sync/bloom.py ===         59
=== src/skillbook/p2p_sync/crdt.py ===          22
=== src/skillbook/p2p_sync/engine.py ===        61
=== src/skillbook/p2p_sync/transport.py ===     12
=== src/skillbook/symcon_bridge/actor.py ===    35
=== src/skillbook/symcon_bridge/jsonrpc.py ===  60
```

**Total: 1308 LOC** (incl. small `__init__.py` files and `conftest.py`).

## 2. Curator — what algorithm

It embeds the canonical (trigger, strategy) JSON, scans all existing rules for the same actor doing a unit-vector dot product, and either drops the new rule as a near-duplicate (cosine ≥ 0.99) or INSERTs it into SQLite with a fresh uuid.

## 3. Reflector — what trace analysis

`reflect(task_id)` reads `TraceStep` rows from SQLite (`reflector.py:50`), formats them into a prompt, and calls `self.llm.complete(prompt)` (`reflector.py:57`) to get back **a string of Python source**. That string is passed to `_run_in_sandbox` (`reflector.py:59`) which writes the trace JSON to a temp file and spawns `sys.executable -I -c <code>` as an asyncio subprocess (`reflector.py:81–91`), reading the child's stdout as a `Verdict` JSON line (`reflector.py:94–115`). In the capstone the "LLM" is `tests/fakes/llm.py:FakeLLM` returning a hardcoded ~30-line snippet that extracts the last failing trace step's actor name and emits a fixed `retry_with_delay/delay_s=3/max_retries=2` rule — so the actual "analysis" performed by the Reflector is deterministic, constant-shape rule emission with the only variable being the actor name.

## 4. LATS — confirm or deny

**Confirm.** The audit was correct at the time of inspection. `lats.py:85–104` was a breaker-open short-circuit; `lats.py:106–120` was `try: result = await call(params); except Exception: record_failure; return BLOCKED`; `lats.py:122–128` was `record_success; return OK`. No tree, no rollout, no UCB1, no MCTS, no branch selection. The class was named `LATSEngine`, the file was named `lats.py`, the docstring said "LATS-style preemptive rollback engine" — and the body was a `try`/`except` plus a per-actor `dict[str, int]` failure counter. The audit's phrasing "try/except + counter, no tree, no MCTS" was literally accurate.

(After this audit, the gap was closed by adding `src/skillbook/guardrails/mcts.py` with PlanNode + UCB1 + select/expand/backpropagate, plus `LATSEngine.search_and_execute`; see commits `4d091e9` → `847255b`.)

## 5. CRDT — does the math hold

The 22-line `crdt.py` is three `if` branches:

```python
def crdt_merge(local, remote):
    if local is None: return remote
    if local.id != remote.id: raise ValueError(...)
    if local.deleted or remote.deleted:
        base = remote if remote.deleted else local
        return base.model_copy(update={"deleted": True})
    return local
```

This is **not a CRDT in the mathematical sense**; it is an element-level resolver for one Rule identified by uuid. The OR-Set machinery (union across peers) lives in `engine.py`'s per-rule loop.

- **Commutativity:** fails for object identity; holds for content **only under the unenforced precondition that same-id rules are content-identical when alive**. The function never checks content equality.
- **Associativity:** holds. Tombstone is absorbing.
- **Idempotence:** `merge(a, a) == a`. Holds.
- **No vector clock, no LWW with timestamps, no add-tag/remove-tag set structure.** `Rule.created_at_ns` exists but `crdt_merge` does not consult it.

It's a tombstone-wins per-element resolver with optimistic preconditions, applied per-uuid by `SyncEngine`. Not a full OR-Set.

## 6. Capstone trace — what really executes

```
collected 1 item

tests/test_capstone.py::test_capstone_closed_loop[seed0]
------------------------------- live log setup --------------------------------
DEBUG    asyncio:proactor_events.py:633 Using proactor: IocpProactor
PASSED

============================== 1 passed in 0.44s ==============================
```

The only `DEBUG` line is `asyncio`'s proactor init — there are zero `logging` calls in `src/skillbook/`. Test passes silently in 440 ms.

Step-by-step REAL/FAKE labelling at the time of inspection:

| # | Step | REAL or FAKE |
|---|---|---|
| 1 | `InProcessTransport.pair()` | **FAKE** |
| 2 | `AgentInstance.build` → `SQLiteMemoryStore.open` → `sqlite3.connect(tmp_path/'a_0.db')` | **REAL** (writes to a real file) |
| 3 | Engine/Generator/Reflector/Curator/SyncEngine instantiated | **FAKE** |
| 4 | `register_actor(FakeSymconActor(...))` | **FAKE** |
| 5 | `Generator.run_task` → `memory.query_rules` SQLite SELECT | **REAL** |
| 6 | `FakeSymconActor.call({})` raises `TimeoutError` | **FAKE** |
| 7 | `AgentDoG.diagnose` returns a Diagnostic dataclass | **FAKE** |
| 8 | `memory.put_trace_step` INSERT | **REAL** |
| 9 | `Reflector.reflect` → `memory.query_trace_steps` | **REAL** |
| 10 | `FakeLLM.complete(prompt)` returns hardcoded Python string | **FAKE** |
| 11 | `_run_in_sandbox` spawns `sys.executable -I -c <code>` | **REAL** (subprocess + tempfile) |
| 12 | Parent reads child stdout, parses Verdict | **REAL** |
| 13 | `Curator.curate` writes Rule via `memory.put_rule` | **REAL** |
| 14 | `a.sync_once()` JSON-encodes rules, calls `transport.gossip` | **REAL** read / **FAKE** transport |
| 15 | `InProcessTransport.gossip` iterates peer's handler list | **FAKE** |
| 16 | B's `SyncEngine._on_message` calls `memory.put_rule` | **REAL** |
| 17 | Follow-up `run_task` on A | **FAKE** actor, **REAL** DB |
| 18 | Follow-up on B | **FAKE** actor, **REAL** DB |

**Counts:** 9 REAL I/O hops (SQLite + Reflector subprocess). 9 FAKE / in-process. External edges (LLM, actor, transport) all FAKE; persistence + Reflector subprocess REAL.

## 7. Disk effects after capstone

Two files written, 64 KB each:

```
65536  test_capstone_closed_loop_seed0\a_0.db
65536  test_capstone_closed_loop_seed0\b_0.db
```

**`a_0.db`:** five tables; `skb_entities`, `skb_meta`, `skb_relations` all **0 rows**; `skb_rules` 1 row (the learned `retry_with_delay` rule); `skb_traces` 3 rows.

**`b_0.db`:** identical schema; `skb_entities`, `skb_meta`, `skb_relations` all **0 rows**; `skb_rules` 1 row with the same id as A's (synced); `skb_traces` 2 rows.

**One sentence:** two 64 KB SQLite files, each with one learned Rule and 2–3 trace rows; the three knowledge-graph tables exist as schema but are never written to.

(After this audit, that gap was closed in `1100201`: Generator now populates `skb_entities` + `skb_relations` per task interaction.)

## 8. The four questions

**Q1. Would the closed loop work end-to-end against real IP-Symcon + real Anthropic API?** No (at the time of inspection). `AgentInstance.build()` required a `transport: Transport` argument and `src/skillbook/p2p_sync/transport.py` was twelve lines containing only the `Transport` Protocol declaration — no concrete implementation in `src/`. The customer could not construct an AgentInstance without importing `tests/fakes/transport.py:InProcessTransport`. `pyproject.toml` declared an `[mqtt]` extra with `aiomqtt`, but zero code in `src/` imported it. The LLM path *would* have worked — `default_llm()` returned a real `AnthropicLLM` when `ANTHROPIC_API_KEY` was set, and `SymconActor` was a real urllib JSON-RPC client. But without a transport, no closed loop.

(After this audit, `bf546c2` shipped `AsyncioTcpTransport` and `564d2da` shipped `MqttSubscriber` + `aiomqtt_message_stream` in `src/`.)

**Q2. Would two real Jarvis instances on different machines sync skillbook deltas?** No (at the time of inspection). `SyncEngine.sync_once()` built a JSON envelope and called `await self.transport.gossip(payload)`; the only working `Transport` was `tests/fakes/transport.py:InProcessTransport` which used an in-process reference and synchronously iterated its handler list. Two processes on the same machine shared no `peer._handlers` list, let alone two machines. The Bloom filter primitive existed but was never imported by `engine.py` (zero `bloom` references in `src/skillbook/p2p_sync/engine.py`).

(After this audit, `bf546c2` shipped real TCP, `b4d9868` wired Bloom into `engine.py`, and `994bf6c` added a capstone variant proving the full loop converges over `AsyncioTcpTransport`.)

**Q3. Percentage of the architecture document's promises actually implemented as runtime behavior?** About **30 %** at the time of inspection. Genuinely real and exercised: SQLite persistence; Reflector subprocess sandbox; AgentDoG StrEnum diagnostics; stdlib-urllib-backed JSON-RPC. Half-real: ACE three-agent loop (plumbing existed but Reflector's analysis was FakeLLM's hardcoded snippet); Curator delta-update dedup (worked for exact content); CRDT merge (correct under unenforced precondition). Named but absent: MCTS / LATS tree search; Bloom-assisted set reconciliation (Bloom code existed, never wired); SHIMI hierarchical Merkle-DAG (zero code); TTSR student/teacher (zero code); MQTT subscriber (zero `aiomqtt` imports in `src/`); knowledge-graph bi-temporal validity (schema existed, zero rows). Structural skeleton ~80% present; algorithmic substance ~10%; weighted, about 30%.

(After this audit, MCTS, Bloom-wiring, MQTT, and KG-writes were all closed. SHIMI hierarchical Merkle-DAG and TTSR remain unimplemented as documented future work.)

**Q4. Most embarrassing first-hour discovery for a paying customer?** The shipped agent could not be instantiated without importing from the test directory. A customer reading the README and trying `from skillbook.agent import AgentInstance` would discover that `AgentInstance.build()` required a `transport` argument, and the only Transport implementation in the entire codebase was `tests/fakes/transport.py:InProcessTransport`. The docstring hint pointed them at `tests/fakes/` — a paying customer would discover the production product required imports from `tests/`. (Closed by `bf546c2`: `AsyncioTcpTransport` now ships in `src/`.)
