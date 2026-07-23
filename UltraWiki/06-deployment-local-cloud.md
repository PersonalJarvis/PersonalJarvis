# 06 · Deployment — local *and* cloud, every layer

Hard requirement (from the maintainer and from AP-22/23): the whole thing runs
**fully local** on a normal machine and a headless Linux box, and **scales to
cloud** when wanted — provider-agnostic, key-aware, degrading honestly. Nothing is
load-bearing on a single vendor.

## The matrix — each layer has both a local and a cloud path

| Layer | Local (private · free · default) | Cloud (stronger · costs) | Degrades to |
|---|---|---|---|
| **Embeddings** | Ollama model (nomic-embed, bge, gte) — offline, nothing leaves the box | OpenAI, Gemini, Voyage, Cohere embeddings | **Normal Wiki** (FTS5 only), stated honestly |
| **Vector store** | sqlite-vec on `data/jarvis.db` — no server, headless-ok | Postgres + pgvector | local is enough for one user ~forever |
| **Entity/temporal graph** | tables in the same SQLite DB (or embedded Neo4j/Kùzu) | Neo4j / managed Graphiti | metadata-only retrieval (no multi-hop) |
| **Reranker** | small local cross-encoder, or none | Cohere Rerank / LLM-rerank | RRF without rerank |
| **Synthesis brain** | local LLM via Ollama | existing `provider_chain` (OpenRouter, Gemini…) | raw ranked evidence, no prose |
| **VisionRAG** | local ColQwen2 (opt-in extra) | hosted vision model | text-only retrieval |

Everything routes through **one key-aware chain** (the pattern
`provider_chain.py` already implements): try the configured provider, else any
other credential-ready provider, else degrade. New providers auto-join; no
allowlist. Gate on **capability** ("can embed", "can rerank"), never a provider
name (AP-21).

## Privacy posture (matches Jarvis's "STT local, brain cloud" hybrid)

"Pull everything in" means Slack/WhatsApp/mail/docs live — **distilled and
embedded** — in the local DB. Two honest options, user's choice:

- **Local embeddings (recommended default):** the distilled text is embedded on
  the user's own machine; **nothing leaves the box**. Best privacy, slightly lower
  retrieval quality, zero cost.
- **Cloud embeddings (opt-in):** the *distilled* text (not raw messages) is sent
  to an embedding API. Better quality, a per-token cost, and a deliberate privacy
  trade the user opts into per source.

Raw messages are never stored (only `raw_ref`), so even the local DB holds
summaries + vectors, not full transcripts.

## Headless / VPS

Base install must boot on `python:3.11-slim` with no GPU, keyring, or audio:
- sqlite-vec is pure SQLite — works.
- Local embeddings need a model; on a tiny VPS, either use a small CPU embedding
  model or configure a cloud embedding key. If neither is present, UltraWiki
  **cleanly reports "not configured" and Normal Wiki keeps working** — never a
  crash, never a silent brick.

## Cost shape

- **One-time backfill:** embedding the historical import. Bounded, rate-limited,
  runs in the background. Local model = free (just time); cloud = a one-off token
  bill proportional to history size.
- **Ongoing:** only deltas are embedded — cheap. A few distillation + embedding
  calls per new thread/doc.
- **Query:** two small LLM calls (plan + synth) + index lookups. Negligible.

The zero-cost, zero-key configuration (local everything) is a first-class,
fully-supported path — not a degraded afterthought.
