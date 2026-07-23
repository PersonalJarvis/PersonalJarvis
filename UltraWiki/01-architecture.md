# 01 · Architecture

Five layers. Data flows **down** on the write path and **up** on the read path.
The middle layer — the **Unified Store** — is the whole point: everything from
every source lands in one schema and becomes queryable through one interface.

```
            WRITE PATH  ↓                              READ PATH  ↑
  ┌─────────────────────────────────────────────────────────────────┐
  │  L1  CONNECTORS      Slack · WhatsApp · GitHub · Drive · Cal ·    │
  │                      Notion · Mail · Obsidian · Screenshots       │
  ├─────────────────────────────────────────────────────────────────┤
  │  L2  INGEST & ENRICH  fetch delta → distill (LLM) → extract       │
  │                       entities/relations/time → embed             │
  ├─────────────────────────────────────────────────────────────────┤
  │  L3  UNIFIED STORE    one row per unit:                           │
  │        ├ vector index (HNSW)       ← meaning                      │
  │        ├ FTS5 index                ← exact tokens                 │
  │        ├ metadata indexes          ← who/where/when filters       │
  │        └ entity+temporal graph     ← multi-hop, bi-temporal       │
  ├─────────────────────────────────────────────────────────────────┤
  │  L4  HYBRID RETRIEVAL plan → parallel fan-out → RRF → rerank →    │
  │                       context expansion                           │
  ├─────────────────────────────────────────────────────────────────┤
  │  L5  ANSWER           synthesis LLM → cited answer / voice        │
  └─────────────────────────────────────────────────────────────────┘
```

Legend for the reuse tags used across the docs: **[HAVE]** exists in Jarvis
today · **[EXTEND]** exists but needs a small addition · **[NEW]** genuinely new.

---

## L3 — the Unified Store (the core data model)

Two tables plus the derived indexes. Everything is keyed so ingestion is
**idempotent** (re-processing the same source item updates in place, never
duplicates).

### Table A — `memory_unit` (one row per distilled unit)

| Field | Purpose |
|---|---|
| `id` | internal primary key |
| `source` | `slack` \| `whatsapp` \| `github` \| `drive` \| `calendar` \| `notion` \| `mail` \| `obsidian` \| `screenshot` |
| `external_id` | stable id in the source (thread ts, file id, PR number) — **dedup/update key** |
| `kind` | `message_thread` \| `doc_section` \| `pr` \| `issue` \| `event` \| `note` \| `email` \| `image` |
| `one_line` | the searchable "what is this about" question/summary |
| `distilled_text` | the LLM-normalized document — **this is what gets embedded**, not the raw text |
| `raw_ref` | URI/pointer back to the original (open in Slack/Drive); raw text is *not* stored |
| `embedding` | dense vector (dim = provider's, e.g. 1024–3072) → HNSW index |
| `participants` | array of entity ids (people in the unit) → metadata index |
| `place` | entity id / geo string → metadata index |
| `valid_from`, `valid_to` | **bi-temporal**: when the fact was true in the world |
| `ingested_at`, `expired_at` | **bi-temporal**: system time; supersede, don't delete |
| `scope` | project/permission tag (which sources a query may see) |
| `source_meta` | JSON blob: author, url, reactions, thread length, etc. |

Local backend: **sqlite-vec** on the existing `data/jarvis.db` (the `wiki_fts`
table already lives there). Cloud backend: **Postgres + pgvector**. Same schema,
swappable driver.

### Table B — the entity/temporal graph (GraphRAG + Graphiti style)

- **`entity`**: `id`, `type` (`person` \| `place` \| `org` \| `project` \|
  `event` \| `thing`), `name`, `aliases[]`, `canonical_ref` (e.g. a Jarvis
  Contact id — **[HAVE]** contacts already exist), `embedding` (for fuzzy entity
  match).
- **`edge`**: `subject → predicate → object`, each carrying `valid_from`,
  `valid_to`, `source_unit_id`, `confidence`. Facts are **invalidated, not
  deleted** when superseded (Graphiti's rule) — so "who is Viktoria *now*" and
  "who was Viktoria *in 2024*" are both answerable.

Example edges written from one WhatsApp thread:

```
(Ruben) —[had_dinner_with]→ (Viktoria)   valid_from=2024-06-23  src=wa#123
(dinner#88) —[took_place_in]→ (San Francisco)   valid_from=2024-06-23
(dinner#88) —[at_venue]→ (Trestle)
(Viktoria) —[is_a]→ (person)   canonical_ref=contact:viktoria-m
```

This graph is what makes the Viktoria query precise instead of "semantically
nearby". See [`04-accuracy-techniques.md`](04-accuracy-techniques.md).

---

## Why one store, not one-per-source

Cerebras's key move: a Slack thread, a PR, a Drive doc, a calendar event all
become **the same shape** — a distilled document with a vector and metadata. A
query never has to know "which source do I search"; it searches the union and the
metadata/scope tags sort out provenance and permissions. New sources join by
emitting rows in this schema — no retrieval code changes.

---

## Where each layer maps in Jarvis (short form; full map in doc 07)

| Layer | Jarvis today |
|---|---|
| L1 Connectors | `marketplace/` OAuth, `channels/`, `plugins/tool/` (Drive, Calendar), wiki `watcher.py` **[EXTEND]** |
| L2 Distill | `wiki/extractor.py` + `consolidator.py` + `curator.py` **[HAVE]**; + embed step **[NEW]** |
| L3 Store | `data/jarvis.db` + `wiki_fts` **[HAVE]**; + vector index + graph tables **[NEW]** |
| L4 Retrieval | `wiki/search.py` (FTS) **[HAVE]**; + vector/graph/fusion/rerank **[NEW]** |
| L5 Answer | `provider_chain.py` **[HAVE]** |
