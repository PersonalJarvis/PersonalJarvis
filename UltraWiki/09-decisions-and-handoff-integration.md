# 09 · Locked Decisions & Handoff Integration

> This document merges an external architecture handoff (a second, independent
> design session) into the UltraWiki design. **Where it sharpens or contradicts
> docs 01–08, this document wins.** The two sessions converged on the same
> north-star query independently — strong evidence the direction is right.

---

## 0. Locked decisions (maintainer, 2026-07-23)

These are settled. Do not revisit without refuting the rationale.

### D-1 · Build inside Jarvis, in Python
UltraWiki is a module of Personal Jarvis, not a separate product. Reuse what
Jarvis already ships (~2/3): the distill pipeline (`extractor`/`consolidator`/
`curator`), contacts/person-pages, the `marketplace/` OAuth machinery, the
`provider_chain` key-aware fallback, FTS5, the wiki watcher. The external handoff
assumed a JavaScript greenfield build — its **principles are language-neutral and
adopted in full; its language/stack are not**. Every stack item below is mapped to
a Python equivalent.

### D-2 · Store access via connection string only — NO vector-DB SDK in code
The store is a SQL database reached through a **connection string**. Vector search
lives as a **database extension** (`pgvector` on Postgres, `sqlite-vec` on SQLite)
queried through **plain SQL**. The code never imports a vector-DB client
(`chromadb`, `faiss`, `pinecone-client`, `qdrant-client`, `weaviate-client`, …).

Rationale: swappable (local SQLite ↔ cloud Postgres over the same SQL layer), no
vendor lock-in, testable without a running vector service, headless-safe, and it
matches AP-21 (gate on capability, not a product name). The DB access layer is a
thin typed-SQL wrapper (`asyncpg` / SQLAlchemy Core / raw SQL) — a SQL toolkit,
**not** a vector SDK.

### D-3 · Primary store Postgres + pgvector; SQLite + sqlite-vec is the local floor
Handoff Decision 1: one store holds vector, full-text, time-ranges, and graph in a
single transaction, with sync-state next to the data. Postgres 16+ with `pgvector`
+ `pg_trgm` is the primary. `sqlite-vec` on the existing `data/jarvis.db` is the
zero-server local/headless floor. Both are addressed via D-2 (connection string +
SQL). **Open point (O-1):** running Postgres on a plain desktop / headless box
(embedded Postgres vs. requiring a server vs. defaulting to the SQLite floor).

---

## 1. The four reframes (adopted from the handoff — the load-bearing ideas)

### R-1 · It's a reconstruction problem, not a retrieval problem
The sentence the search should find often **does not exist** in the data: a chat
reads "19:00?" — "works" — "omw", with no restaurant, no city, no date. No
similarity search finds a sentence that was never written. The answer is
**reconstructed** from several independent sources — chat + photo EXIF/GPS +
calendar + card statement + location history. The **structured event/entity layer
carries the load; vector search is the fallback, not the main path.** This
sharpens doc 03/04: retrieval is *triangulation across sources*, not top-k
similarity.

### R-2 · History and flow are two separate systems
A one-time 10-year backfill and the last-minute inflow have completely different
needs (rate limits, legality, fault tolerance, mode). One mechanism for both is
the classic mis-build.
- **History comes from official data exports** — GDPR Art. 20 forces any
  EU-operating service to hand data over machine-readably. WhatsApp "Export chat",
  Google Takeout, Instagram, Apple, Amazon, Netflix, Spotify. Legal, complete, no
  rate limit, no ban risk. **This is how we get WhatsApp** — not a realtime
  connector.
- **Flow comes from APIs, webhooks, and local file watchers.**
This replaces doc 02's single "backfill trigger" with two distinct paths.

### R-3 · Realtime is created on write, not on read
Already in our docs (doc 05). Kept as a first-class principle: the write path is
expensive, async, and does all the work up front; the read path only looks things
up. The only route to sub-second.

### R-4 · State belongs in the database, not the call stack
Not `fetch() → normalize() → embed() → store()`. If step 3 fails, 1 and 2 are
lost; a restart redoes everything; one bad item blocks the queue. Instead **each
item carries its state as a column**; workers perform exactly one transition and
commit. Failures are local, retryable, and block nothing. This is a hard upgrade
over doc 02's "durable queue" — it becomes an explicit state machine.

