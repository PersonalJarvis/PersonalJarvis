# Wiki Reliability + Premium RAG — Design

**Date:** 2026-07-07
**Status:** Approved by maintainer (brainstorming session).
Implementation is phased (maintainer decision 2026-07-07): **Pillar A ships
first and alone** (rollout steps 1–3); Pillar B (Premium tier) is deferred to
a later, separate implementation plan.
**Scope:** `jarvis/memory/wiki/*`, wiki tools, wiki routes/UI, Obsidian setup flow

## 1. Problem

On a fresh test machine and on a second Windows box, the wiki silently did
nothing: no note files were created, and explicit "write this to the wiki"
requests were ignored. Obsidian had been connected, yet the user's vault
showed nothing. `docs/diagnostics/fresh-machine-forensics-2026-07.md`
(Bug 12/18) already names the class: the subsystem is engineered to fail
quietly (fire-and-forget, broad `except`, empty-list returns), so on the
maintainer's box (strong tool-calling model + credit) it works, while a fresh
install (weak free default model) degrades into silence — the AP-23 class.

Ranked root causes found in the current code:

1. The weak free default model never calls `wiki-ingest`; the system prompt
   even biases against manual storing (`jarvis/brain/tool_use_loop.py:106-108,
   225-228,644-656`). Explicit user requests therefore do nothing.
2. The conversation feed writes to a SQLite candidate journal, not vault
   files, and consolidates only at >= 8 pending candidates
   (`jarvis/core/config.py:896`) — a short test session produces zero `.md`
   files even when extraction works.
3. Extractor/curator LLM failures (empty salience, malformed JSON, exhausted
   provider chain) are swallowed to `[]` with a log line only
   (`jarvis/memory/wiki/curator_llm.py:368-450`,
   `provider_chain.py:113-123`).
4. "Connect Obsidian" registers Jarvis's own auto-created vault, never the
   user's existing vault (`jarvis/setup/obsidian.py:364`,
   `jarvis/ui/web/setup_routes.py:90-108`).
5. Relative `vault_root` resolves against `Path.cwd()`
   (`jarvis/core/config.py:1789`, `jarvis/ui/web/server.py:2186-2190`).

The read path already exists and is keyword-only: `WikiContextInjector`
prepends FTS5 results to every router turn within an 80 ms budget
(`jarvis/brain/wiki_context.py`, wired at `jarvis/brain/factory.py:1194-1229`),
plus `wiki-recall` / `wiki-page-read` router tools. There are no embeddings.

## 2. Maintainer decisions (fixed)

- **Two user-selectable tiers**, switchable in-app at any time:
  - **Basic** (default): current keyword/FTS5 retrieval. No key, no download.
  - **Premium**: semantic retrieval via **cloud embedding APIs only** — no
    local embedding model ships or downloads.
- Reliability fixes (Pillar A) apply to **both** tiers.
- "Connect Obsidian" lets the user **choose** between writing into their
  existing vault (dedicated Jarvis subfolder) or a separate Jarvis vault; the
  UI always shows the active target.
- Approach: evolve the existing subsystem ("repair + premium"), no greenfield
  rebuild, no external RAG framework.

## 3. Goals / non-goals

**Goals**

- G1: An explicit "write this to the wiki" request produces a visible note
  file on ANY install (any OS, weakest free model, any single key) or an
  honest spoken/written failure message — never silence.
- G2: Jarvis confirms wiki writes only AFTER the file exists on disk.
- G3: Ambient capture becomes visible quickly (small journal threshold +
  idle/time-based flush) without touching voice latency.
- G4: Wiki health (last write, vault path, provider-chain state, tier) is
  visible in the Wiki tab.
- G5: Premium tier adds hybrid (semantic + keyword) retrieval for deliberate
  lookups, degrading honestly to Basic when no embedding provider is
  reachable.
- G6: All of the above passes the three non-maintainer paths of CLAUDE.md §3.

**Non-goals**

- No local embedding model (maintainer decision).
- No change to the note format: plain Markdown, Obsidian-compatible.
- The pre-answer `WikiContextInjector` stays FTS5-only in BOTH tiers (a query
  embedding needs a network round-trip and would blow the 80 ms budget;
  AP-9/AP-26). Semantic search applies to deliberate lookups only.
- No rewrite of curator/extractor/atomic-writer internals beyond the hooks
  named here.

## 4. Design — Pillar A: reliable, honest writing (both tiers)

### A1. Deterministic wiki-intent capture

A small deterministic matcher (module `jarvis/memory/wiki/intent.py`) runs on
the final user transcript in the router pre-pass, alongside the existing
navigation-intent / local-action gates. It recognizes explicit wiki-write
commands in all supported languages (de/en/es token sets are
speech-recognition input vocabulary — closed-list category 3), e.g.
"schreib das ins Wiki", "merk dir … im Wiki", "write/save … to the wiki",
"guarda … en la wiki". <!-- i18n-allow: input vocabulary the matcher must contain -->

