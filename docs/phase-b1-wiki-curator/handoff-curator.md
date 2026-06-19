# Phase B1 — Instance D Handoff (Curator LLM)

**Branch:** `impl/wiki-memory-b1-curator` (forked from `impl/wiki-memory` @ `ef041608`)
**Worktree:** `C:/Users/Administrator/Desktop/jarvis-wiki-b1-curator/`
**Owner:** Instance D
**Status:** Done. 46 tests green, regression-checked against existing config tests.

---

## What this branch ships

Two production modules + one config extension + 46 unit tests:

| File | Lines | Purpose |
|---|---|---|
| `jarvis/memory/wiki/curator_llm.py` | ~330 | `WikiCuratorLLM` — provider-agnostic LLM curator. Resolves provider via `[memory.wiki.curator]` → `brain.primary` fallback; wraps the brain call in `asyncio.wait_for(timeout_s)`; never raises; produces `list[PageUpdate]`. |
| `jarvis/memory/wiki/prompt.py` | ~290 | `build_system_prompt`, `build_user_prompt`, `compute_vault_summary`, `select_top_slugs`. Pure functions. Schema is fed verbatim into the system prompt — Python never reinterprets it. |
| `jarvis/core/config.py` (edit) | +33 | New Pydantic models `WikiCuratorConfig` + `WikiMemoryConfig`; hooked into `MemoryConfig.wiki`. |
| `jarvis.toml` (edit) | +11 | New `[memory.wiki.curator]` block with provider/model defaults left blank for the documented fallback. |
| `tests/unit/memory/wiki/test_curator_llm.py` | ~470 | 26 test cases: parse-coverage, provider-resolution, timeout, brain-exception, missing-schema, unavailable-provider, caching, request-shape, default-registry, fallback resolution. |
| `tests/unit/memory/wiki/test_prompt.py` | ~250 | 20 test cases: keyword-overlap ranking, vault-summary, system-prompt schema-verbatim, user-prompt rendering, full pipeline smoke. |
| `jarvis/memory/wiki/protocols.py` | ~140 | **Briefing-§4 verbatim** — see *Drift Notes* below. |

---

## How the Brain provider is chosen

1. Read `[memory.wiki.curator].provider`. If empty, use `brain.primary`.
2. Read `[memory.wiki.curator].model`. If empty, use `brain.providers[<resolved-provider>].model`.
3. Instantiate the brain via `BrainProviderRegistry.instantiate(provider, model=model)`.
4. Cache the brain instance for the lifetime of the `WikiCuratorLLM`.

Pattern mirrors `jarvis/awareness/verdichter.py` — same lazy-instantiate-on-first-call, same `asyncio.to_thread` for the registry call to keep the event loop free.

The default `jarvis.toml` block ships with both fields blank, so the curator follows the user's voice-switch ("Jarvis, wechsel auf Gemini" → `brain.primary = "gemini"` → curator switches with it). Pinning is opt-in: drop a model name into the config to override.

---

## Failure modes (all return `[]`, all log a warning, never raise)

* `len(source_content.strip()) < 3` — no LLM call.
* `schema.md` unreadable — no LLM call.
* Provider not in registry / `KeyError` from `instantiate` — caught and logged.
* `asyncio.TimeoutError` from `wait_for` — duration logged.
* Any other exception from `brain.complete` — message truncated, logged.
* Response is not parseable JSON, or the outermost array is malformed — logged.
* Individual items malformed (bad operation, empty body, missing `rename_from` on a rename) — dropped one-by-one with a count log.
* More than 30 valid updates in one response — truncated to 30 (hard cap).

The salience filter — "smalltalk → `[]`" — lives in the system prompt. Python does not classify content; the LLM does. This was a user decision (aggressive ingest strategy) recorded in the README.

---

## Drift notes (what Wave 2 needs to know)

### 1. `protocols.py` was committed on this branch too

Reason: my production code imports `PageUpdate` and the `PageRepository` / `VaultIndex` Protocols at module load time. Without `protocols.py` the branch is not importable, which means the test suite can't even collect.