---

## 2. Decision log (adopted, adapted to Python/Jarvis)

| # | Decision | Why | Rejected |
|---|---|---|---|
| 1 | Postgres as the single store (SQLite floor) | vector+FTS+time+graph in one transaction; sync-state with data | separate vector DB → consistency hell |
| 2 | Structured extraction before embedding | R-1 | pure vector RAG |
| 3 | Exports for backfill, APIs for flow | R-2 | one mechanism for both |
| 4 | Unofficial clients (Baileys etc.) NOT in core repo | shipping them distributes a ToS breach + account-ban risk (pattern: yt-dlp, HACS) | Baileys in main repo |
| 5 | A connector yields only `RawItem`, never touches the DB | 3rd-party community code must not reach the user's data | connector writes itself |
| 6 | `AsyncIterator`, not a returned list | 10-year backfills are six-figure; streaming lets the runtime own memory + checkpoints | return an array |
| 7 | No enrichment in the connector | pure I/O is testable without models, cost, or network | connector embeds itself |
| 8 | A `capabilities` declaration drives the scheduler | connector authors don't write scheduling logic | each connector polls itself |
| 9 | State machine with state in the DB | R-4 | function chain |
| 10 | Extraction cached on `(content_hash, prompt_version, model)` | LLM output isn't deterministic even at temp 0; identical input is never re-called, so the *system* is reproducible | call every time |
| 11 | Versioned enrichment, not rebuild | the extraction prompt will change; a full re-import on a local system is unacceptable | throw DB away, re-import |
| 12 | Push for latency, poll for correctness | webhooks arrive twice / never / out of order; a desktop app has no public endpoint anyway | webhooks only |
| 13 | Bitemporality on facts | else "where does X live" can't be told from "where did X live in 2018" | one timestamp |
| 14 | Confirmation queue, not auto-merge below a confidence threshold | Trap 1 | always auto-merge |
| 15 | Multilingual embeddings (`bge-m3` class) | the data is largely German; English-tuned models measurably degrade there | default English model |
| 16 | Four complementary indexes, fused by RRF | each covers where the others fail | vector only |
| 17 | `permalink` mandatory on every item | without it the system knows *when* but not *where to* — half the requirement | store content only |

---

## 3. What kills this project (traps, by damage)

1. **Wrong identity merge** — two people fused into one entity; every answer about
   both is now wrong, noticed months later. *Fix:* deterministic resolution first;
   uncertain cases into a **confirmation queue**, not an auto-merge; merges logged
   reversibly. **This is the #1 killer.**
2. **Function chain instead of state machine** (R-4) — unrepairable without a
   rebuild. *Fix:* get Phase 1 right before anything else exists.
3. **Missing idempotency** — reruns after crashes/deploys must converge. *Fix:*
   `UNIQUE (source, external_id)`, every write an UPSERT, every transition
   individually retryable.
4. **Relative time stored as string** — "next Friday" left as text is never
   filterable again; episodic ability collapses. *Fix:* resolve against the
   message's own timestamp, store as an absolute time range.
5. **Vector search as the main path** — works in a 10-doc demo, fails the litmus
   test. *Fix:* extraction layer first, embeddings as a supplement.
6. **Missing `permalink`** — surfaces only when the corpus is big; retrofitting
   means a full re-import. *Fix:* mandatory from item one.
7. **Unofficial clients in core** — users lose accounts, it reflects on the
   project. *Fix:* a separate community repo with an install-time warning.
8. **No eval set** — can't tell if a change improved or regressed anything. *Fix:*
   Phase 6.
9. **Migrations on distributed local DBs** — not one server DB but thousands of
   local DBs in different states. *Fix:* forward-only, each migration tested
   against a fixture DB of the previous version.
10. **Webhooks as the only truth** — gaps go unnoticed until an answer is wrong.
    *Fix:* a nightly reconcile run per connector.
11. **Forgotten deletions** — user deletes a message, the system keeps answering
    with it. *Fix:* tombstones, detection in the reconcile run, cascading delete
    onto everything derived.

---

