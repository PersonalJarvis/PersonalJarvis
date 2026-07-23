# 02 · Ingestion — the Write Path ("always the latest data")

This is where "it always pulls the newest data" is earned. The goal: the moment
someone posts in a Slack/WhatsApp thread, that knowledge is in the brain within
seconds — **without** re-scanning the whole history each time.

```
 SOURCE CHANGES ─► (1) TRIGGER ─► (2) FETCH DELTA ─► (3) DISTILL(LLM)
        ─► (4) EXTRACT entities/relations/time(LLM) ─► (5) EMBED
        ─► (6) UPSERT unit + graph edges ─► (7) CONSOLIDATE (add/update/invalidate)
```

Steps 3–7 are exactly Jarvis's existing `extractor → consolidator → curator →
atomic_writer` pipeline, plus an embed step. So the write path is mostly reuse.

---

## (1) Trigger — three ways to know something changed

The connector declares **how it learns about changes**. Pick per source; prefer
push over poll.

| Strategy | How | Best for | Jarvis today |
|---|---|---|---|
| **Push / realtime** | the source calls us: webhook or a socket the bot holds open | Slack (Socket Mode WS), GitHub/Notion (webhooks), Telegram/Discord (bot gateway), Obsidian (filesystem watcher) | `channels/` bots + wiki `watcher.py` **[HAVE]** |
| **Cursor poll (CDC)** | we ask on an interval, but only for items **after a stored cursor** (sync token / page token / IMAP `IDLE`) | Google Drive (changes feed), Calendar (sync tokens), e-mail | Drive/Calendar tools exist **[EXTEND]** |
| **Backfill (one-time)** | on first connect, import bounded history, rate-limited, in the background | every source, once | **[NEW]** |

**"Actions" the user asked about** = this trigger layer. In practice it is a mix
of webhooks/sockets (instant) and cursor-based polling (for sources that don't
push). The important part is **incremental**: every source stores a
`last_cursor` / `last_seen_ts`, and each run only touches the delta.

### Idempotency & completeness (two Cerebras tricks worth copying)

- **Re-fetch the whole thread on any reply.** When one new Slack/WhatsApp message
  arrives, fetch the *entire parent thread* and store it as **one** unit keyed by
  `external_id = thread_id`. An update replaces the row — the unit is always
  complete, never a pile of fragments.
- **`external_id` upsert.** Same source item → same row, always. Reprocessing is
  safe; no duplicates.

---

## (2) Fetch delta

The connector returns raw items for the changed `external_id`s. Raw text is
**transient** — used for distillation, then dropped (only `raw_ref` is kept). This
keeps the store small and side-steps storing giant transcripts.

Durability: enqueue fetched items on a work queue so bursts (a busy channel)
don't drop and don't block. Jarvis's `EventBus` + a lightweight persistent queue
covers this; a crash mid-ingest resumes from the cursor.

---

## (3) Distill — the single biggest accuracy lever

Do **not** embed raw messages. An LLM rewrites each unit into a normalized
document with fixed fields:

- `one_line` — the question this unit answers ("Dinner plan with Viktoria in SF")
- `summary` — 2–4 sentences, self-contained
- `resolution / outcome` — what was decided/what happened
- `entities` — people, places, orgs, dates mentioned
- `refs` — systems, files, links referenced

Then, per **Anthropic Contextual Retrieval**, prepend a short **context prefix**
("This is a WhatsApp thread between Ruben and Viktoria, June 2024, about…") to the
text *before* embedding. Anthropic measured **−49% failed retrievals** from this
alone, **−67%** combined with reranking. This is cheap and it is where most of the
accuracy comes from.

> Speed note: distillation is many small LLM calls. This is exactly why Cerebras
> stresses that **fast inference enables the architecture** — with a fast model
> you can afford to distill everything. Distillation is off the hot path (runs in
> the background on ingest), so it never touches query latency.

---

## (4) Extract entities, relations, time (the graph write)

The same or a second LLM pass emits graph triples with timestamps:

```
(Ruben)—[had_dinner_with]→(Viktoria)  valid_from=2024-06-23  src=wa#123  conf=0.9
(event#88)—[at_venue]→(Trestle, San Francisco)  valid_from=2024-06-23
```

- **Entity resolution on write:** "Viktoria" is linked to the existing Contact
  (alias match + embedding similarity) so all her mentions collapse to one node.
  Jarvis already builds person-pages from contacts (`contact_mirror.py`) — extend
  it to graph nodes.
- **Bi-temporal (Graphiti):** store both *when the fact was true* (`valid_from/to`)
  and *when we learned it* (`ingested_at/expired_at`). When a later message
  contradicts an earlier fact, **invalidate** the old edge (set `valid_to`),
  don't delete it. History stays answerable.
- **Relative-time resolution:** "last Tuesday" is resolved to an absolute date
  using the message's own timestamp as the reference point.

---

## (5) Embed

`distilled_text` (+ context prefix) → dense vector via the configured embedding
provider (local Ollama model or a cloud API — see doc 06). Only changed units are
re-embedded, per commit/edit. Optionally also compute a **ColPali multi-vector**
for image/screenshot/PDF units (doc 04, VisionRAG).

---

## (6) Upsert

One transaction writes: the `memory_unit` row (updating the vector, FTS, metadata,
bi-temporal fields) and the graph edges. `atomic_writer.py` already does
lock+tempfile+BOM-safe writes for the vault (AP-7) — the DB upsert follows the
same atomic discipline.

---

## (7) Consolidate

Jarvis's `consolidator.py` already judges **ADD / UPDATE / NOOP / INVALIDATE**
against existing knowledge. UltraWiki extends this to the graph: a new fact that
contradicts an old one triggers **INVALIDATE** (bi-temporal supersede) rather than
a silent overwrite. This is what keeps the brain consistent instead of
accumulating contradictions.

---

## End-to-end example (one WhatsApp message triggers all of this)

1. Viktoria replies in the SF-dinner thread → **Socket push**.
2. Jarvis re-fetches the **full thread** (external_id = `wa#123`).
3. LLM distills → `one_line: "Dinner with Viktoria at Trestle, SF, 23 Jun"`.
4. Extract → edges `(Ruben)-[had_dinner_with]->(Viktoria) @ SF, 2024-06-23`.
5. Embed the distilled doc (+ context prefix).
6. Upsert row `wa#123` (replaces the earlier version) + edges.
7. Consolidate: no conflict → **UPDATE**. Done, in seconds. The brain can now
   answer the north-star question.
