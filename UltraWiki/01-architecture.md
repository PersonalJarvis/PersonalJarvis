# 01 · Architecture

## Principles (each one is load-bearing)

1. **Meet data where it lives.** No source is forced into a new home. One
   connector per source extracts data directly from where the user already
   produces it; the platforms stay the system of record for editing.
2. **One unified store.** Every source — a WhatsApp export line, an Obsidian
   note, a calendar event, a plugin's output — lands as rows of the same shape
   in one database. Anything in the store is immediately queryable through the
   same interface; adding a source never changes the query side.
3. **Distill before you embed.** Raw content is kept and keyword-indexed as-is,
   but what gets embedded is a normalized document an LLM distilled from it
   (question, summary, resolution, entities, references). Cerebras measured a
   significant accuracy gain from this normalization; raw transcripts embed
   poorly because chat is fragmentary and length-biased.
4. **Do the expensive work on write, never on read.** Ingestion may take
   seconds per item and run for hours in the background; the query path only
   looks things up. This is the only route to the day-one voice budget (D-8).
5. **Hybrid retrieval, no single scorer trusted.** Keyword search catches exact
   tokens (names, error strings), vector search catches paraphrase, term-rarity
   weighting separates signal from filler, recency decay retires stale answers,
   and the entity/time indexes answer "who/when" directly. Their ranked lists
   are fused; no technique is allowed to veto the others.
6. **Bring-your-own everything, degrade honestly (D-2).** No bundled services.
   Every capability slot (storage, embedding, distillation) offers at least one
   local and one cloud option, chosen by the user. A missing or dead choice
   produces an honest message and a graceful fallback, never a brick — on
   Windows, macOS, Linux, and a headless server alike.

## Layer model

```
┌────────────────────────────────────────────────────────────────────┐
│ SURFACES     Wiki UI (Ultra mode) · chat · realtime voice ·        │
│              REST routes → jarvis CLI · brain tools for agents     │
├────────────────────────────────────────────────────────────────────┤
│ READ PATH    planner → parallel fan-out over indexes → RRF fusion  │
│              → rerank → context expansion → cited synthesis        │
├────────────────────────────────────────────────────────────────────┤
│ UNIFIED STORE   items · documents+embeddings · entities ·          │
│                 identifiers · events · areas · sync/queue state    │
├────────────────────────────────────────────────────────────────────┤
│ WRITE PATH   staged pipeline (state machine in the DB):            │
│              captured → keyword-indexed → embedded → distilled     │
│              → entity-linked                                       │
├────────────────────────────────────────────────────────────────────┤
│ CONNECTORS   local files · export imports · OAuth APIs ·           │
│              Jarvis plugin/CLI bridge · community connectors       │
└────────────────────────────────────────────────────────────────────┘
```

Higher layers reach lower ones only via the existing Jarvis protocol/event
conventions; the write path runs strictly off the voice hot path.

## Storage: two backends, one SQL surface

The store is an SQL database addressed through a **user-supplied location** —
a local file path or a connection string (D-2). Vector search is provided by a
database extension, queried through plain SQL; the code never imports a
vector-database SDK. This keeps the two backends behind one code path:

| | **Local option** | **Cloud / self-hosted option** |
|---|---|---|
| Engine | SQLite file under the Jarvis data dir | PostgreSQL via connection string (any host: own server, Supabase, Neon, RDS, …) |
| Vectors | `sqlite-vec` extension | `pgvector` extension |
| Keyword search | FTS5 | `tsvector` + GIN |
| Setup burden | zero — created on activation | user pastes a connection string in the settings section |
| Fits | single device, offline, default experience | multi-device access, large corpora, server installs |

Both run the identical schema and the identical query templates (a thin
dialect adapter covers the FTS and vector syntax differences). Migrations are
forward-only and tested against fixture databases of the previous version,
because thousands of independent local installs will be on different versions.

## Unified store schema (v1)

Column lists are indicative, not final DDL.

- **`uw_items`** — one row per raw unit (a message thread, a note, a calendar
  event, a mail, a file chunk, a plugin record):
  `id, source_id, external_id, area_ids, thread_key, author_raw, body_raw,
  attachments, permalink, timestamp_utc, content_hash, state, attempt_count,
  last_error, next_retry_at, deleted_at`.
  `UNIQUE (source_id, external_id)`; every write is an upsert (idempotent
  re-runs). `state` drives the staged pipeline (doc 02). **`permalink` is
  mandatory from item one** — an answer must always be able to deep-link back
  to where the evidence lives.
- **`uw_documents`** — the distilled, embed-ready documents derived from items
  (thread summary, burst, file chunk, event description):
  `id, item_id, doc_type, text_normalized, embedding, embed_model,
  distill_version, idf_score, created_at`.
  Several documents may derive from one item (whole-thread + notable bursts).
- **`uw_entities` / `uw_identifiers`** — people, places, organizations,
  projects; identifiers map raw handles (phone numbers, emails, nicknames,
  display names) many-to-one onto entities. Seeded from Jarvis contacts.
  Merge history is kept so any merge is reversible (D-10).
- **`uw_events`** — reconstructed episodic facts: `event_type (meal / travel /
  meeting / purchase / milestone), participant_entity_ids, place_entity_id,
  time_range, confidence, evidence_item_ids, extraction_version`. Time ranges
  are always absolute (relative expressions are resolved against the source
  item's own timestamp at extraction time — "next Friday" is never stored as
  text).
  **Shipped (2026-07-28, `migrations/0004_events.sql`).** Three properties
  worth stating because they are load-bearing rather than incidental:
  - **Bi-temporal.** `occurred_at`/`occurred_end` are VALID time (when it
    happened); `recorded_at` is TRANSACTION time (when the source recorded the
    statement). A Monday message about a Friday dinner answers both "when was
    it" and "when did I plan it". `occurred_end` is always filled — from
    `occurred_precision` when the source gave no end — so a month-precision
    event still matches a query about one day inside it.
  - **No new model call.** Events ride the per-document distillation that
    already runs: prompt version 2 emits an `events` array in the SAME call.
    The model normalizes LANGUAGE (a relative expression in any language
    becomes one token of a closed English vocabulary); the deterministic
    resolver in `jarvis/ultrawiki/events.py` does the arithmetic. A corpus
    distilled under version 1 still yields events wherever its stored
    distillation states an absolute date, without being re-distilled.
  - **`time_anchor` is the honesty column** (`absolute` / `relative` /
    `recorded`): an event anchored merely on the item's own timestamp is worth
    far less than one whose source spelled the date out, and a surface that
    cannot tell them apart will state a guess as a fact.
- **`uw_areas`** — named source bundles (D-12): `id, name, icon, is_default`.
  A source belongs to one or more areas; items inherit their source's areas at
  ingest time so scoping is a cheap indexed filter, not a join cascade.
- **`uw_sources` / `uw_sync_state`** — configured sources with their connector
  type, credential reference, schedule, and per-source cursor/checkpoint
  (backfill position, last success, last reconcile).
- **`uw_merge_log` / `uw_confirm_queue`** — reversible identity merges and the
  pending uncertain matches surfaced in the UI (doc 05).

## Why the normal wiki survives untouched

UltraWiki writes to its own tables and its own files; it treats the normal
wiki's vault as a read-only source. The mode switch (D-5/D-9) only decides
which system captures new knowledge and which system answers. This makes the
switch trivially reversible and keeps the zero-dependency wiki as the honest
fallback whenever an UltraWiki capability slot is unconfigured or its provider
is down.
