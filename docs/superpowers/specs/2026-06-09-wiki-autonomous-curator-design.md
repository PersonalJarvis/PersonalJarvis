# Wiki Autonomous Curator — Redesign Design Spec

**Date:** 2026-06-09
**Status:** Approved direction (maintainer delegated detailed decisions; final acceptance is an end-to-end test of the running feature)
**Author:** brainstorming session + 15-agent deep dive (workflow `wf_5bc187bb-27d`)
**Supersedes behaviour of:** the awareness-episode → `SessionRollupWorker` → wiki feed; amends the curator selection model under ADR-0013 / ADR-0014.

---

## 1. Problem (verified by deep dive)

The Knowledge Wiki was designed as an **autonomous long-term memory** — "the LLM is the wiki's editor; knowledge is compiled once and maintained continuously" (ADR-0013) — but in production it behaves as a **desktop-activity logger plus 16 frozen hand-seeded demo pages**. Every load-bearing claim below was adversarially re-verified against source (`confirmed`):

1. **No real selection layer.** The only "is this worth remembering" gate is one sentence in the curator's system prompt — `"When in doubt: write the note"` (`jarvis/memory/wiki/prompt.py:72-74`). No score, no NER, no embeddings, no confidence threshold on the write path.
2. **Wrong autonomous source.** Session pages are built from awareness L2 episodes whose only raw signal is **window focus** (process name + window title), summarised by a chain of 2–3 LLM passes, each token-capped. The rollup cap (`max_output_tokens=600`, `config.py:610-618`) cuts the paragraph mid-sentence. Result: **39 of 57 session pages (68%) truncated**; content-free "user was in Windows Terminal" notes; one page leaked the prompt template as its body (`_archive/sessions/2026-06-02-rkffieuk.md`).
3. **The value-creating hop was never built.** The designed flow episodes → sessions → durable entity/concept/project pages stops at "write session page". `session_rollup.py` has no promotion step; `log.md` is 100% `merge | session rollup` with **zero autonomous entity/concept/project creates** since the 2026-05-12..15 seed window.
4. **Structured-profile capture was removed, not replaced.** The legacy Curator (autonomous `data/workspace/{USER.md,SOUL.md,people/*.md}` structured clusters) was soft-disabled 2026-05-17; its replacement only writes free-form prose, and a separate **brain-must-consciously-call** `update-profile` tool was added 2026-05-30 to fill the gap.
5. **The curator is blind to existing page bodies.** `propose_updates()` discards the repo (`curator_llm.py:276 del repo`); it sees only per-type counts, ≤5 slug names/type, 3 log headings, a top-10 slug shortlist — never page content. On `update` it regenerates the whole `new_body` blind, and `AtomicWriter` `os.replace`s the file wholesale → duplicates + the exact "unstructured rewrite drops content" failure ADR-0013 said the redesign would fix.
6. **Runs on the frontier model, fires near-continuously.** Both curator and rollup default to `brain.primary` (`_resolve_provider_and_model`); the `CuratorScheduler` cooldown/lock is bypassed in production (`server.py:1742 scheduler_factory=None`); the live throttle is only the `VoiceFactBridge` 60s rate-limit with `aggressive_mode=True` ingesting every ≥30-char turn.
7. **Voice-path vault bug.** `WikiContextInjector` reads a non-existent field `config.memory.vault_root` (`factory.py:1016-1023`) → always `None` → hardcoded fallback; a custom `[wiki_integration].vault_root` is silently ignored on the voice path. Plus: no boot-time FTS index build (stale vault returns zero hits) and the CLI `reindex` default `--vault` points at the legacy `data/workspace` tree.

**Diagnosis in one line:** the intelligence exists but sits at the wrong input (window focus, not conversation) and is mis-tuned (over-capture, blind to existing content); the autonomous "grow real knowledge" hop was never built; and it runs expensively.

