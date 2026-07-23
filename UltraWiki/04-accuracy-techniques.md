# 04 · Accuracy Techniques — how to be *exact* on vague questions

The whole point of UltraWiki over a naïve vector store. Each technique below
attacks a specific failure mode of "just embed chunks and cosine-search". They
stack; the design uses all of them, gated so a keyless install still degrades to
something useful.

| # | Technique | Failure mode it fixes | Cost | Priority |
|---|---|---|---|---|
| 1 | Contextual distillation | chunks lose context; paraphrase misses | ingest-time LLM | **must** |
| 2 | Hybrid search (vector + FTS) | embeddings blur exact tokens | cheap | **must** |
| 3 | Entity resolution | "Viktoria" ≠ a searchable id | contacts reuse | **must** |
| 4 | Temporal / bi-temporal graph | "when" questions; stale facts | graph store | **must** |
| 5 | GraphRAG multi-hop | relational questions ("who did X with Y") | graph store | **high** |
| 6 | RRF fusion | scores across retrievers aren't comparable | trivial | **must** |
| 7 | Reranking | top-k precision | small model | **high** |
| 8 | HyDE / query expansion | very vague queries | 1 LLM call | medium |
| 9 | VisionRAG (ColPali) | screenshots/photos/PDFs, no OCR | vision model | later |
| 10 | Metadata prefiltering | searching everything is slow & noisy | trivial | **must** |

---

## 1 · Contextual distillation (the biggest single win)

Rewrite each unit into a self-contained document and **prepend a context
sentence** before embedding (Anthropic Contextual Retrieval). A raw line "yeah
Trestle at 8" is useless embedded; distilled to "Dinner with Viktoria at Trestle,
San Francisco, 23 Jun 2024, 20:00" it matches the query directly. Measured
**−49% failed retrievals**, **−67%** with reranking. Done on the write path (doc
02), so it's free at query time.

## 2 · Hybrid search (vector + keyword)

Vector finds meaning ("Essen" ≈ "dinner"); FTS5 finds exact strings ("Trestle",
error codes, hostnames, `--flags`) that embeddings smear together. You need both.
Jarvis has FTS5 today; vector is the new half. Merge with RRF (#6).

## 3 · Entity resolution

Collapse every mention/alias of a person/place/thing to one canonical node
(reusing Jarvis contacts). Turns fuzzy text matching into exact id filtering —
the single biggest precision lever for "questions about a specific person". Do it
**on write** (link mentions to contacts) and **on read** (resolve query names to
ids).

## 4 · Temporal & bi-temporal (Graphiti/Zep model)

Every fact and edge carries two timelines:

- **valid-time** — when it was true in the world (`valid_from/valid_to`)
- **ingestion-time** — when we learned it (`ingested_at/expired_at`)

This is what makes "**when** was I with Viktoria" answerable at all, and what lets
"who is my manager" return the *current* answer while "who was my manager in 2023"
returns the historical one. Superseded facts are **invalidated, not deleted**.
Without this, a memory system either forgets or contradicts itself. Graphiti is
the open-source reference implementation (Neo4j-based) if you don't want to build
the temporal layer from scratch.

## 5 · GraphRAG (multi-hop relational retrieval)

Pure similarity can't answer "who did I travel with to the offsite that Anna
organized" — that's three hops. A knowledge graph traverses `Anna →organized→
offsite →attended_by→ {people}`. For UltraWiki the graph is entity-centric
(people/places/events/projects). Microsoft GraphRAG's community-summarization is
overkill for a personal brain; the lightweight entity-graph + traversal is the
right size. The graph is also what returns **exact facts** (a date, a venue) by
lookup instead of by ranking.

> **On "Mantis":** no established RAG framework by that name was found. The intent
> — a graph/temporal memory that answers relational + time questions precisely —
> is exactly techniques #4 and #5, best embodied by **Graphiti/Zep**. If a
> specific tool was meant, flag it and it can be slotted in here.

## 6 · Reciprocal Rank Fusion

Combine retriever outputs by **rank**, not score: `Σ 1/(60+rank)`. No calibration
needed between incomparable scorers. De-facto standard.

## 7 · Reranking

A cross-encoder re-scores the shortlist jointly (query×doc), far more precise than
bi-encoder cosine. Retrieve wide (top ~150), rerank to a tight top-k. Optional
(local cross-encoder / Cohere / LLM-rerank / skip) so it never becomes a hard
dependency.

## 8 · HyDE & query expansion

For thin/vague queries, have the LLM (a) generate a hypothetical ideal answer and
embed that, and/or (b) expand synonyms and the user's language ("Essen" → dinner,
meal, restaurant). Use selectively — strong metadata filters usually make it
unnecessary and it adds a call.

## 9 · VisionRAG (ColPali / ColQwen2) — for later

Screenshots, photos of receipts/whiteboards, and visually-rich PDFs lose their
meaning through OCR. ColPali embeds the **page image** as multi-vector patch
embeddings and scores with late-interaction MaxSim — retrieving figures, tables,
and layout that text extraction misses, no OCR pipeline. Relevant once the brain
ingests images (a photo from the Viktoria dinner, a screenshot of a reservation).
Store the ColPali multi-vector alongside the unit; keep it an opt-in extra
(needs a vision model). Byaldi is the easy wrapper.

## 10 · Metadata prefiltering

Before any similarity work, filter by the resolved facets (participant, place,
time, source, scope). Shrinks the candidate set by orders of magnitude → both
faster (doc 05) and more precise (less room for a semantically-close-but-wrong
hit). Cheap and always on.

---

## How they combine for the north-star query

`Viktoria` (resolve→id) + `SF` (resolve→place) → **prefilter** (#10) → **graph
hop** finds the dinner event with its date (#4/#5) while **vector+FTS** (#1/#2)
confirm the thread → **RRF** (#6) → **rerank** (#7) → **cited answer**. Six of the
ten techniques fire on this one question — that's why it lands the exact date
instead of "some restaurant, maybe".
