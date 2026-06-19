# Wiki Autonomous Curator — Wave 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two-stage conversation-fed curator — a cheap ADD-only fact extractor feeding a durable SQLite candidate journal, and a body-aware consolidator that judges each candidate ADD/UPDATE/NOOP/INVALIDATE against the actual bodies of the k-nearest pages — plus the living user profile, invalidate-not-delete history, the wired `CuratorScheduler` (after the `VaultLock` wall-clock fix), telemetry quality counters, and a deterministic self-documentation page `memory.md` that refreshes on every consolidation run.

**Architecture:** Conversation turns (voice `TranscriptFinal` + chat/channel `MessageSent(role="user")`) flow fire-and-forget into Stage 1 (`ConversationFactExtractor`, cheap model via the existing `_resolve_provider_and_model` hook) which appends atomic candidate facts to `wiki_candidate_journal` in `data/jarvis.db`. Stage 2 (`Consolidator`) drains the journal in batches under `CuratorScheduler` (cooldown + `VaultLock`), retrieves k-nearest pages via FTS5/BM25 (`VaultSearch`) + slug overlap, shows their FULL bodies to the judge, and applies decisions through the existing Wave-1 write pipeline (`WikiCurator` link demotion → `AtomicWriter` secret guard/backup/validate/rollback/FTS upsert). Contradictions set `valid_until` + `superseded-by` frontmatter — nothing is deleted.

**Tech Stack:** Python 3.11, sqlite3/FTS5 (no new base dependency — CLOUD.md doctrine), Pydantic v2, pytest `asyncio_mode=auto`, fakes in-test (no `unittest.mock` for components).

**Source spec:** `docs/superpowers/specs/2026-06-09-wiki-autonomous-curator-design.md` §4–§8 (Wave-2). Approved direction; D1–D5 binding. Wave 1 landed as commits `4709c40c..f2c77c0c`.

---

## Binding cross-task decisions

1. **One model hook for both stages (spec §4.1/D3):** the extractor and the consolidator both resolve provider/model through `jarvis.memory.wiki.curator_llm._resolve_provider_and_model(cfg.memory.wiki.curator, root_cfg)` — the same `[memory.wiki.curator]` pair the Wiki settings card writes. No second provider config.
2. **Decision vocab is consolidator-level, writer ops stay frozen.** `add|update|noop|invalidate` live in the new `jarvis/memory/wiki/constants.py` (single source) + SQL CHECK + Pydantic `Literal` + parity test. An INVALIDATE decision **materialises as an AtomicWriter `update`** (frontmatter `valid_until` + `superseded-by` on the superseded page); `AtomicWriter.ALLOWED_OPERATIONS` and `curator_llm._VALID_OPERATIONS` are NOT extended — this avoids widening the legacy blind-curator contract that Wave 2 supersedes. (Documented deviation from spec §8-Wave-2.3 wording; the on-disk semantics are identical and the five-layer discipline is applied to the journal vocab where it actually crosses layers: Python ↔ SQL ↔ Pydantic. No TS layer exists for these strings — they surface only as telemetry counter names.)
3. **Wave-2 writes reuse the Wave-1 pipeline.** `WikiCurator` gains one public method `apply_external_updates(updates, *, source_label, verb)` = dangling-link demotion → `writer.apply` → `log.md` entry. The consolidator and the profile/self-doc writers call it (or `AtomicWriter` directly for the deterministic `memory.md`) — never raw file writes (AP-3).
4. **AP-9 is non-negotiable:** extractor and consolidator only ever run inside `asyncio.create_task` background tasks; the bus handlers return immediately. B3 ships the regression test.
5. **Lock fix FIRST (spec §10):** `VaultLock` writes `time.time()` (wall clock) into the lock file before the scheduler is wired (B4).
6. **Parallel sessions:** anchor edits by literal strings, stage only named files, never `git add -A`. Commit per task with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task B1: `constants.py` + `CandidateJournal` + migration 0005

**Files:**
- Create: `jarvis/memory/wiki/constants.py`
- Create: `jarvis/memory/migrations/0005_wiki_candidate_journal.sql`
- Create: `jarvis/memory/wiki/journal.py`
- Test: `tests/unit/memory/wiki/test_candidate_journal.py`, `tests/unit/memory/wiki/test_curator_decision_parity.py`

