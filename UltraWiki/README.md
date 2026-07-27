# UltraWiki — the semantic memory mode

> **Status: design only. Nothing here is implemented yet.** This folder is the
> specification that coding agents will later build from. It was written from
> scratch on 2026-07-24, based on the Cerebras Knowledge architecture
> (see [Prior art](#prior-art)) and twelve maintainer decisions recorded below.
> Earlier design documents that once lived in this folder were deliberately
> discarded; this set does not build on them.

UltraWiki is the second mode of the Jarvis Wiki section. Where the normal wiki
is a local, keyword-searched collection of markdown pages, UltraWiki is a
**semantic personal memory**: it continuously ingests everything the user
chooses to connect — local files, chat exports, mail, calendar, code, and any
data reachable through Jarvis's own plugins and CLIs — and answers questions
about the user's own life with **cited, exact facts**, fast enough to feed the
**realtime voice pipeline from day one**.

The north-star query used as the worked example throughout these documents:

> **"When was I at dinner with Viktoria in San Francisco?"**
>
> → *"On 23 June 2024, at Trestle in San Francisco. Sources: WhatsApp export
> (23 Jun), calendar event 'Dinner w/ Viktoria'."*

Answering that well requires entities (who is Viktoria), time (a date IS the
answer), places, activity semantics (dinner ≈ restaurant ≈ essen), and reading
the right thread out of years of history in a few hundred milliseconds.
<!-- i18n-allow: "essen" is a German activity-synonym example the multilingual matcher must handle -->

---

## The two modes (either-or, risk-free switching)

The Wiki section offers exactly one active mode at a time (maintainer decision
D-5). The switch is **role-based, never destructive** (D-9):

| | **Normal Wiki** | **UltraWiki** |
|---|---|---|
| Store | Obsidian markdown + FTS index | unified semantic store (SQL + vectors + entities) |
| Search | keyword | hybrid: keyword + vector + entity graph + time |
| Sources | Jarvis conversations | every connected source, incl. the normal wiki itself |
| Answers | page links | cited answers with exact dates, people, places |
| Needs | nothing (offline, free) | user-supplied storage + embedding + distillation choices |
| Voice | page lookups | full semantic answers within the realtime latency budget |

- Activating UltraWiki ingests the complete normal wiki as one of its sources —
  nothing is lost by switching forward.
- Switching back pauses UltraWiki (its store stays on disk untouched) and the
  normal wiki simply resumes. Nothing is ever deleted by the mode switch.
- While UltraWiki is active, it fully replaces the normal wiki as the capture
  and answer system.

## Decision log (maintainer, 2026-07-24)

| # | Decision |
|---|---|
| D-1 | Design written from scratch; earlier cleared design docs are not a basis. |
| D-2 | **Bring-your-own everything.** Jarvis bundles and provisions no database and no service. A dedicated UltraWiki settings section (modeled on the API-Keys section) lets the user pick storage, models, and sources — always with at least one local AND one cloud option per slot. |
| D-3 | The embedding model is a **deliberate choice in the activation wizard**. Switching later triggers an explicit, confirmed rebuild — the new vector space is built in the BACKGROUND alongside the live one, so semantic search never goes dark and switching back before it completes costs nothing. Only the model defines the space: the same model behind a different provider needs no rebuild at all. |
| D-4 | Source scope is "the whole life": local files (Obsidian, Jarvis conversations, folders), export-file imports (WhatsApp, takeouts), OAuth APIs (Google mail/calendar/drive, GitHub), and a **generic bridge that turns every connected Jarvis plugin/CLI into a data source**. |
| D-5 | UI is an **either-or mode switch** inside the Wiki section; the active mode replaces the other completely. |
| D-6 | The distillation model (the LLM that summarizes raw content before storage) is chosen in the same activation wizard — local or cloud, one conscious privacy decision. |
| D-7 | The first big import is **staged**: keyword-searchable immediately, embeddings async, LLM distillation progressively by priority. Usable from minute one. |
| D-8 | **Voice from day one.** The realtime voice pipeline queries UltraWiki in v1; the sub-second latency budget applies from the start. |
| D-9 | Mode switching is non-destructive in both directions; both stores persist. |
| D-10 | Identity resolution: certain matches merge automatically; uncertain ones go to a **confirmation queue** in the UI; every merge is reversible. |
| D-11 | Freshness: always-on background sync (watchers, push where available, cursor polling) + a per-source "sync now" button + a nightly reconcile run. |
| D-12 | **Areas from the start**: sources are grouped into named areas (e.g. Work / Private / Project X) in v1; search and voice can be scoped to an area. |

## Document index

| File | Covers |
|---|---|
| [`01-architecture.md`](01-architecture.md) | Principles, layer model, the unified store and its schema |
| [`02-connectors-and-ingestion.md`](02-connectors-and-ingestion.md) | Connector contract, source families, the staged write pipeline, freshness |
| [`03-retrieval-and-voice.md`](03-retrieval-and-voice.md) | The read path, hybrid search + fusion + rerank, the voice latency budget |
| [`04-configuration-and-ui.md`](04-configuration-and-ui.md) | The settings section, activation wizard, provider slots, UI surfaces |
| [`05-identity-areas-privacy.md`](05-identity-areas-privacy.md) | Entity resolution, the confirmation queue, areas, privacy and deletion |
| [`06-roadmap.md`](06-roadmap.md) | Build phases with hard gates |
| [`07-visual-projection.md`](07-visual-projection.md) | The readable face: topic/moment pages, the entity graph, and the generated Obsidian vault |
| [`08-universal-ingestion.md`](08-universal-ingestion.md) | Taking in everything from every platform worldwide: one extractor, media enrichment, the platform export guide, the generic custom source |

## Prior art

- **Cerebras, "How Cerebras Built Its Enterprise Knowledge Base"** — the
  architectural template: meet data where it lives; one unified embeddings
  table; distill before embedding; hybrid retrieval fused with RRF; a
  planner → parallel executor → synthesis read path; retrieval primitives
  exposed as simple tools for agents; scoped search.
- **Anthropic, "Introducing Contextual Retrieval"** — prepend context to each
  chunk before embedding (cited by the Cerebras article as technique (2)).
- **Cormack et al., SIGIR 2009** — reciprocal rank fusion.
