# 03 · Retrieval & the day-one voice budget

## The read path

```
question
   │
   ▼
PLANNER (one fast LLM pass, 100–200 ms)
   slots: {entities, time window, place, semantic core, likely sources, area}
   │
   ▼
PARALLEL FAN-OUT (SQL, 50–150 ms total — all lists at once)
   keyword FTS  ·  vector ANN  ·  entity graph  ·  time-range  ·  profiles
   │
   ▼
RRF FUSION (pure arithmetic, ~0 ms)
   score(d) = Σ_lists  weight / (60 + rank_list(d))
   dedupe chunks to their source · cap results per source
   │
   ▼
RERANK (small scoring model over top ~20 → keep top ~10)
   │
   ▼
CONTEXT EXPANSION (pull neighboring sections/messages back in)
   │
   ▼
SYNTHESIS (streamed LLM answer with citations)
```

Stage notes:

- **Planner.** Extracts structured slots from the question and picks which
  retrieval lists matter ("Viktoria" → entity lookup; "when" → the answer is a
  date; "dinner" → event-type filter `meal`; active area → SQL prefilter).
  Runs on the fastest configured model tier. If planning fails or times out,
  the fallback is a plain hybrid search over all lists — degraded, never dead.
- **Fan-out.** Every list is a single indexed SQL query against the unified
  store; they run concurrently. Recency decay and term-rarity weighting are
  baked into the keyword/vector list scoring, so stale answers lose ties and
  filler ("sounds good, thanks!") never surfaces on similarity alone.
- **Fusion (RRF, smoothing constant 60).** Consensus beats a single strong
  vote: a document appearing near the top of several lists outranks one that
  is first in only one. Weights are per-list and tunable; defaults to 1.0.
- **Rerank.** A small model scores each candidate against the actual question
  (0–10), killing look-alikes that share vocabulary but answer a different
  question. Uses the configured rerank-capable provider if present; skipped
  honestly when none is configured (fusion order then stands).
- **Context expansion.** Winners are re-hydrated with their surroundings — the
  neighboring wiki sections, the messages around a burst — so the synthesis
  model sees complete evidence, not orphaned fragments.
- **Synthesis.** Streams the answer with inline citations; every claim about
  the user's life must carry at least one evidence permalink. **No evidence →
  say so.** The system answers "I don't have that" rather than inventing a
  plausible dinner date.

## Cross-source reconstruction

The north-star answer often exists in no single row: the chat says "19:00?",
the calendar says "Dinner w/ Viktoria", the photo's metadata says San
Francisco. The event extraction on the write path (doc 02) has already fused
such fragments into `uw_events` rows with absolute time ranges, participants,
and evidence ids — so at read time, episodic questions hit a **precomputed
event**, and the synthesis stage merely verbalizes it with its citations.
Vector search over documents is the safety net for whatever extraction did
not anticipate, not the primary episodic path.

## Voice from day one (D-8): the latency budget

The realtime voice pipeline queries UltraWiki in v1. Budget to first spoken
token, measured not estimated:

| Stage | Budget |
|---|---|
| Planner pass (fast tier, capped) | ≤ 200 ms |
| Parallel fan-out (indexed SQL) | ≤ 150 ms |
| Fusion + rerank (rerank capped or skipped for voice) | ≤ 150 ms |
| Synthesis to first streamed token | ≤ 400 ms |
| **Total to first token** | **≤ 900 ms** |

Rules that make the budget reachable:

1. **Precomputed profiles.** Every entity keeps a stored, continuously
   re-summarized profile ("who is Viktoria", "what is Project X"), refreshed on
   the write path whenever linked items change. Identity and summary questions
   are a **single-row lookup**, no fan-out at all.
2. **Voice degrades stages, never blocks on them.** If rerank or planning
   would bust the budget, voice falls back to fusion order or plain hybrid
   search and still answers; the full pipeline remains available to chat/UI.
3. **Nothing warms up on the hot path.** Store connections, planner prompts,
   and embedding lookups for query text are prewarmed off the boot-critical
   path; a cold UltraWiki answers voice questions honestly ("memory is still
   waking up") instead of stalling the conversation.
4. **Voice answers are spoken-short.** The voice surface gets the answer
   sentence and offers detail on request; the full cited evidence packet
   renders in the UI transcript.

## Surfaces

Per the repo's CLI-first contract, retrieval ships as REST routes (which makes
it `jarvis api` CLI-reachable automatically), plus:

- **Wiki UI (Ultra mode)** — ask-and-answer view with citations, filters by
  area/source/time.
- **Brain tool** — the router/worker brains get a flat `ultrawiki_search`
  tool; agents and missions use the same primitive.
- **Primitive tools, not one oracle.** Following the Cerebras MCP design, the
  building blocks (`search`, `search_source`, `who_is`, `events_between`) are
  exposed individually — cheap, LLM-free, narrow inputs/outputs — so any agent
  can orchestrate them; the synthesized answer endpoint is a composition, not
  the only door.