Solution: I committed a **verbatim copy** of the Briefing § 4 contract (frozen `WikiPage`, `PageUpdate`, `WriteResult` dataclasses + four Protocols). Instance A's branch contains the same file content because it is also a verbatim copy of the same briefing.

**Wave-2 merge expectation:** identical-content merge for `protocols.py`. Pick either side. If Instance A's version diverged (added helper methods, changed signatures), use Instance A's version — they own the file and my consumers (parse + dataclass construction) only depend on the public surface.

### 2. `tests/unit/memory/wiki/__init__.py` is a new empty file

The directory existed (other instances put test files there) but the package marker was missing on my branch. Empty file, no merge risk.

### 3. `asyncio.to_thread(self._registry.instantiate, ...)` instead of direct call

`BrainProviderRegistry.instantiate` may load entry-point classes the first time, which can do file I/O. I wrap the call in `to_thread` so the event loop never blocks even on a cold registry. If Wave 2 wires the curator into a hot path (Phase B5 plans this), this stays safe.

### 4. Brief mentioned `BrainProviderRegistry.get_provider` — the real API is `instantiate`

The Briefing § 5 → Instance D wording says "patch `BrainProviderRegistry.get_provider`". The real method is `instantiate(name, **kwargs)` (verified at `jarvis/brain/provider_registry.py:45`). Tests patch / fake the real method. No production-code workaround needed.

### 5. Output contract block hardened beyond the brief

The system prompt's "Output Contract" section explicitly tells the LLM to:

* return ONLY a JSON array (no prose, no code fences),
* `[]` for smalltalk / ack-only / content-free sources,
* never break a `[[wikilink]]` (also create missing targets in the same array),
* never touch `_archive/` or `attachments/`, never store secrets,
* keep the response below the output-token budget rather than truncate.

This is stricter than the briefing example. `_parse_updates` is still tolerant of code-fenced output as a belt-and-braces fallback when the LLM ignores the instruction.

### 6. Isolated worktree

I created a separate worktree at `C:/Users/Administrator/Desktop/jarvis-wiki-b1-curator/` because Instance B / C were actively branch-switching the shared `jarvis-wiki-memory/` worktree, which kept losing my edits. Wave 2 can prune this worktree after merging via `git worktree remove`.

---

## How to run

```powershell
cd 'C:/Users/Administrator/Desktop/jarvis-wiki-b1-curator'
python -m pytest tests/unit/memory/wiki/test_curator_llm.py `
                 tests/unit/memory/wiki/test_prompt.py -v
```

Expected: **46 passed**. No fixtures from `conftest.py` are needed — every test defines its fakes inline (`FakeBrain`, `FakeRegistry`, `FakeVault`, `FakeRepo`, `_FakeVault`, `_Page`).

---

## What this branch deliberately does NOT do

* No disk writes. `propose_updates` is pure-compute over the source + vault snapshot. Instance C decides what to do with the returned `PageUpdate` list.
* No `atomic_writer` import. The dependency arrow is C → D, never D → C.
* No heuristic salience filter in Python. The LLM is responsible.
* No re-implementation of the schema. `schema.md` is loaded verbatim into the system prompt.
* No bus subscription. Phase B5 wires `BrainTurnCompleted` / `EpisodeRecorded` / `MissionCompleted` to the curator; B1 only provides the function.

---

## Open questions for Wave 2

1. Where does Wave 2 want `schema_path` to come from at runtime? Right now it's a constructor arg. Suggested default: `Path(config.memory.data_dir) / "workspace" / "schema.md"`. I left this to the integrator since the `WikiCurator` orchestrator (Wave 2) holds the vault root.
2. Should `WikiCuratorLLM.provider_name` (already exposed) be promoted into a structured `CuratorStats` object for the FastAPI status endpoint in Phase B3? Not in B1 scope; flagged for B3.
3. Is the 30-update hard cap (`_MAX_UPDATES_PER_INGEST = 30`) the right number? The schema documents "typically 10-15 pages per ingest", so 30 covers two ingests' worth of guard rails. Tunable via constant — no config field yet.