## 2. Maintainer decisions (this brainstorm)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Memory character | **Completeness with cleanliness** — capture broadly (recall-biased), but zero junk artifacts: no truncation, no dangling links, no duplicates, no prompt leaks. The problem is artifact *quality* + *growth*, not volume. |
| D2 | What feeds the memory | **Conversation only** — what the user says/asks/decides (voice + chat). Window/app-focus pages are dropped as a memory source. Awareness keeps live situational awareness but no longer writes durable pages. |
| D3 | Curator model | **User-configurable dedicated model** — a new "Wiki" provider/model card in the settings/API-keys area (mirrors Brain + TTS), reusing the existing provider/model selector. Sensible cheap default, overridable. |
| D4 | What the curator understands | **Living profile + knowledge graph** — autonomously maintains a structured living profile of the user (identity, preferences, work style, people, active projects, decisions) AND linked topic pages, updating rather than blindly re-creating. Revives structured-fact capture, unified in the one vault. |
| D5 | Everything else | **Maintainer delegated** — take the most plausible / best-practice option for embeddings, invalidate-vs-delete, scheduler, cadence. Validate by end-to-end test of the running feature. |

## 3. Goals / Non-Goals

**Goals**
- A genuinely autonomous curator that decides what to remember and keeps a coherent, growing, deduped, cross-linked vault sourced from conversation.
- Runs on a **user-chosen dedicated model**, low-token, **never on the voice critical path** (AP-9).
- Cross-platform: Windows, macOS, Linux, and a headless `python:3.11-slim` €5 VPS (1 vCPU / 1 GB, no GPU) — base install gains **no new hard dependency**.
- Fixes every confirmed quality defect (truncation, dangling links, duplicates, prompt leaks, blind merge, vault-path bug, missing boot FTS index).

**Non-Goals**
- Local-only GPU inference; mandatory embeddings on the base install (embeddings are an opt-in extra).
- Re-introducing a second parallel notebook (`data/workspace/`) — the living profile lives **inside** the one Obsidian vault.
- Touching the voice latency path. The curator stays fully background / off-path.

## 4. Architecture — two-stage conversation-fed "sleep-time" curator

Pattern validated against mem0 (extract → ADD/UPDATE/NOOP judge against k-nearest), Letta (sleep-time consolidation off the user path), Zep (invalidate-not-delete), Generative Agents (importance + periodic reflection), Anthropic memory tool (write durable facts not transcripts; confined sandbox + PII guard).

```
Conversation turn (voice TranscriptFinal / chat user+assistant turn)
        │  (fire-and-forget, off voice path)
        ▼
STAGE 1 — Extractor  (cheap dedicated model, tiny prompt, ADD-only)
        │  emits 0..N candidate atomic facts → append to a SQLite journal
        ▼
   candidate journal  (data/jarvis.db table; survives restart)
        │  (debounced / idle-triggered, batched — CuratorScheduler-gated)
        ▼
STAGE 2 — Consolidator  (cheap dedicated model, body-aware judge)
        │  per candidate: retrieve k-nearest existing pages (FTS5/BM25 + slug overlap)
        │  show their BODIES → decide ADD | UPDATE | NOOP | INVALIDATE
        │  build/maintain: living profile (user entity) + entity/concept/project pages
        │  contradictions → mark superseded (valid_until + superseded-by wikilink), never delete
        ▼
   AtomicWriter.apply()  (backup → validate → os.replace → rollback → FTS upsert)
        ▼
   Obsidian vault  (one source of truth; human-readable, Git-diffable)
```

**Why two stages:** Stage 1 is cheap and ADD-only so a failing/over-eager extractor can never corrupt existing pages; the expensive, judgment-bearing merge/promote work is **batched off-path** on a cheap model — the lowest steady-state token profile and the literature-backed selection mechanism.

### 4.1 Components (new + changed)

| Unit | Role | Seam |
|------|------|------|
| `ConversationFactExtractor` (new) | Stage 1: cheap-model extraction of candidate atomic facts from a conversation turn → journal. ADD-only. | new module `jarvis/memory/wiki/extractor.py`; invoked from `voice_bridge.py` (and a chat hook) |
| `CandidateJournal` (new) | Durable append-only queue of candidate facts (SQLite table in `data/jarvis.db`, cross-platform, FTS5 already present). | new `jarvis/memory/wiki/journal.py` + migration |
| `Consolidator` (replaces blind curator path) | Stage 2: drains journal, body-aware ADD/UPDATE/NOOP/INVALIDATE judge against k-nearest pages, maintains living profile + topic graph. | extends `curator.py` / `curator_llm.py`; **passes page bodies into the prompt** (undo `del repo`) |
| `WikiModelConfig` surfacing | Reuse `WikiCuratorConfig.provider/model` via the single `_resolve_provider_and_model` hook (drives both stages). Cheap default. | `curator_llm.py:63-81`, `config.py:553-571` |
| Wiki settings card (new) | Frontend provider/model picker for the wiki; backend setter mirrors `provider_routes.py` + `config_writer`. | `ApiKeysView.tsx` + `ProviderSwitcher.tsx` + `useProviders.ts`; new route in `settings_routes.py`/control API |
| `SessionRollupWorker` (retired as wiki source) | No longer writes durable wiki pages from window-focus episodes. Optionally repurposed later to digest *conversation* content only. Awareness L1/L2 stays for live situational awareness. | `session_rollup.py`, `integration.py` (drop the `IdleEntered`→rollup→re-ingest double pass) |
| `CuratorScheduler` (finally wired) | Gates Stage 2 with cooldown + `VaultLock`. **Fix `lock.py` `time.monotonic()` → `time.time()`** for correct cross-process stale detection before wiring. | `server.py:1742 scheduler_factory`, `scheduler.py`, `lock.py` |

