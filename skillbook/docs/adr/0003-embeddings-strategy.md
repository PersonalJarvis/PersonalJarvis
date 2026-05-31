# ADR-0003: Embeddings — sentence-transformers with deterministic hash fallback

**Status:** Accepted
**Date:** 2026-05-26

## Context

The goal pre-decides: "Embeddings: sentence-transformers all-MiniLM-L6-v2 (local)". This 384-dim model is ~80 MB. Test verification re-runs in `/tmp` from a fresh checkout: model download requires internet, takes 5-30 seconds, and fails silently if Hugging Face is unreachable. We need a deterministic offline path.

## Decision

Define an `Embedder` `Protocol` with one method: `embed(text: str) -> np.ndarray` returning `float32` of fixed dim 384. Two implementations live behind it:

1. **`SentenceTransformersEmbedder`** — lazy-imports `sentence_transformers` only when constructed. Used in production and when the model cache is warm.
2. **`HashEmbedder`** — deterministic, offline, fast. Takes the SHA-256 of the text, seeds `np.random.default_rng`, draws a 384-dim unit vector. Semantically meaningless beyond exact-string equality, but stable across runs and platforms. Sufficient for unit and capstone tests because the Curator's deduplication threshold can be set such that hash-collisions never cross it.

A factory `default_embedder()` picks `SentenceTransformersEmbedder` if `sentence_transformers` imports successfully *and* the model is reachable; otherwise returns `HashEmbedder`. Tests force `HashEmbedder` via a fixture to keep them offline and fast.

## Consequences

- Tests pass without network access and without the 80 MB model.
- Production agents get true semantic deduplication when they install the `[embeddings]` extra.
- The Curator's similarity threshold is set as a configuration parameter, not a hardcoded magic number — tests can tune it.

## Alternatives considered

- **Mandatory sentence-transformers**: rejected — would make capstone re-run in `/tmp` flaky.
- **fastembed (ONNX runtime)**: similar weight, similar issues, adds an onnxruntime dependency.
- **OpenAI embeddings API**: violates the offline default and adds a paid dependency.