**constants.py** (single source of truth, BUG-008 defense):

```python
"""Wire-format vocabulary for the two-stage conversation curator.

Single source of truth (five-layer-enum discipline, docs/anti-drift-three-layer.md):
Python tuple here -> SQL CHECK constraint in 0005_wiki_candidate_journal.sql ->
Pydantic Literal in journal.py. Parity tests pin all layers. These strings
surface to the UI only as telemetry counter names (wiki_consolidator_<decision>),
so no TypeScript layer exists for them.
"""
from __future__ import annotations

from typing import Literal

# Lifecycle of one candidate fact in the journal.
CANDIDATE_STATUSES: tuple[str, ...] = ("pending", "consolidated", "rejected", "skipped")
CandidateStatus = Literal["pending", "consolidated", "rejected", "skipped"]

# Stage-2 judge decision per candidate.
CURATOR_DECISIONS: tuple[str, ...] = ("add", "update", "noop", "invalidate")
CuratorDecision = Literal["add", "update", "noop", "invalidate"]

# Runtime drift assertions (mirror jarvis/memory/constants.py pattern).
assert set(CANDIDATE_STATUSES) == set(CandidateStatus.__args__)  # type: ignore[attr-defined]
assert set(CURATOR_DECISIONS) == set(CuratorDecision.__args__)  # type: ignore[attr-defined]
```