## 4. Phases & gates (the real discipline is the gates)

The gate — what must be *true* before the next phase begins — matters more than
the phase. Note this **reorders** doc 08: **runtime-first**, indexes late.

- **P1 · Runtime.** `RawItem` schema; state machine (`raw → normalized → resolved
  → extracted → indexed`, plus `retrying`/`failed`); worker loop with retry,
  backoff, dead-letter. **Gate:** four scenarios green as automated tests —
  (a) a 1000+-item backfill hard-killed mid-run restarts to an identical end state
  (no dupes, no gaps); (b) a connector throwing at item 500 lets 1–499 finish and
  500 land in `failed`; (c) a second run over unchanged data creates zero new
  enrichment jobs (proof via `content_hash`); (d) the whole suite runs with no
  network and no credentials.
- **P2 · First connector + fixture CI.** Exactly one Class-D connector (local
  file, no OAuth, no network) + the fixture test harness. **Gate:** a stranger can
  write and CI-test a connector with no network/credentials/DB. If the contract
  feels awkward here, fix the contract before P3.
- **P3 · Identity resolution.** `entities` + `identifiers`, deterministic
  resolution, contact-book match, fuzzy resolution (name similarity + co-occurrence),
  confirmation queue. **Gate:** a golden set of 30–50 known people resolves
  correctly; **zero wrong merges**; uncertain cases provably queue.
- **P4 · Episodic layer.** Session segmentation, burst detection, LLM event
  extraction, temporal anchoring of relative expressions, bitemporality. **Gate:**
  the litmus test is answered correctly on a real data slice — with date,
  confidence, and `permalink` to the evidence. **This is where the project proves
  it works; before this it's all infrastructure.**
- **P5 · Indexes & query path.** Four indexes, slot extraction, routing, parallel
  fan-out, RRF fusion, rerank, cross-source triangulation, cited synthesis.
  **Gate:** latency *measured*, not estimated — under 1 s to first token on a
  multi-hop question over a realistically large corpus.
- **P6 · Eval as a CI gate.** 100–200 real questions with known answers.
  Retrieval-recall measured **separately** from answer accuracy. **Gate:** a
  regression breaks the build. Only from here is prompt-tuning worthwhile.
- **P7 · Connector scaling.** Only now the 2nd, 3rd, 10th connector. Built earlier,
  the runtime gets rewritten repeatedly.

---

## 5. Technical reference (adapted to Python)

### 5.1 `items`
`source`, `external_id`, `thread_id`, `author_identifier` (raw, unresolved),
`timestamp_utc`, `body`, `attachments`, `permalink`, `content_hash`, `status`,
`attempt_count`, `last_error`, `next_retry_at`, `deleted_at`.
`UNIQUE (source, external_id)`.

### 5.2 `entities` / `identifiers`
`entities`: person, place, org, project. `identifiers`: many-to-one on `entities`
— one person typically has 5–10 identifiers (phone, several emails, handles,
display names, nicknames). Reuse Jarvis contacts as the seed.

### 5.3 `events`
`event_type` (`meal`/`travel`/`meeting`/`purchase`/`milestone`), `participants`
(entity ids), `place_id`, `valid_time` (time range), `confidence`, `evidence`
(item ids), `extraction_version`.

### 5.4 Connector contract (Python)
A connector is a `Protocol` yielding only `RawItem`; it never touches the DB, does
no enrichment, and declares capabilities that drive the scheduler:
- `id: str`; `auth: oauth2 | apikey | local-file | export-upload | none`
- `capabilities`: `backfill: bool`, `incremental: push | cursor | full-scan | none`,
  `deletes: bool`
- `backfill(ctx, checkpoint=None) -> AsyncIterator[RawItem]`
- `incremental(ctx, cursor) -> AsyncIterator[RawItem]`

### 5.5 Procurement classes
| Class | Examples | Backfill | Flow | Risk |
|---|---|---|---|---|
| A — API + push | Gmail, GCal, GitHub, Notion, Slack, Linear | API | webhook | none |
| B — API, poll only | smaller services | API | cursor-poll | none |
| C — Export | WhatsApp, Instagram, Apple, Amazon, Netflix | file import | ✗ | none |
| D — Local artifacts | iMessage `chat.db`, Apple Notes, Photos, browser history, Obsidian, filesystem | read file | file watcher | none |
| E — Unofficial | Baileys, scrapers | yes | yes | ToS breach, account ban |
**Class D is the underrated foundation** — no OAuth, no network, no credentials,
no legal question, instantly testable, and identical to the local-first claim.
**Start P2 with a Class-D connector.**

