# 03 · Retrieval — the Read Path (fast + exact)

This is the live path. It must return a cited, exact answer in a budget the
**realtime voice model** can live with (first token well under ~1 s). The trick is
that all the expensive work (distilling, embedding, graph-building) already
happened on the write path — the read path is mostly index lookups plus two small
LLM calls (plan + synthesize).

```
 QUESTION
   └─(1) PLAN (LLM)      → {entities, place, time-window, semantic core, sources}
   └─(2) RESOLVE ENTITIES → "Viktoria" → contact:viktoria-m ; "SF" → place node
   └─(3) FAN-OUT (parallel, all metadata-prefiltered)
          ├ vector search (meaning)          ┐
          ├ FTS5 search (exact tokens)        │→ candidate sets
          ├ graph traversal (multi-hop)       │
          └ temporal filter (time-window)    ┘
   └─(4) FUSE (RRF)      → one ranked list
   └─(5) RERANK (cross-encoder) → top few
   └─(6) EXPAND CONTEXT  → pull full thread / calendar event / photo
   └─(7) SYNTHESIZE (LLM) → cited answer
```

---

## (1) Plan — turn a vague question into structured facets

A single fast LLM call converts the question into a query object. This is where
"even if the user describes it imprecisely" is handled.

For **"Wann war ich mit Viktoria beim Essen in San Francisco?"**: <!-- i18n-allow: quoted German example query — the specimen being traced through the pipeline -->

```json
{
  "intent": "temporal_lookup",         // the answer is a DATE
  "entities": { "person": ["Viktoria"], "place": ["San Francisco"] },
  "activity": "dinner | restaurant | essen",
  "time_window": null,                  // unspecified → all-time
  "sources": ["whatsapp", "slack", "calendar"],
  "semantic_core": "dinner with Viktoria in San Francisco"
}
```

Optional **HyDE** (Hypothetical Document Embeddings): for very vague queries, the
LLM also writes a fake ideal answer and embeds *that* — it often matches the real
distilled unit better than the raw question. Use only when the plan is thin;
skip it when metadata filters are strong (they usually are here).

---

## (2) Resolve entities — the accuracy multiplier

"Viktoria" is ambiguous text; the graph needs a **node id**. Resolve via the
contact store (alias + embedding match) → `contact:viktoria-m`. "San Francisco" →
place node. Now retrieval can **filter on ids**, not fuzzy strings — which is the
difference between "restaurants that sound like something Viktoria-ish" and
"units where participant = Viktoria AND place = San Francisco".

Jarvis already has contacts and person-pages — this reuses them.

---

## (3) Fan-out — four retrievers in parallel, all pre-filtered

The metadata filter (`participants ⊇ {Viktoria} AND place ≈ SF`) runs **first**
and shrinks the search space from years-of-everything to a handful of candidates.
Then the four retrievers run **concurrently**:

| Retriever | Finds | Why it's needed here |
|---|---|---|
| **Vector** | meaning: "dinner ≈ essen ≈ restaurant reservation" | catches paraphrase; the user's word "Essen" ≠ the thread's word "reservation" |
| **FTS5** | exact tokens: "Viktoria", "San Francisco", "Trestle" | nails proper nouns embeddings blur |
| **Graph** | `Viktoria —had_dinner_with→ event —in→ SF` with its date | this is what actually produces the exact date, by traversal not similarity |
| **Temporal** | orders/filters candidates by `valid_from` | if the user *had* said "in June" this narrows hard |

The graph retriever is the star of this particular query: a two-hop traversal
from the resolved Viktoria node to dinner-events located in SF returns the event
with its `valid_from` date directly — no ranking needed to *find* it, only to
confirm it.

---

## (4) Fuse — Reciprocal Rank Fusion

Merge the four ranked lists into one with RRF: `score(d) = Σ 1/(k + rank_i(d))`,
`k=60`. RRF needs no score calibration between retrievers (a vector cosine and a
BM25 score aren't comparable) — it only uses ranks, which is why it's the
production standard. Anthropic's default leans ~80% semantic / 20% keyword;
tunable.

---

## (5) Rerank — precision on the shortlist

Pass the top ~50–150 fused candidates through a small **cross-encoder reranker**
that scores query-vs-document jointly (0–10) and keep the top few. Anthropic:
reranking is what takes contextual retrieval from −49% to **−67% failed
retrievals**. Local option: a small cross-encoder; cloud option: Cohere Rerank;
zero-key fallback: an LLM-rerank through `provider_chain`, or skip reranking (RRF
alone is decent). Keep it optional so a keyless install still works.

---

## (6) Expand context

For the surviving top hits, pull the **neighbourhood**: the full WhatsApp thread,
the linked calendar event, the venue's other mentions, an attached photo. This
gives the synthesis LLM complete evidence instead of a snippet, so it can state
the exact venue and cross-confirm the date across two sources.

---

## (7) Synthesize — cited answer

The synthesis LLM (via `provider_chain`) gets the plan + expanded evidence and
writes:

> **"On 23 June 2024 you had dinner with Viktoria at Trestle in San Francisco."**
> Sources: WhatsApp thread (23 Jun 2024) · Calendar 'Dinner w/ Viktoria'.

Rules: **never answer beyond the evidence** (no hallucinated dates — if only a
month is known, say the month), always cite, and honour the runtime output
language resolver so voice answers come back in the conversation's language.

For **voice/realtime**, the answer streams token-by-token; first token is the
latency that matters, not the full answer (see doc 05).

---

## Two interfaces (like Cerebras)

- **Full pipeline** — the above, for the user asking Jarvis (voice or UI).
- **Retrieval primitives (MCP)** — expose `search_unified`, `graph_lookup`,
  `who_knows`, `timeline` as MCP tools with **no synthesis LLM**, so external
  agents (or Jarvis's own workers) can pull raw evidence fast. This is doc 08's
  Phase 4 and turns the personal brain into a platform.