**Migration `0005_wiki_candidate_journal.sql`** (mirror 0004's header style; CHECK lists MUST stay byte-aligned with constants.py):

```sql
-- 0005_wiki_candidate_journal.sql
-- Stage-1 candidate facts extracted from conversation turns (Wave 2,
-- docs/superpowers/specs/2026-06-09-wiki-autonomous-curator-design.md §4).
-- Durable append-only queue: survives restarts; drained by the consolidator.
CREATE TABLE IF NOT EXISTS wiki_candidate_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ms      INTEGER NOT NULL,
    source_label    TEXT    NOT NULL,
    turn_hash       TEXT    NOT NULL,
    fact            TEXT    NOT NULL,
    kind            TEXT    NOT NULL DEFAULT 'other',
    subjects        TEXT    NOT NULL DEFAULT '[]',   -- JSON array of strings
    status          TEXT    NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'consolidated', 'rejected', 'skipped')),
    decision        TEXT
        CHECK (decision IS NULL OR decision IN ('add', 'update', 'noop', 'invalidate')),
    target_path     TEXT,
    consolidated_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wiki_candidate_journal_status
    ON wiki_candidate_journal (status, id);
```

**journal.py** — sync sqlite3 + `threading.Lock` (called only from background tasks; sub-ms inserts). Public surface:

```python
@dataclass(frozen=True, slots=True)
class CandidateFact:
    fact: str
    kind: str = "other"
    subjects: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class JournalRow:
    id: int
    created_ms: int
    source_label: str
    turn_hash: str
    fact: str
    kind: str
    subjects: tuple[str, ...]
    status: CandidateStatus

class CandidateJournal:
    def __init__(self, db_path: Path, *, clock=time.time) -> None: ...  # lazy connect, ensure_schema via migration SQL exec (idempotent CREATE IF NOT EXISTS)
    def append(self, facts: Sequence[CandidateFact], *, source_label: str, turn_hash: str) -> int: ...
    def pending(self, limit: int = 20) -> list[JournalRow]: ...
    def mark(self, ids: Sequence[int], *, status: CandidateStatus, decision: CuratorDecision | None = None, target_path: str | None = None) -> None: ...
    def backlog_count(self) -> int: ...
    def seen_turn(self, turn_hash: str) -> bool: ...   # dedupe support for B3
    def close(self) -> None: ...
```

`ensure_schema` executes the migration file's SQL directly (idempotent `IF NOT EXISTS`) so the journal works even when the DB was never opened through `RecallStore`; databases opened via `RecallStore.open()` get the same DDL through `run_migrations` (same pattern as `fts_index.ensure_schema` + 0004).

**Tests:** append→pending→mark roundtrip; status/decision CHECK rejected at SQL level (`pytest.raises(sqlite3.IntegrityError)` for an invalid status written via raw SQL); backlog_count; seen_turn; **parity test** asserting (a) constants tuple == Literal args, (b) the migration file's CHECK lists contain exactly `CANDIDATE_STATUSES` / `CURATOR_DECISIONS` (regex-extract from the SQL text).

Commit: `feat(wiki): durable candidate journal + decision vocab (Wave-2 Stage-1 store)`.

---

### Task B2: `ConversationFactExtractor` (Stage 1, ADD-only)

**Files:**
- Create: `jarvis/memory/wiki/extractor.py`
- Modify: `jarvis/core/config.py` — new `ExtractorConfig` (`enabled: bool = True`, `min_user_chars: int = 12`, `max_output_tokens: int = 800`, `timeout_s: float = 30.0`, `ConfigDict(extra="allow")`) + field `extractor: ExtractorConfig` on `WikiMemoryConfig` (AP-16).
- Test: `tests/unit/memory/wiki/test_extractor.py`

Extractor contract:

```python
class ConversationFactExtractor:
    def __init__(self, *, config: JarvisConfig, journal: CandidateJournal, registry: BrainProviderRegistry | None = None) -> None: ...
    async def extract_and_journal(self, user_text: str, assistant_text: str, *, source_label: str, turn_hash: str) -> int:
        """One cheap LLM call -> 0..N CandidateFacts -> journal.append. Returns count."""
```

- Provider/model: `_resolve_provider_and_model(cfg.memory.wiki.curator, cfg)` (decision 1). Lazy brain via `BrainProviderRegistry.instantiate`, cached, `asyncio.wait_for(timeout_s)`.
- Prompt (module constant, English): system = "You extract durable personal-memory facts from one conversation turn… Return ONLY a JSON array of {\"fact\", \"kind\", \"subjects\"} objects. kind ∈ identity|preference|person|project|decision|event|other. Recall-biased: when unsure whether something matters long-term, include it. Return [] for smalltalk, questions without personal content, or commands with no durable fact. Never include credentials." User = user_text + (assistant reply as context, truncated to 500 chars).
- Guards: `len(user_text) < cfg.min_user_chars` → 0 without LLM call; `is_length_truncated(agg.finish_reason, agg.text)` → discard + `telemetry.inc("wiki_writes_blocked_truncated")`; malformed JSON → 0; every accepted fact `telemetry.inc("wiki_candidates_extracted")`. ADD-only: this class never touches the vault, only `journal.append`.
- Tests with in-test `FakeBrain` (mirror `test_curator_llm.py` FakeBrain): happy path appends parsed facts; smalltalk/short input → no brain call; truncated stream → nothing appended; JSON in code fence tolerated (reuse `curator_llm._extract_json_array`).

Commit: `feat(wiki): Stage-1 conversation fact extractor (cheap model, ADD-only, journal-backed)`.

---

### Task B3: Conversation observer wiring (voice + chat) — AP-9 pinned

**Files:**
- Modify: `jarvis/memory/wiki/voice_bridge.py` — `VoiceFactBridge` gains optional `extractor: ConversationFactExtractor | None`; both ack + aggressive paths route to `_spawn_extract` (fire-and-forget `asyncio.create_task` calling `extractor.extract_and_journal`) when an extractor is present; legacy `curator.ingest` path kept as fallback when `extractor is None` (`WikiIntegrationConfig.fallback_to_direct_ingest` posture). Add subscription to `MessageSent` (`jarvis/core/events.py:290`) filtering `role == "user"` so desktop chat + Discord/Telegram channels feed the same journal; pair with the next `ResponseGenerated` like the voice path.
- Dedupe: module-level helper `_turn_hash(text) -> str` (sha1 of casefolded/whitespace-collapsed text) + a bounded LRU (`collections.OrderedDict`, max 128) inside the bridge — voice turns surface on BOTH `TranscriptFinal` and `MessageSent` (`server.py:1017`), so a hash seen recently is skipped; `journal.seen_turn` is the durable second line of defense.
- Modify: `jarvis/memory/wiki/integration.py` — `bootstrap_wiki_integration` constructs `CandidateJournal` + `ConversationFactExtractor` (guarded; on any failure falls back to direct-ingest mode with a logged English message) and passes the extractor into `VoiceFactBridge`.
- Test: `tests/unit/memory/wiki/test_conversation_observer.py` —
  1. voice turn → fact lands in journal (FakeBrain-driven extractor), `curator.ingest` NOT called;
  2. chat `MessageSent(role="user")` + `ResponseGenerated` → journal;
  3. same text via both events → journaled once (hash dedupe);
  4. **AP-9 regression:** extractor's fake brain sleeps 0.5 s; assert the bus `publish(...)` of both events returns in < 100 ms and the journal is still empty at that moment (extraction completes later) — the voice path never awaits extraction;
  5. `extractor=None` → legacy direct `curator.ingest` still fires (fallback preserved).

Commit: `feat(wiki): conversation observer feeds extractor journal (voice + chat, off the voice path)`.

---

### Task B4: `VaultLock` wall-clock fix, then `CuratorScheduler` wiring

**Files:**
- Modify: `jarvis/memory/wiki/lock.py` — lock-file content switches from `time.monotonic()` to `time.time()` (lines 111, 167) and `_steal_if_stale` compares with `time.time()` (line 139). The `acquire` deadline loop (lines 58–65) KEEPS `time.monotonic()` (in-process timeout — correct usage). Update module docstring: monotonic timestamps in the file are meaningless across process restarts/reboots (the monotonic clock restarts at 0), so a stale lock from a crashed previous boot could appear "fresh" forever or "from the future". Tolerate-and-steal: a parsed timestamp that is > 60 s in the FUTURE is treated as corrupt (pre-fix monotonic remnant) and stolen.
- Modify: `jarvis/core/config.py` — `SchedulerConfig` gains `consolidate_after_candidates: int = 8` (journal-pressure threshold; `ConfigDict(extra="allow")` present).
- Modify: `jarvis/memory/wiki/scheduler.py` — `TriggerSource` gains `JOURNAL = "journal"`; constructor gains optional `consolidator=None`; `_do_trigger` routes `JOURNAL` (cooldown-honouring, lock-guarded) to `await self._consolidator.run_once()` and returns its label; without a consolidator the JOURNAL source skips with `skip_reason="no_consolidator"`.
- Modify: `jarvis/ui/web/server.py:~1753` — replace `scheduler_factory=None` with a real factory building `CuratorScheduler(curator=..., lock=VaultLock(cfg.memory.wiki.scheduler.lock_path, stale_after_seconds=cfg.memory.wiki.scheduler.lock_stale_after_seconds), config=cfg.memory.wiki.scheduler, consolidator=...)`; consolidator injected in B5 (B4 wires with `consolidator=None`).
- Modify: `jarvis/memory/wiki/integration.py` — after a journal append, the observer checks `journal.backlog_count() >= cfg.memory.wiki.scheduler.consolidate_after_candidates` and fires `asyncio.create_task(scheduler.trigger(TriggerSource.JOURNAL))` (guarded, background-only).
- Tests: `tests/unit/memory/wiki/test_lock.py` additions — (a) stale wall-clock lock from a "previous boot" (write `pid;<time.time()-9999>`) is stolen; (b) fresh wall-clock lock is not; (c) future-dated timestamp (monotonic remnant) is stolen. `tests/unit/memory/wiki/test_scheduler.py` additions — JOURNAL trigger runs the consolidator under cooldown; no consolidator → skip.

Commit: `fix(wiki): cross-process VaultLock staleness uses wall clock; wire CuratorScheduler with journal-pressure trigger`.

---

### Task B5: `Consolidator` (Stage 2, body-aware judge + invalidate)

**Files:**
- Create: `jarvis/memory/wiki/consolidator.py`
- Modify: `jarvis/memory/wiki/curator.py` — add public `apply_external_updates(self, updates, *, source_label, verb="consolidate") -> WriteResult` (= `_demote_dangling_links` → `writer.apply` → `log.append_log_entry(verb=verb, ...)`); the consolidator uses it so every Wave-1 guardrail applies.
- Modify: `jarvis/memory/wiki/prompt.py` — add `build_consolidator_prompt(candidates, neighbours)`: per candidate the k-nearest pages with their FULL bodies (undoing the `del repo` blindness), the decision contract (JSON array of `{candidate_id, decision: add|update|noop|invalidate, target, new_body, reason}`), the page-type templates from `schema.md`, the living-profile section list, and the hard rules: UPDATE must preserve all existing facts/sections of the page it edits (smallest correct edit); INVALIDATE names the superseded page and the superseding slug; links follow create-or-refuse.
- Consolidator contract:

```python
class Consolidator:
    def __init__(self, *, config: JarvisConfig, journal: CandidateJournal, curator: WikiCurator, search: VaultSearch, repo: PageRepository, vault_root: Path, registry=None, batch_limit: int = 20, k_nearest: int = 4) -> None: ...
    async def run_once(self) -> str:
        """Drain one batch: retrieve -> judge -> apply -> mark -> refresh self-doc. Returns a source label."""
```

- Retrieval per candidate: `search.search(candidate.fact)` top-k + slug-overlap on `candidate.subjects`; load full page text from disk (vault-root-anchored); dedupe neighbours across the batch.
- One LLM call per batch (provider/model via decision 1; truncation guard; `_extract_json_array`).
- Decision execution: `add`/`update` → `PageUpdate(create/update)`; `noop` → mark only; `invalidate` → load the superseded page, set frontmatter `valid_until: <today>` + `superseded-by: "[[<superseding-slug>]]"` (frontmatter merge, body untouched) as a `PageUpdate(update)`. All applied through `curator.apply_external_updates`; per-decision telemetry `wiki_consolidator_<decision>` + `wiki_consolidator_runs` once.
- Journal bookkeeping: applied → `mark(status="consolidated", decision=..., target_path=...)`; judge dropped/malformed → `mark(status="skipped")`; blocked by secret-guard / failed validation → `mark(status="rejected")`. A candidate is never lost silently.
- Tests `tests/unit/memory/wiki/test_consolidator.py` (FakeBrain returns scripted decision JSON; tmp vault): ADD creates a page; UPDATE merges in place (old facts survive — assert a pre-existing line is still present); NOOP only marks; INVALIDATE sets `valid_until` + `superseded-by` and deletes nothing; bodies of neighbours appear in the prompt (assert via FakeBrain's received request); truncated judge output → batch marked `skipped`, no writes.

Commit: `feat(wiki): Stage-2 body-aware consolidator (add/update/noop/invalidate, invalidate-not-delete)`.

---

### Task B6: Living profile (D4)

**Files:**
- Create: `jarvis/memory/wiki/profile.py` — `PROFILE_SECTIONS = ("Identity", "Preferences", "Work style", "Values", "Relationships", "Active projects", "Decisions")` + `ensure_profile_skeleton(vault_root, slug, *, curator) -> bool`: loads `entities/<slug>.md`, appends any MISSING `## <section>` headings (existing content byte-preserved), writes via `curator.apply_external_updates(verb="update")`. Idempotent.
- Modify: `jarvis/memory/wiki/integration.py` — call `ensure_profile_skeleton` once at bootstrap (guarded, uses `cfg.memory.wiki.session_rollup.user_entity_slug`).
- Modify: `jarvis/memory/wiki/prompt.py` — consolidator prompt names the profile page + sections as the preferred UPDATE target for identity/preference/person facts.
- Test: `tests/unit/memory/wiki/test_profile.py` — skeleton added once, idempotent re-run, existing Summary/Facts preserved.

Commit: `feat(wiki): living user profile skeleton + consolidator targeting (D4)`.

---

### Task B7: Self-documentation page `memory.md` (deterministic, refreshes)

**Files:**
- Create: `jarvis/memory/wiki/self_doc.py` — `render_memory_page(*, vault_root, journal, telemetry_snapshot, now) -> str` (pure) + `refresh_memory_page(*, vault_root, writer, repo, journal) -> None`. Page = root-level `memory.md`, frontmatter `type: meta` (precedent: `schema.md`), sections: "How my memory works" (static explainer of the two-stage pipeline), "Live status" (page counts per type via dir glob, journal backlog, last-refresh timestamp, ADD/UPDATE/NOOP/INVALIDATE totals from the telemetry snapshot), "Recently updated" (10 newest non-archive pages by mtime as `[[wikilinks]]`). **No LLM call.** Write via `AtomicWriter.apply` (`update` op when the file exists, `create` otherwise).
- Modify: `jarvis/memory/wiki/consolidator.py` — `run_once` ends with `refresh_memory_page` (guarded best-effort).
- Modify: `jarvis/ui/web/server.py` — `_init_wiki_boot_index` tail also calls `refresh_memory_page` (guarded) so the page exists from first boot.
- Verify/extend `jarvis/memory/wiki/page.py` validation: a root-level `type: meta` page must parse `is_schema_valid=True` (schema.md is the precedent); add a unit case; extend `REQUIRED_KEYS`/dir-derivation only if the test proves it necessary.
- Test: `tests/unit/memory/wiki/test_self_doc.py` — page created at first refresh; second refresh updates "Live status" (timestamp/backlog change) without duplicating sections; wikilinks resolve (run `dangling_link_targets` over the rendered page == `[]`); never touches `_archive/`.

Commit: `feat(wiki): deterministic self-documentation page memory.md, refreshed at boot + per consolidation run`.

---

### Task B8: Telemetry quality counters

**Files:** `jarvis/memory/wiki/telemetry.py` (`DEFAULT_COUNTERS` += `wiki_candidates_extracted`, `wiki_consolidator_add`, `wiki_consolidator_update`, `wiki_consolidator_noop`, `wiki_consolidator_invalidate`, `wiki_consolidator_runs`), `tests/unit/memory/wiki/test_telemetry.py` (snapshot contains the six at 0). Auto-exposed by `GET /api/wiki/telemetry` (`wiki_routes.py:596`).

Commit: `feat(wiki): consolidator quality counters in DEFAULT_COUNTERS`.

---

### Task B9: End-to-end acceptance (spec D5)

**Files:** `tests/integration/memory/wiki/test_two_stage_e2e.py`

Scripted `FakeBrain` (separate canned responses for extractor + consolidator calls), tmp vault with profile seed, real journal/extractor/consolidator/curator/writer stack:

1. Turn 1 "My friend Lena moved to Hamburg and works as a veterinarian." → extractor facts → consolidator ADD → `entities/lena.md` exists, valid schema, linked from the profile's Relationships section (consolidator UPDATE on the profile in the same batch).
2. Turn 2 "Lena got a new job at the animal clinic in Altona." → UPDATE in place: `entities/lena.md` still ONE file, old fact retained, new fact added, no `lena-2.md`.
3. Turn 3 "Lena actually moved to Berlin, not Hamburg." → INVALIDATE/UPDATE: the superseded statement carries `valid_until` + `superseded-by` (or the body is corrected in place with the old fact marked superseded) — nothing deleted.
4. A turn containing `api_key = ABCD1234EFGH5678IJKL` → fact may journal, but the write is blocked (`blocked_pii`), journal row marked `rejected`, page absent.
5. Zero dangling links across the produced pages (`dangling_link_targets` == `[]` for every written page); `memory.md` exists and its "Live status" reflects the runs; telemetry counters advanced.

Then full buckets: `py -3.11 -m pytest tests/unit/memory/wiki/ tests/integration/memory/ tests/integration/test_wiki_boot_index.py tests/integration/test_wiki_provider_route.py tests/unit/brain/ -q` + `ruff check` on every touched file. Live smoke after app restart (real conversation → page appears on the user-chosen Wiki model) — manual, after restart (pywebview RAM bundle).

Commit: `test(wiki): two-stage curator end-to-end acceptance (D5)`.

---

## Self-review notes

- Spec §8-Wave-2 items 1–4 map to B1–B3 (item 1), B4+B5 (item 2), B5+B6 (item 3), B8 (item 4); the self-doc page (mission §7 acceptance) is B7; D5 acceptance is B9.
- Cloud-first: sqlite3/FTS5/pathlib only; network calls go to the user-chosen provider; everything boots on `python:3.11-slim`.
- AP-2 covered by the Wave-1 secret guard at the single write surface; AP-9 pinned by B3 test 4; AP-16 via `ConfigDict(extra="allow")` on the new config classes.