### 5.6 Backfill mechanics
Split into monthly windows, descending. Each completed window persists a
checkpoint; an abort resumes at the last complete window. `sync_state` per (user,
connector): `cursor`, `last_success_at`, `backfill_position`, `backfill_complete_at`.

### 5.7 Indexes
| Index | Technique | Covers |
|---|---|---|
| Vector | `pgvector` HNSW over session summaries + bursts | paraphrase |
| Full-text | Postgres `tsvector` + GIN (or ParadeDB BM25); SQLite FTS5 on the floor | exact strings, names, errors |
| Time | GiST over a time range | everything episodic |
| Graph | edge table person↔person, person↔place, person↔topic | multi-hop |
Plus a per-person entity profile, re-summarized on change, so "who is X" is a
lookup, not a scan.

### 5.8 Stack (Python mapping — all store access via D-2)
Postgres 16+ (`pgvector` + `pg_trgm`), SQLite + `sqlite-vec` floor · thin typed
SQL (`asyncpg` / SQLAlchemy Core / raw SQL — **no vector SDK**) · a DB-backed job
queue (state-in-DB, no Redis) · `aioimaplib` IMAP IDLE for mail · Google Calendar
push · `piexif`/`Pillow`/`exifread` for photo metadata · `bge-m3` (multilingual)
locally via `sentence-transformers` or Ollama · `dateparser` + LLM fallback for
time expressions · a rerank capability (Voyage `rerank-2` or a local cross-encoder)
· `provider_chain` (any fast provider) for planner + synthesis.

### 5.9 Latency budget
Ingest: raw item full-text-findable in < 2 s, embedding async after, LLM
extraction within minutes. Query: planner 100–200 ms, parallel retrieval
50–150 ms, rerank 100 ms, synthesis streamed. Under 1 s to first token.

---

## 6. Open points (decide before the relevant phase)
- **O-1 · Postgres on a local/headless box** (D-3) — embedded Postgres vs. require
  a server vs. default to the SQLite floor. Affects P1/P7.
- **O-2 · Webhook endpoint for a local desktop install** — relay service
  (centralized, weakens privacy) vs. pure polling. Affects P7.
- **O-3 · Extraction model local or external** — external is faster/better but raw
  content leaves the device; with this corpus that's a matter of principle.
  Affects P4. (Ties to Jarvis's privacy-hybrid stance.)
- **O-4 · Cost of the first import** — LLM extraction over 10 years of chat is the
  single biggest line item; tiered model choice by information density is an option.
- **O-5 · Per-service export parsers** — every provider has its own format that
  changes; needs the same fixture approach as API connectors.
- **O-6 · Encryption at rest + key management** — the most sensitive corpus a user
  will ever hold in one place.

---

## 7. How this reshapes docs 01–08

| Earlier doc | Sharpened / overridden by |
|---|---|
| 02 ingestion | R-2 (history=exports vs flow=APIs), R-4 (state machine), traps 3/9/10/11, procurement classes A–E |
| 03 retrieval | R-1 (reconstruction/triangulation), slot extraction, cross-source confirm |
| 04 accuracy | trap 1 (wrong merge → confirmation queue), D-15 (multilingual embeddings), bitemporality as facts |
| 05 performance | R-3 confirmed; latency budget kept |
| 06 deployment | D-2 (connection-string-only, no vector SDK), D-3 (Postgres primary / SQLite floor) |
| 07 reuse map | D-1 reaffirms Python/Jarvis reuse; the new-vs-have split stands |
| 08 roadmap | replaced by §4 here — runtime-first, hard gates, indexes late, eval as a gate |

**Bottom line:** the handoff's principles are adopted wholesale; they are built in
Python inside Jarvis, over a connection-string SQL store with no vector SDK, with
runtime + identity + episodic layers proven (gated) before indexes and scale.
