# 07 · Reuse Map & Gaps — what exists vs what's new

The honest scope. Jarvis already ships roughly two-thirds of this system. The
genuinely new work centers on **one keystone (embeddings + vector index)** and its
downstream wiring. This doc names the existing modules a coding agent should build
on, so nothing is rebuilt from scratch.

## Already in Jarvis — reuse it **[HAVE]**

| Capability | Where | Use for |
|---|---|---|
| Two-stage distill pipeline | `jarvis/memory/wiki/extractor.py`, `consolidator.py`, `curator.py` | L2 distill + consolidate — add an embed step at the end |
| Atomic writes (lock+tempfile+BOM) | `wiki/atomic_writer.py`, `core/config_writer.py` | safe upserts (AP-7) |
| FTS5 keyword index | `wiki/fts_index.py`, `search.py`, migration `0004_wiki_fts.sql` | the keyword half of hybrid search |
| Key-aware provider fallback | `wiki/provider_chain.py` | plan + synth LLM calls, embedding-provider chain |
| Live filesystem sync | `wiki/watcher.py` (+ `wiki_ws.py`) | Obsidian connector; live UI updates |
| Contacts / person-pages | `wiki/contact_mirror.py`, `memory/people.py` | entity resolution + graph nodes |
| Candidate journal / audit | `wiki/journal.py`, extraction-audit tables | ingest provenance |
| OAuth + connectors | `jarvis/marketplace/` (registry, token_store, oauth_*, discord/telegram connect, Drive/Calendar/Slack/Notion cards) | L1 connectors — most of the auth is done |
| Channel bots | `jarvis/channels/{discord,telegram}.py` | realtime push connectors |
| External data tools | `plugins/tool/drive_rest.py`, `google_calendar_rest.py` | Drive/Calendar fetch |
| Wiki frontend + graph | `frontend/src/components/wiki/` (`WikiGraph.tsx`, search, renderer), `wiki_routes.py` | the UI to extend into the brain view |
| Episodic memory | `jarvis/awareness/` | complementary time-ordered signal |
| EventBus | `core/bus.py` | ingest queue / event fan-out |

## Genuinely new — build it **[NEW]**

| Piece | Effort | Notes |
|---|---|---|
| **Embedding layer** | **keystone** | capability-gated provider (local Ollama / cloud API) through a key-aware chain; the one thing everything else depends on |
| **Vector index** | small | sqlite-vec on the existing `jarvis.db` (+ pgvector driver for cloud) |
| **Entity/temporal graph** | medium | `entity` + `edge` tables, bi-temporal fields, write-time extraction; or embed Graphiti |
| **Hybrid fusion + rerank** | small–medium | RRF over FTS+vector+graph, optional cross-encoder rerank |
| **Query planner** | small | one LLM call → structured facets (+ entity resolution on read) |
| **RAG synthesizer** | small | evidence → cited answer via `provider_chain` (reuse) |
| **Connector "source" interface** | medium | the plugin contract: *what / how to connect / how to sync*; wrap existing OAuth + add cursor/backfill |
| **New connectors** | per-source | Slack (socket), WhatsApp, GitHub, mail — reuse marketplace auth |
| **Brain UI** | medium | "Ask your brain" search + the graph over all sources + connector dashboard |
| **VisionRAG** | later/opt-in | ColPali multi-vector for images/PDFs |

## Extend (exists, needs a bit) **[EXTEND]**

- `consolidator.py` → also do bi-temporal INVALIDATE on the graph.
- `contact_mirror.py` → also emit graph nodes/edges, not just pages.
- Drive/Calendar tools → add cursor-based incremental sync + backfill.
- `WikiGraph.tsx` → render all-source nodes + semantic neighbourhood, not just
  wikilinks.

## One-line scope summary

> Build the **embedding + vector + graph** core, wrap the **existing OAuth/distill/
> FTS/provider-chain** machinery around it, and add the **planner + fusion +
> synthesizer** on top. ~1/3 new, ~2/3 wiring.