### 4.2 Selection mechanism (the thing that was missing)

- **Stage 1 extraction** = "what in this turn is a candidate fact?" (cheap model, recall-biased per D1 — when unsure, surface a candidate).
- **Stage 2 judge** = "is this new / a change / already known / now contradicted?" against the **actual bodies** of the k-nearest pages → ADD / UPDATE / NOOP / INVALIDATE. This is where over-capture is contained (NOOP de-dupes) and where D1's "completeness" is reconciled with "no junk" — we capture broadly but the judge prevents duplicate/contradictory clutter.
- k-nearest retrieval starts with **FTS5/BM25 + slug-overlap** (no new dependency). Embedding-based similarity is a later opt-in upgrade (§6).

### 4.3 Living profile + growth (D4)

- The user entity page (`entities/<user>.md`) gains structured sections the consolidator maintains autonomously: Identity, Preferences, Work style, Values, Relationships, Active projects, Decisions — the capability the legacy curator had, now **inside the vault** (no second notebook, avoids the 2026-05-17 divergence problem).
- People / project / concept pages are created and **updated in place** (body-aware merge), giving the "memory that grows and understands me" the maintainer asked for.

## 5. Guardrails (autonomous-but-bounded)

- **Truncation killed:** raise rollup/curator `max_output_tokens` and add a finish-reason / stream-drain check — a capped generation is completed or discarded, never written half-finished.
- **Create-or-refuse links:** honor `schema.md:148` — a `[[link]]` that resolves nowhere is either created in the same batch or demoted to plain text. No dangling links.
- **No-PII / no-secret validator on write** (regex, AP-2) — pages persist deliberately, so a secret/credential pattern blocks the write.
- **Body-aware UPDATE** prevents the blind-regenerate content loss.
- **Invalidate-not-delete** (Zep): contradictions set frontmatter `valid_until` + a `superseded-by` wikilink — auditable, Git-diffable history; nothing is silently erased.
- Keep existing safety: `AtomicWriter` backup→validate→rollback, `_MAX_UPDATES_PER_INGEST=30`, `_VALID_OPERATIONS` allowlist, 30s concurrent-edit lock, never-touch `_archive/`, backup dir outside watchdog scope (AP-13), synchronous reload-test.
- **Scheduler cooldown + VaultLock** wired so concurrent ingests / parallel Jarvis processes don't race the vault (after the `time.monotonic`→`time.time` fix).
- **New wire-format vocab** (the `invalidate` operation; curator decision reasons ADD/UPDATE/NOOP/INVALIDATE if surfaced to UI/telemetry) follows the **five-layer enum pattern** (`docs/anti-drift-three-layer.md`) + a parity test (AP-4 / BUG-008 defense).

## 6. Cross-platform (binding — CLOUD.md doctrine)

- Everything Stage 1/2, journal, scheduler, profile is **pure Python + pathlib + SQLite/FTS5** — FTS5 ships in `python:3.11-slim`; `AtomicWriter` is already UTF-8 / `newline=''` / `os.replace` / `splitdrive`-safe.
- **No new base hard dependency.** k-nearest is FTS5/BM25 + slug overlap in the base install.
- **Embeddings = opt-in extra (phase 3).** A local CPU embedding index (e.g. MiniLM) for stronger dedupe goes in a `[desktop]`-style extras group with a **graceful FTS5-only fallback** on the bare VPS (capability probe; AD-6). Vectors stored as a sidecar SQLite index, never altering the human-readable Markdown.
- Dedicated model is a network call to whatever provider the user picked → runs on a 1-vCPU headless VPS; `follow_brain`-style default means no extra key required.