- On match, the turn bypasses model tool-choice entirely: the ingest pipeline
  is invoked directly **through `ToolExecutor.execute()`** (AP-3) with the
  `wiki-ingest` tool (risk tier stays `monitor`).
- Content resolution: if the utterance carries inline content beyond the
  trigger phrase, ingest that; if it is anaphoric ("write THAT down"), ingest
  the last user/assistant exchange as the source snippet (the curator already
  accepts conversation snippets).
- Negative guard: the matcher requires an explicit wiki/notes object in the
  phrase; plain "merk dir das" (no wiki reference) keeps today's behavior
  (memory/ambient path) to avoid false positives. <!-- i18n-allow: input vocabulary example -->
- The LLM tool path stays available as-is; the matcher is an additional,
  model-independent trigger, mirroring the wake-word philosophy: explicit
  user intent must never depend on model goodwill.

### A2. Immediate write for explicit ingest

`wiki-ingest` calls (from the matcher OR the model) skip the candidate
journal: the tool awaits the curator ingest, and it does not return until
`AtomicWriter.apply` has completed (or failed). The tool result carries the
written page path(s). The 2026-07-06 honesty fix (`success=False` on no-op)
stays and becomes load-bearing.

### A3. Confirmation only after the write

The spoken/written confirmation for an explicit wiki command is produced
AFTER the tool result reports success, from the actual outcome (page title /
path), localized via `resolve_output_language` — same pattern as the
deterministic Computer-Use readbacks. Phrasing follows the "no canned voice
phrases" rule: contextual flash-LLM line when a flash provider is available,
small all-language phrase pool as keyless fallback. A failed write produces
an equally honest failure line naming the cause category and the in-app fix
(e.g. "no provider reachable — check keys in Settings").

### A4. Ambient feed tuning

- `consolidate_after_candidates`: default 8 → **1**. A reviewed durable fact
  becomes visible without waiting for unrelated future conversation; concurrent
  candidates may still be consolidated together.
- New time/idle flush: the existing `IdleEntered` trigger additionally drains
  the journal when the oldest pending candidate exceeds a configurable age
  (default 10 min), regardless of count.
- The ambient path stays fire-and-forget for the voice pipeline (AP-9); its
  failures surface via A5, never via interruptions.

### A5. Wiki health surface

- New `GET /api/wiki/health` (mounted in `wiki_routes.py`; CLI coverage per
  `check_cli_coverage.py`, e.g. `jarvis wiki health`): tier, active vault
  path, curator bootstrapped y/n, last write (ts + ok/error + cause), provider
  chain state, journal backlog size, embedding index stats (Premium).
- Wiki tab renders it as a compact status panel; ERROR states are visible
  without opening logs.
- Existing telemetry events (`wiki_all_providers_failed`, …) feed this
  surface instead of dying in the log.

### A6. Obsidian connect with vault choice

- The connect flow (`setup_routes.py` + `ObsidianSetupDialog`) gains a step:
  **[use existing vault]** (Jarvis writes into `<vault>/Jarvis/`; vault list
  read from `obsidian.json`) or **[create separate Jarvis vault]** (today's
  behavior). The choice updates `[wiki_integration].vault_root` atomically
  via `config_writer` (AP-7).
- After connect, UI + health panel always display the active target.
- Existing-vault mode constrains ALL wiki writes to the `Jarvis/` subtree
  (guard in `AtomicWriter`) so Jarvis can never touch foreign notes; the FTS
  boot index reindexes on vault switch.
- Obsidian detection stays Windows-registry/exe-probe based where available
  and degrades to a quiet, clearly-messaged no-op elsewhere; vault choice
  works purely on paths and must function on macOS/Linux too (obsidian.json
  lives under the platform config dir; discovery uses `pathlib` +
  capability probes, no hardcoded user paths).

### A7. CWD-independent vault root

Relative `vault_root` resolves against the repo/app root anchor from
`jarvis/core/paths.py` (import-time absolute), never `Path.cwd()`. Migration:
if the legacy CWD-resolved vault exists, is non-empty, and differs from the
new anchor resolution, keep using the populated one and flag the ambiguity in
the health panel instead of silently forking the vault.

## 5. Design — Pillar B: Premium semantic retrieval

### B1. Tier setting

`[memory.wiki].tier = "basic" | "premium"` (default `"basic"`), exposed as a
toggle in the Wiki/Settings view, changed at runtime via the existing config
writer + reload path. The value crosses Python ↔ TS ↔ UI (and SQLite index
metadata), so it follows the five-layer anti-drift pattern with a parity test
(AP-4 / BUG-008 class).

### B2. Embedding provider chain

