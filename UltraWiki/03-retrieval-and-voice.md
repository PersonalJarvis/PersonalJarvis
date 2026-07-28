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
  **Binding order of implementation:** a deterministic slot extractor (regex +
  contact/area lookup + time-expression parsing) is the DEFAULT planner; the
  LLM pass is the optional upgrade on top. On a keyless or headless install an
  LLM-only planner is a dead path, and inside the voice budget it is the most
  expensive stage — the deterministic floor keeps both alive.
- **Fan-out.** Every list is a single indexed SQL query against the unified
  store; they run concurrently. Recency decay and term-rarity weighting are
  applied to the fused candidates, so stale answers lose ties and filler
  ("sounds good, thanks!") never surfaces on similarity alone.
  **Shipped (2026-07-25):** term rarity is computed from the store's own
  corpus (`term_document_frequency` + `live_item_count`, both backends), and a
  candidate covering none of the query's rare vocabulary is scaled DOWN, never
  dropped — a hard content filter is the AP-27 trap. Age decay is
  `0.5 ** (age_days / [ultrawiki].recency_half_life_days)`, default 180 days,
  `0` disables it.
- **Fusion (RRF, smoothing constant 60).** Consensus beats a single strong
  vote: a document appearing near the top of several lists outranks one that
  is first in only one. Weights are per-list and tunable
  (`[ultrawiki].rrf_keyword_weight` / `rrf_vector_weight`); default 1.0.
- **Rerank.** A small model scores each candidate against the actual question
  (**0–10, the same scale for every backend**), killing look-alikes that share
  vocabulary but answer a different question. Skipped honestly when no
  provider is configured or ready (fusion order then stands).
  **Universality (§3/AP-22, shipped 2026-07-25):** the default backend is
  `llm`, which grades through the key-aware cross-family provider chain — the
  same one distillation uses — so the stage works with whatever credential the
  install actually holds, including a purely local Ollama on a headless box.
  `voyage` / `cohere` remain available for installs holding those keys; their
  native 0–1 relevance is normalized ×10 onto the shared scale, because the
  relevance floor below must not depend on which backend answered.
- **Context expansion.** Winners are re-hydrated with their surroundings — the
  neighboring wiki sections, the messages around a burst — so the synthesis
  model sees complete evidence, not orphaned fragments.
- **Synthesis.** Streams the answer with inline citations; every claim about
  the user's life must carry at least one evidence permalink. **No evidence →
  say so.** The system answers "I don't have that" rather than inventing a
  plausible dinner date.

## The relevance floor (binding — the "Bugatti case" lesson)

RRF scores are **ordinal**: they rank candidates against each other but can
never say "nothing here is actually relevant" — a fusion over garbage still
produces a confident-looking top result. For the Ask view that is acceptable
(the user explicitly searched and sees the evidence). For every **unsolicited
surface** — context injection into the brain prompt, voice answers that
volunteer personal facts, proactive summaries — it is the defect class the
normal wiki already shipped and fixed once (2026-07-25, the relevance-gate
commit): private facts surfacing in answers nobody asked for. Binding rule:
before UltraWiki results reach any surface the user did not explicitly query,
they must pass an **absolute relevance gate** (the wiki's three-gate pattern:
score floor on the underlying leg scores, query-term overlap, and an
honest empty-result path), never the bare fusion ranking.

**Mechanism (shipped 2026-07-25).** The rerank grade IS the absolute measure
the fused score cannot provide. `hybrid_search(..., enforce_floor=True)` drops
every candidate graded below `[ultrawiki].rerank_min_score` (default 4.0, `0`
disables) and returns an empty list rather than a weak match; explicit
surfaces (Ask view, `GET /api/ultrawiki/search`, the CLI) never pass the flag.
Two honesty rules that must not be softened:

1. A candidate the rerank stage never graded (stage off, provider down, or
   below the rerank pool) arrives with `rerank_score=None` and is passed
   through UNGRADED rather than silently admitted as "good enough" — a floor
   that disappears on provider failure is worse than a visible one. The
   caller's deterministic gate (`jarvis/brain/wiki_relevance.py`) stays
   responsible for those.
2. The floor never runs on an explicit search. The user asked; hiding evidence
   from them is a different defect than volunteering it unasked.

**Wiring status:** the flag exists and is tested, but no unsolicited surface
consumes UltraWiki yet — `wiki_relevance` still guards the normal wiki vault.
Whoever wires UltraWiki into context injection or voice MUST pass
`enforce_floor=True`; that is the moment this section becomes load-bearing.

## Cross-source reconstruction

The north-star answer often exists in no single row: the chat says "19:00?",
the calendar says "Dinner w/ Viktoria", the photo's metadata says San
Francisco. The event extraction on the write path (doc 02) has already fused
such fragments into `uw_events` rows with absolute time ranges, participants,
and evidence ids — so at read time, episodic questions hit a **precomputed
event**, and the synthesis stage merely verbalizes it with its citations.
Vector search over documents is the safety net for whatever extraction did
not anticipate, not the primary episodic path.

**Shipped (2026-07-28): the event leg.** Events reach the read path as a
THIRD ranked list beside keyword and vector — its own keyword index over a
stored card that carries the absolute date in several written forms, the
place and the participants (FTS5 on SQLite, a generated `tsvector` on
Postgres). Consequences:

- It is fused, never privileged: `[ultrawiki].rrf_event_weight` (default 1.0,
  `0` silences the leg) enters the same RRF sum as every other list, so a
  precomputed event still has to win on consensus (principle 5). What the leg
  DOES get is the representative slot when an event and its evidence item
  merge — an event card states the date, the place and who was there, which
  is the better citation for an episodic question than a fragment of chat.
- Event hits are ranked and age-decayed by the event's own `occurred_at`, not
  by when the message that mentioned it was written.
- The leg is optional in the strict sense: a store without the episodic
  tables (a third-party backend, a corpus that predates them) returns nothing
  and every other leg answers unchanged.
- `events_between` ships as the design's primitive
  (`GET /api/ultrawiki/events`, `GET /api/ultrawiki/events/{id}`, and
  therefore `jarvis api ultrawiki list-events` / `get-event`) — cheap,
  LLM-free, narrow in and out.

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