## 7. Config + Settings UI (D3)

- Backend: reuse `WikiCuratorConfig.provider` / `.model` (already exist, `ConfigDict(extra="allow")` per AP-16) through the single `_resolve_provider_and_model` hook (drives both stages). Default to a cheap tier (mirror the ack-brain `follow_brain` + per-provider cheap-model map, or pin `gemini-3.1-flash-lite`).
- Writes go through `config_writer` (lock + tempfile + BOM-safe, AP-7); a live setter mirrors `provider_routes.py` so a UI change applies without restart where possible.
- Frontend: a **"Wiki" card** in `ApiKeysView.tsx` reusing `ProviderSwitcher.tsx` + `useProviders.ts` (same component as Brain/TTS), bound to a new `GET/PUT /api/settings/wiki-provider` (or control-API) endpoint. i18n: English source key + de/en/es.
- Telemetry: add quality counters (candidates extracted, ADD/UPDATE/NOOP/INVALIDATE counts, pages created/updated, dangling-links-refused, PII-blocked) to `GET /api/wiki/telemetry`.

## 8. Implementation waves

**Wave 1 — Stop the bleeding + dedicated-model UI (ships fast, low risk):**
1. Surface the Wiki provider/model settings card (D3) + backend setter; pin a cheap default through `_resolve_provider_and_model`.
2. Retire the awareness-episode → session-page wiki feed (D2); remove the redundant third LLM re-ingest pass in `integration._flush_and_ingest`.
3. Fix truncation (token cap + finish-reason/drain check); enforce create-or-refuse links; de-duplicate the archiver (move, not copy).
4. Fix `WikiContextInjector` `vault_root` bug (`factory.py:1016`); add boot-time/first-search FTS `index_vault`; fix CLI default `--vault`.
5. Add the no-PII/secret write validator.
6. One-time vault cleanup: archive/prune the existing truncated/window-focus junk pages.

**Wave 2 — Two-stage conversation curator (the real feature):**
1. `ConversationFactExtractor` (Stage 1) + `CandidateJournal` + migration; wire to `voice_bridge.py` + a chat hook (ADD-only, off-path).
2. `Consolidator` (Stage 2): body-aware ADD/UPDATE/NOOP/INVALIDATE judge against k-nearest pages; wire `CuratorScheduler` (cooldown + `VaultLock`, after the `time.monotonic`→`time.time` fix).
3. Living profile + topic-graph maintenance (D4); invalidate-not-delete schema (frontmatter `valid_until` + `superseded-by`); five-layer enum for the new `invalidate` op.
4. Telemetry quality counters.

**Wave 3 — Optional enhancement (later):**
- Extras-gated local CPU embeddings for stronger dedupe + retrieval (FTS5-only fallback); periodic reflection/consolidation pass over the growing vault.

## 9. Testing & acceptance

- **TDD per unit** (extractor, journal, consolidator judge, link validator, PII validator, invalidate schema), fakes in `tests/fakes/`, no `unittest.mock` for components.
- Parity test for the new `invalidate` enum (Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI).
- Cross-platform: the base path must import + boot on a fresh `python:3.11-slim` Linux container with no extras (CI matrix).
- Regression: curator/extractor never invoked on the voice critical path (assert background-task only).
- **Maintainer acceptance = end-to-end test of the running feature** (D5): talk to Jarvis, confirm real facts land as clean, deduped, growing, cross-linked pages with no truncation/dangling links, and that the living profile fills in over a few conversations — on the user-chosen Wiki model.

## 10. Risks / open items

- Conversation-fed extraction quality on a cheap model — mitigated by recall-biased Stage 1 + body-aware Stage-2 judge; tunable.
- FTS5-only k-nearest may under-cluster near-duplicate entities until embeddings land (Wave 3).
- `VaultLock` `time.monotonic` stale-clock bug **must** be fixed before wiring the scheduler.
- The named master plan `~/.claude/plans/also-er-muss-auch-lexical-pond.md` is missing on disk; vision was reconstructed from ADR-0013/0014/0015 + the B1 README + `schema.md` (all agree on the autonomous-editor ambition).