`jarvis/memory/wiki/semantic/embedder.py` mirrors
`provider_chain.py`: a key-aware, cross-family chain over cloud embedding
APIs (initial families: OpenAI, Gemini, Voyage, Cohere, Mistral; extensible
via the provider registry). Gated on a new capability flag
`supports_embeddings` (AP-21) — never on provider names in call sites.
Dead/keyless providers are skipped; when NO family is reachable, Premium
degrades to Basic behavior with an honest health-panel state and a one-time
notice (AP-22). Chain order follows the same key-awareness rules as the wiki
LLM chain; the embedding model per family is a curated default in the model
catalog, overridable in config.

### B3. Vector store

New migration `jarvis/memory/migrations/0006_wiki_embeddings.sql`: one table
(`page_chunk_embeddings`: chunk id, page path, chunk text hash, provider +
model id, dimension, vector BLOB, updated ts) in the existing SQLite DB.
Query = brute-force cosine over numpy arrays loaded lazily — no new heavy
dependency, adequate for a personal vault (tens of thousands of chunks).
Chunking: per Markdown section (heading-bounded), consistent with FTS rows.
Vectors from different provider/model ids are never mixed in one query; a
provider/model switch marks old rows stale for re-embedding.

### B4. Hybrid retrieval

In Premium, `wiki-recall` (and any deliberate/background lookup, e.g. worker
research) runs FTS5 AND vector search, fused via Reciprocal Rank Fusion, and
returns the merged top-k with the same result schema as today (callers are
unaffected). Basic tier and the pre-answer injector keep pure FTS5. Query
embedding failures (timeout/4xx) degrade that single query to FTS5 silently
for the caller but count toward chain health.

### B5. Indexer + backfill

Embedding happens: (a) on successful page writes (post-`AtomicWriter`, async,
off the voice path), and (b) as a background backfill when the tier switches
to Premium or stale rows exist — resumable, rate-limited, with progress
(x/y pages) in the Wiki tab health panel. Backfill respects AP-26: it starts
from `_heavy_backend_bg`/post-ready hooks only, never the boot critical path.

## 6. Error handling — "honest, not silent"

| Failure | Behavior |
|---|---|
| Explicit command, write fails (chain dead, curator not bootstrapped, no-op) | Honest localized failure reply naming cause category + in-app fix; never a success phrase; health panel updated |
| Explicit command, curator registry `None` | Same as above ("wiki not ready"), plus health panel shows bootstrap error cause |
| Ambient extraction/consolidation fails | Never interrupts conversation; health panel + telemetry record it |
| Premium selected, no embedding family reachable | Immediate honest notice at switch time; retrieval transparently runs Basic; recoverable in-app (key entry) |
| Single query embedding fails | That query falls back to FTS5; chain health counts the failure |
| Headless Linux / no Obsidian / no keyring | All wiki paths boot and run; Obsidian features report "not available here" as quiet no-ops; credential storage uses the standard keyring→ENV→file fallback |
| Vault path ambiguity after upgrade (A7) | Populated legacy vault wins; ambiguity flagged in health panel |

## 7. Testing

- **Fresh-machine end-to-end (the anchor test):** empty vault, fake weak
  model (no tool calls), one fake key of an arbitrary family → user says
  "merk dir X im Wiki" → note file exists, confirmation emitted only after
  the write; also the failure twin (all providers dead → honest failure
  reply, no file, no success phrase). <!-- i18n-allow: quoted trigger utterance under test -->
- **Intent matcher unit tests** (de/en/es): positive commands, anaphoric
  commands, and must-NOT-fire negatives (no wiki object, mid-sentence
  mentions). Fixtures follow closed-list category 4.
- **Embedding chain with fakes** (`tests/fakes/`, no `unittest.mock`): first
  family dead → crosses family; all dead → Basic degradation + health state;
  provider/model switch → stale-row re-embed.
- **Hybrid fusion:** RRF ordering, schema-compatibility with existing
  `wiki-recall` consumers, per-query FTS5 fallback.
- **Paths:** vault root resolution is CWD-independent on Windows AND POSIX;
  legacy-vault migration preference; existing-vault mode never writes outside
  `Jarvis/`.
- **Tier parity test** across Python/TS/UI layers (five-layer pattern).
- **Obsidian connect:** both vault choices update config atomically; POSIX
  hosts without Obsidian degrade quietly (extend
  `tests/unit/setup/test_obsidian_register.py`).
- **Headless boot:** wiki + Premium-off boot on `python:3.11-slim` profile
  stays green; backfill never runs on the boot critical path (boot-budget
  gate).
- **§3 definition of done:** all three non-maintainer paths verified before
  the feature is called done.

## 8. Rollout order (feeds the implementation plan)

1. A7 vault root + A6 Obsidian vault choice (kills the "I see nothing" trap).
2. A1–A3 explicit command path with honest readback (kills the reported bug).
3. A4 ambient tuning + A5 health surface.
4. B1 tier setting (parity-tested), B2 embedder chain, B3 store + migration.
5. B4 hybrid recall, B5 backfill + progress.

Each step lands independently green; steps 1–3 are shippable without any
Premium code.
