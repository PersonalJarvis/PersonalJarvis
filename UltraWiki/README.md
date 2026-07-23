# UltraWiki — the Personal Brain

> **Status: design & idea collection. No implementation yet.** This folder is the
> spec that coding agents will later build from. All artifacts here are English
> per the repo language rule; conversation about them happens in the maintainer's
> language.

UltraWiki turns Jarvis's existing wiki from a local keyword-searched Obsidian
vault into a **semantic personal memory** that ingests everything the user
already produces — Slack, WhatsApp, GitHub, Google Drive, Calendar, Notion,
e-mail, the Obsidian vault itself — and answers questions about the user's own
life **fast enough for the realtime voice model** and **accurate enough to name
the exact date, place, and people** behind a vague question.

The north-star query, used as the worked example throughout these docs:

> **"When was I at dinner with Viktoria in San Francisco?"**
>
> → *"On 23 June 2024, at Trestle in San Francisco. Sources: WhatsApp thread
> (23 Jun), calendar event 'Dinner w/ Viktoria'."*

Answering that well is the whole design problem. It needs **entities** (who is
Viktoria), **places** (San Francisco), **time** (a specific date is the answer),
**activity semantics** (dinner ≈ essen ≈ restaurant), and it must read the *right*
chat thread out of years of history in a few hundred milliseconds.

> **Locked decisions (2026-07-23) — see [`09-decisions-and-handoff-integration.md`](09-decisions-and-handoff-integration.md):**
> **(D-1)** built inside Jarvis, in Python, reusing the existing ~2/3.
> **(D-2)** store access via **connection string only — no vector-DB SDK in code**;
> vector search is a DB extension (`pgvector`/`sqlite-vec`) queried through SQL.
> **(D-3)** Postgres + pgvector primary, SQLite + sqlite-vec local floor.
> Doc 09 merges an external architecture handoff and, where it sharpens docs 01–08,
> **it wins**.

---

## The two modes (like Realtime vs Pipeline for the API keys)

| | **Normal Wiki** (ships today) | **Ultra Wiki** (this design) |
|---|---|---|
| Store | Obsidian markdown + SQLite FTS5 | + vector index + entity/temporal graph |
| Search | keyword (BM25) | hybrid: keyword + vector + graph + time |
| Sources | Jarvis conversations | + every connected external source |
| Answers | page links | cited RAG answers with exact facts |
| Needs | nothing (offline, free) | 1 embedding provider (local or cloud) |
| Fallback | — | degrades cleanly back to Normal Wiki |

Ultra is an **add-on, not a replacement**. If the embedding provider is missing,
Ultra falls back to Normal Wiki and says so. This mirrors the AP-22/AP-23 mandate:
provider-agnostic, key-aware, degrades honestly, runs on a headless Linux box.

---

## The two paths (the whole system in one sentence each)

- **Write path (background, always-on):** a source changes → Jarvis fetches the
  delta → an LLM **distills** it into a clean document → **extracts entities,
  relations, and timestamps** → **embeds** it → **upserts** one row into the
  unified store and edges into the graph. Incremental, idempotent, never
  re-scans everything. → [`02-ingestion-write-path.md`](02-ingestion-write-path.md)

- **Read path (live, must be fast):** a question arrives → an LLM **plans** it
  into `{entities, place, time-window, semantic core, sources}` → resolves
  "Viktoria" to a real contact → **fans out in parallel** over vector + keyword +
  graph + time → **fuses** (RRF) → **reranks** → **expands context** →
  **synthesizes** a cited answer. → [`03-retrieval-read-path.md`](03-retrieval-read-path.md)

---

## Document index

| File | What it covers |
|---|---|
| [`01-architecture.md`](01-architecture.md) | The 5 layers, the unified store schema, the entity/temporal graph |
| [`02-ingestion-write-path.md`](02-ingestion-write-path.md) | Connectors, how "always latest" works (webhook / socket / poll / CDC), distill → extract → embed → upsert |
| [`03-retrieval-read-path.md`](03-retrieval-read-path.md) | Query understanding → fan-out → fuse → rerank → synthesize, with the Viktoria query traced end-to-end |
| [`04-accuracy-techniques.md`](04-accuracy-techniques.md) | Every trick for "extremely accurate on vague questions": contextual retrieval, GraphRAG, bi-temporal, entity resolution, HyDE, reranking, VisionRAG |
| [`05-performance-realtime.md`](05-performance-realtime.md) | The latency budget, ANN/HNSW, prefilter-first, precompute, streaming — how it stays realtime-fast |
| [`06-deployment-local-cloud.md`](06-deployment-local-cloud.md) | Local vs cloud for every layer, privacy, graceful degradation |
| [`07-reuse-map-and-gaps.md`](07-reuse-map-and-gaps.md) | What Jarvis already has vs what is genuinely new (the ~1/3 to build) |
| [`08-roadmap-and-open-questions.md`](08-roadmap-and-open-questions.md) | Phased build order + the decisions only the maintainer can make |
| [`09-decisions-and-handoff-integration.md`](09-decisions-and-handoff-integration.md) | **Authoritative.** Locked decisions, the external handoff merged in, decision log, kill-list, phase gates, Python-adapted tech reference |

---

## How to use this with coding agents later

Each doc is written so a coding agent can take one phase from
[`08-roadmap-and-open-questions.md`](08-roadmap-and-open-questions.md), read the
relevant design doc, and implement it against the existing Jarvis modules named
in [`07-reuse-map-and-gaps.md`](07-reuse-map-and-gaps.md). The design deliberately
reuses Jarvis's `curator`/`extractor`/`consolidator` distill pipeline, the
`provider_chain` key-aware fallback, the FTS5 index, the wiki watcher, the
`marketplace/` OAuth machinery, and the `contacts` store — so most of the work is
wiring, not greenfield.

## Prior art these docs lean on

- **Cerebras Knowledge** — the "meet the data where it lives / one embeddings
  table / distill before embed / hybrid search / speed enables architecture"
  doctrine.
- **Anthropic Contextual Retrieval** — prepend context to each chunk before
  embedding; hybrid + RRF + rerank (−67% failed retrievals).
- **Graphiti / Zep** — bi-temporal knowledge graph for agent memory (valid-time
  vs ingestion-time, invalidate-don't-delete). This is the backbone of temporal
  accuracy.
- **ColPali / ColQwen2** — vision-based document retrieval (late interaction) for
  screenshots, photos, and visually-rich PDFs, no OCR.
