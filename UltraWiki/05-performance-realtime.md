# 05 · Performance — fast enough for the realtime voice model

Target: a spoken question returns its **first token in ≲ 800 ms**, so UltraWiki
feels like part of the conversation, not a lookup. The full answer streams after.
This is achievable because the heavy work is pre-computed on the write path.

## The latency budget (read path, personal-scale, local sqlite-vec)

| Stage | Typical | Note |
|---|---|---|
| Plan (LLM) | 50–150 ms | one small/fast call; Cerebras-class inference makes this ~free |
| Entity resolve | < 5 ms | local id lookup |
| Metadata prefilter | < 10 ms | indexed; shrinks the space first |
| Vector search (HNSW) | 5–20 ms | approximate-NN over a filtered subset |
| FTS5 | < 10 ms | already fast today |
| Graph traversal | 5–20 ms | 1–2 hops on an indexed adjacency |
| RRF fuse | < 5 ms | pure arithmetic |
| Rerank | 0–150 ms | optional; skip or small model for voice |
| Context expand | < 20 ms | a few keyed DB reads |
| **Synthesis first token** | 200–400 ms | streaming |
| **Total to first token** | **~400–800 ms** | fan-out stages run in parallel |

The retrievers run **concurrently**, so their wall-clock is the slowest one
(~20 ms), not the sum. The two LLM calls (plan + synth) dominate — which is why
model speed is the lever.

## The seven speed principles

1. **Precompute everything expensive.** Distillation, embedding, entity/graph
   extraction all happen on ingest, in the background. Query time never embeds a
   document or calls an LLM to summarize a source.
2. **Prefilter before similarity.** Metadata filters (participant/place/time) cut
   the vector search space by orders of magnitude. Searching 300 candidates is
   both faster and more accurate than searching 3 million.
3. **ANN index, not brute force.** HNSW (in sqlite-vec / pgvector) gives
   sub-20 ms nearest-neighbour at personal scale and stays sub-100 ms at millions
   of units.
4. **Parallel fan-out.** The four retrievers are independent — fire them at once.
5. **Small models on the hot path, big models off it.** Use a fast planner and a
   fast/streaming synthesizer for voice. Reserve larger models for background
   distillation where latency doesn't matter.
6. **Stream the answer.** For voice, first-token latency is what the user feels.
   Start speaking as soon as synthesis emits.
7. **Cache.** Cache resolved entities, hot query plans, and recent answers. Repeat
   and follow-up questions ("and where exactly?") reuse the retrieved evidence.

## Two-tier retrieval for voice

- **Fast tier (default for voice):** prefilter → vector + FTS + graph → RRF →
  **skip rerank** → synth. ~400–600 ms first token.
- **Deep tier (UI / hard questions):** add HyDE, wider retrieval, cross-encoder
  rerank, more context expansion. ~1–2 s, higher precision.

The planner picks the tier from the question (a crisp entity+place+time question
→ fast tier; an open "tell me everything about my SF trips" → deep tier).

## Scale notes

- **Personal (one user):** sqlite-vec + HNSW handles millions of units on a
  laptop. This is the default and it is enough essentially forever for one person.
- **Cloud / team:** pgvector with an HNSW index, same query shape. Cerebras runs
  15k queries/day on a single Postgres embeddings table — the shape scales.

## Speed is an architecture enabler (the Cerebras thesis)

Fast inference doesn't just make the same pipeline quicker — it makes a **richer**
pipeline affordable. Because plan/distill/rerank/synth are cheap, you can run all
of them and still be realtime. On a slow model you'd have to drop steps and lose
accuracy. This is the direct argument for pairing UltraWiki with the realtime
model tier.
