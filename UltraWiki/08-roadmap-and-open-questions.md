# 08 · Roadmap & Open Questions

## Build order — each phase is usable on its own

No big-bang. Every phase ships something the user can feel, and the first one
needs **no external connector at all**.

### Phase 0 — Semantics on what's already there
- Add the **embedding layer** + **vector index** (sqlite-vec) on the existing
  distilled wiki pages.
- Add **hybrid search** (FTS5 + vector + RRF) to `wiki/search.py`.
- **Win:** search in today's wiki gets noticeably better. Zero new connectors,
  lowest risk, proves the keystone.
- Touches: `provider_chain`, `search.py`, `fts_index.py`, a new `vector_index`,
  an embed step in `curator.py`.

### Phase 1 — First external connector + the source interface
- Define the **`source` plugin contract** (what / how to connect / how to sync).
- Wire **Google Drive** through it (the tool already exists) with cursor sync +
  a bounded backfill.
- **Win:** first real outside knowledge in the brain; the connector pattern is
  proven end-to-end.

### Phase 2 — Entity/temporal graph + realtime sources
- Add `entity` + `edge` tables, **bi-temporal** fields, write-time entity/relation
  extraction; hook it to contacts.
- Add **realtime** connectors: Slack (Socket Mode), Telegram/Discord (channels
  exist), incremental only.
- **Win:** "who/when/where" questions work; "always up to date" becomes real.
  This is the phase that makes the **Viktoria query** answerable.

### Phase 3 — Full RAG read path + Brain UI
- **Planner → fan-out → RRF → rerank → context expand → synthesize** with
  citations; two-tier (fast for voice / deep for UI).
- Frontend: **"Ask your brain"** search, the **all-source graph**, a **connector
  dashboard** with sync status.
- **Win:** the second brain talks back, in voice and UI, with sources.

### Phase 4 — Scale & platform
- Optional **pgvector** backend for team/cloud.
- **MCP retrieval primitives** (`search_unified`, `graph_lookup`, `who_knows`,
  `timeline`) with no synthesis LLM — Jarvis's memory becomes a source other
  agents (and Jarvis's own workers) can query.
- Optional **VisionRAG** (ColPali) for images/PDFs.
- **Win:** scales past one machine; the brain becomes a platform.

## Rough dependency graph

```
Phase 0 (embeddings+vector) ─┬─► Phase 1 (connector interface)
                             └─► Phase 2 (graph + realtime) ─► Phase 3 (RAG + UI) ─► Phase 4 (scale/MCP/vision)
```

Phase 0 unblocks everything. Phases 1 and 2 can proceed in parallel once 0 lands.

---

## Open questions — only the maintainer can answer

1. **Audience: single-user or teams/cloud too?**
   - Impact: whether to design the pgvector/permission path from the start or
     defer it. *Recommendation:* **local-first, cloud as an open option** — matches
     the privacy-hybrid posture; pgvector is a swap-in, not a rewrite.

2. **Privacy: local or cloud embeddings by default?**
   - Impact: does distilled text ever leave the machine. *Recommendation:*
     **local embeddings default, cloud opt-in per source.** Nothing leaves the box
     unless the user says so.

3. **Where to start: Phase 0 or a flashy connector first?**
   - Impact: fastest visible win vs fastest "wow". *Recommendation:* **Phase 0** —
     lowest risk, improves what exists immediately, and de-risks the keystone
     before any connector work.

4. **How much history to backfill on connect?**
   - All-time vs last N months. Affects one-time cost/time. *Recommendation:*
     a bounded default (e.g. 12 months) with an "import everything" opt-in.

5. **Graph store: hand-rolled SQLite tables or embed Graphiti/Kùzu?**
   - Impact: build effort vs a dependency. *Recommendation:* start with **SQLite
     tables** (no new infra, headless-safe); revisit Graphiti if temporal logic
     gets heavy.

6. **"Mantis" — did you mean a specific tool?**
   - Not found as a known RAG framework. Currently interpreted as the
     temporal/graph memory need (Graphiti/Zep). If a specific product was meant,
     name it and it gets slotted into doc 04.

---

## Definition of done for the whole thing (the north-star test)

Ask, by voice: **"When was I at dinner with Viktoria in San Francisco?"** →
Jarvis returns the **exact date, venue, and sources** in **under ~1 second to
first spoken word**, entirely from the user's own connected data, and does it on
a machine with **only local models** as well as with cloud keys.
