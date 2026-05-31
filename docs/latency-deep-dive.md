# Latency Deep-Dive — Frontier models without quality compromise

**Date:** 2026-04-30 · **Trigger:** Voice session 2026-04-30 20:18, 8.4 s thinking + 14.21 s speaking (Gemini 3.1 Pro Preview, 20501 input tokens, 91 output tokens) · **Goal:** drastically cut latency **without** a model downgrade. Frontier stays frontier (Gemini 3.1 Pro, Claude Opus 4.7, GPT-5.5 etc.) and automatically follows the market as new frontier models appear.

---

## 1. Forensics: Where do the 22.6 seconds really go?

| Phase | Duration | Source | Note |
|-------|-------|--------|-----------|
| **Brain "Thinking"** | 8.40 s | Transcript | TTFT + reasoning + token generation for 91 tokens of output |
| **Brain → TTS aggregation** | ~0.0 s | `pipeline.py:1335` | But: TTS only starts once the brain is complete (serial) |
| **TTS "Speaking"** | 14.21 s | Transcript | Gemini Flash TTS for ~30 words including audio playback |
| **Other (vision capture, routing, filter)** | ~5.8 s | Difference | Vision-frame collection, output filter, state transitions |

### 1.1 Token forensics: 20501 input tokens for "Was geht ab? Ich möchte wissen, was…"

The token footprint presumably breaks down roughly into:

- **Vision frames** (`[brain.router.vision] enabled = true, refresh_interval_s = 2.0`): foreground screenshot up to 500 KB, ~1500–3000 image tokens per frame. With a 2-second refresh and frame-history/buffer logic, 5000–10000 tokens are not unusual.
- **Persona/system prompt** (`brain/persona_loader.py`): plan §22-compliant pure-dispatcher prompt + sub-Jarvis spawn rules + bilingual + filter hints. Estimate: 2000–3000 tokens.
- **Tool definitions** (router tier: `run-shell`, `screen-snapshot`, `dispatch-to-harness`, `multi-spawn`, `spawn-sub-jarvis`, `dispatch-with-review` plus self-mod tools): each tool with its JSON schema 200–600 tokens, aggregate ~3000 tokens.
- **Memory context** (core memory + recall + persona recall): variable, 1000–3000 tokens.
- **Conversation history** (`use_history = True`): since session start, nearly empty on a voice-session restart.
- **The actual user input**: ~10 tokens.

**Consequence:** 99.9 % of the 20501 input tokens are static or semi-static — the same persona text, the same tool schemas, the same vision frame changes only gradually. **This is exactly what provider caching mechanisms exist for.** Without caching, all 20501 tokens are reprocessed on every turn. With correct caching, depending on the provider 70–95 % of them are billed at the cache-read rate (typically 10× cheaper and 2–5× faster on TTFT).

### 1.2 Pipeline architecture weakness: the aggregation pattern

`jarvis/speech/pipeline.py:1335`:
```python
response = await self._brain_with_ack(text, lang)   # wartet auf ENTIRE String
…
await self._speak(response, language=lang)          # TTS auf ENTIRE String
```

`jarvis/brain/manager.py:1310`:
```python
async def __call__(self, text: str) -> str:
    return await self.generate(text)    # aggregiert Stream zu str
```

`jarvis/brain/manager.py:1345` (in `summarize`):
```python
agg = await aggregate(brain.complete(req))   # kompletter Stream → ein str
```

The individual brain providers (`gemini.py:165`, `_anthropic_base.py`, etc.) already return `AsyncIterator[BrainDelta]` — the stream is aggregated **inside** the `BrainManager`. The pipeline never sees the stream.

Gemini Flash TTS already has "pseudo-streaming via sentence chunking" built in (`gemini_flash_tts.py:8-11, 56`). That is useless, however, because it receives a complete text: TTS could start on sentence 1 while sentence 2 is synthesized, but sentence 1 only arrives in the output **after the brain has produced all sentences**.

**Consequence:** even if the brain has the first sentence ready in 1.5 s, TTS starts at the earliest after 8.4 s. The user hears the first audio only after 8.4 + ~0.7 s (TTS first chunk) = ~9 s. With a sentence-streaming pipeline that could drop to ~2–3 s.

---

## 2. Lever inventory: What works, and what does it cost?

Weighted below by **latency effect × quality risk × effort**. Quality risk = 0 means: answer quality stays 1:1 identical.

### Lever class A — Pipeline architecture (quality risk = 0)

| # | Lever | Latency effect | Effort | Quality risk |
|---|-------|---------------|---------|--------------|
| **A1** | **Sentence-streaming TTS** — pass the brain stream through, TTS on sentence boundaries | **−6 to −10 s perceived latency** (time-to-first-audio) | medium (2–3 days) | **0** |
| **A2** | **Incremental output filter** — `scrub_for_voice` per sentence instead of at the end | −0.2 to −0.5 s | small (1 day) | 0 |
| **A3** | **Async vision-frame refresh** — do not capture the frame in the critical path, only pull the last available one | −0.3 to −1.0 s | small (1 day) | 0 (partially exists via `refresh_interval_s`, but `_collect_vision_images` blocks depending on the implementation) |
| **A4** | **Pre-connection / connection pooling** — `httpx.AsyncClient` with `keep_alive=True` globally, TLS session resumption, HTTP/2 | −100 to −300 ms | small (1 day) | 0 |
| **A5** | **Sub-first-token acknowledgment** — brain delivers its first token < 1s → no longer play a TTS filler phrase ("Einen Moment …"), but start the TTS stream immediately | −0.5 to −1 s perceived | small (1 day) | 0 |

**Recommendation:** A1 is by far the biggest lever. **A1 + A2 + A4 together would more than halve the perceived latency — without touching a single model.**

### Lever class B — Provider caching (quality risk = 0)

| # | Lever | Latency effect | Cost effect | Effort |
|---|-------|---------------|---------------|---------|
| **B1** | **Anthropic Prompt Caching** (`cache_control: ephemeral`, 5min/1h TTL) — system prompt + tool definitions + persona as a cache block | TTFT −40 to −60 % on a cache hit | cache read 10 % of standard | medium (partially exists in `_anthropic_base.py` per CLAUDE.md "Phase L.5 1h-TTL beta header") — **verify whether active** |
| **B2** | **Gemini Context Caching** (`client.caches.create()` with a long prefix, TTL hours) — system + tools + persona as cached content | TTFT −30 to −50 % | cache read ~25 % of standard | medium (2 days) — **currently not implemented in `gemini.py`** |
| **B3** | **OpenAI Automatic Prompt Caching** (kicks in from a 1024-token prefix, automatic) — no code, only prompt structure "most stable prefix up front" | TTFT −80 % on a cache hit | cache read 50 % of standard | small (prompt restructuring) |
| **B4** | **Vision-frame diff cache** — re-encode + send a frame only on a noticeable change, otherwise a cache reference | TTFT −10 to −30 % at steady state | less bandwidth + tokens | medium (3 days, custom logic) |

**Recommendation:** B1+B2 are critical. The persona prompt + tool schemas (~6000 tokens together) practically never change at runtime — a perfect cache candidate. Gemini Context Caching is especially valuable because Gemini is the primary brain (`primary = "gemini"`).

### Lever class C — Thinking-budget control (quality risk: low, controllable)

| # | Lever | Latency effect | Quality risk | Effort |
|---|-------|---------------|--------------|---------|
| **C1** | **Gemini `thinking_config.thinking_budget`** — configurable per tier: Router=0 or dynamic-low, Sub-Jarvis=high | −2 to −5 s in the router tier (currently thinks without a limit) | low (the router makes a tool choice, barely needs reasoning) | small (1 day) |
| **C2** | **Anthropic Extended Thinking `thinking.budget_tokens`** for sub-Jarvis (Opus) — deliberately set instead of default | at a low value −1 to −3 s | low-medium (depending on the value) | small (1 day) |
| **C3** | **OpenAI `reasoning.effort: "low"|"medium"|"high"` for GPT-5.5 Pro** — map per tier | analogous to C1 | analogous to C1 | small |
| **C4** | **Per-intent tier routing** — smalltalk → Flash/Haiku; reasoning → Pro/Opus. Partially exists via the router; map it consistently onto the thinking budget | −3 to −6 s on smalltalk requests | 0 (correct model choice, not a downgrade) | medium |

**Recommendation:** C1 is mandatory. Currently Gemini 3.1 Pro Preview in the sub-Jarvis tier probably thinks with the default budget (high), and the router (Gemini 3 Flash Preview) does too — but Flash has barely any thinking need for tool routing.

### Lever class D — Hedged / parallel requests (quality risk = 0, cost +)

| # | Lever | Latency effect | Cost effect | Effort |
|---|-------|---------------|-------------|---------|
| **D1** | **Provider hedging** — same prompt in parallel to 2 frontier providers (e.g. Gemini 3.1 Pro + Claude Opus 4.7), first answer wins, the other is cancelled | best p50 of two providers → −20 to −40 % | double cost on the hedged prompt (cancelled streams are not 0) | medium (2 days) |
| **D2** | **Region hedging** — same provider in 2 regions (US-East + EU) | TTFT −100 to −500 ms | double cost | small (endpoint config) |
| **D3** | **Speculative acknowledgment** — at latency > 800 ms play a pre-rendered filler phrase (already exists via `_brain_with_ack` and `_task_ack_delay_s`) | perceived −1 s | 0 | already implemented |

**Recommendation:** D1 is a backup strategy for especially tough cases (>10s). Not the first priority, but quite interesting as a "premium mode" for sub-Jarvis spawns.

### Lever class E — Model strategy (quality risk: controllable)

| # | Lever | Latency effect | Quality risk |
|---|-------|---------------|--------------|
| **E1** | **Frontier registry with auto-update** — `models.toml` with "frontier" as a tag, an automatic check (e.g. weekly) for whether new frontier models exist | 0 (latency-neutral, but automatically future-proof) | 0 |
| **E2** | **"Speculative tier" instead of reasoning** for trivial requests — the router already decides: do I need Pro or Flash? The current logic is a pure dispatcher → spawn a sub-Jarvis on every action verb. Not every "öffne …" needs Opus. | −5 to −8 s on trivial sub-Jarvis spawns | low (some spawns then run on Flash instead of Pro) |
| **E3** | **Model aliasing** — `claude-opus-latest` / `gemini-pro-latest` instead of hard-wired `claude-opus-4-7` / `gemini-3.1-pro-preview`. Provider-side or as a config layer. | 0 | 0 |

---

## 3. Strategy: What do I concretely recommend?

### Stage 1 — Immediate measures (1 week, ~80 % of the perceived latency gone)

**Order by cost/benefit:**

1. **A1 + A2: Sentence-streaming pipeline** (3 days)
   - Expose a second public API on the `BrainManager`: `generate_stream(text) -> AsyncIterator[str]` (token deltas)
   - `pipeline.py` parses the stream on sentence boundaries (`.!?`), per sentence: `scrub_for_voice` → `_speak`
   - Expected effect: time-to-first-audio from ~9 s to ~2–3 s.

2. **B2: Gemini Context Caching for persona+tools+system** (2 days)
   - At BrainManager bootstrap: once `client.caches.create(model, system_instruction=…, tools=…, contents=[])` with a 1 h TTL, remember the ID.
   - In `complete()`: pass `cached_content=cache_id` to `generate_content_stream`.
   - Heartbeat refresh every ~50 min (as `prompt_cache_heartbeat_seconds = 240` is already contemplated for Anthropic — extend analogously for Gemini).
   - Expected effect: TTFT for Gemini Pro drops from ~3 s to ~1.5 s, cost roughly halves.

3. **B1: Verify and, if needed, fix Anthropic prompt caching** (1 day)
   - Check the `_anthropic_base.py` beta header for the 1h TTL (`anthropic-beta: prompt-caching-2024-07-31` with `extended-cache-ttl-2025-04-11` or newer for 1h)
   - System prompt + tool definitions must have `cache_control: {type: "ephemeral", ttl: "1h"}`.
   - Expected effect: TTFT for Claude Sonnet/Opus drops analogously to Gemini.

4. **C1: Gemini thinking-budget config** (1 day)
   - Pydantic model: `[brain.providers.gemini].thinking_budget_router = 0`, `thinking_budget_sub_jarvis = "dynamic"` or a fixed token value.
   - In `gemini.py:complete()`: `thinking_config = types.ThinkingConfig(thinking_budget=N)` on `GenerateContentConfig`.
   - Expected effect: router-tier latency roughly halves; sub-Jarvis on trivial actions likewise.

### Stage 2 — Mid-term (2–3 weeks)

5. **E1: Frontier registry with auto-update**
   - New file `jarvis/brain/frontier_registry.py` + `data/frontier-models.json` (per-provider list of the current frontier IDs with a last-checked date).
   - At bootstrap: optional `--frontier-check` or a weekly skill that checks against Anthropic `/v1/models`, Google `aiplatform.list_models()`, OpenAI `/v1/models` and reconciles against a signed source (e.g. our own GitHub Pages YAML `frontier.yaml`).
   - In `jarvis.toml`, tags are used instead of fixed model IDs: `model = "@frontier-fast"`, `deep_model = "@frontier-deep"`.
   - Resolution in the BrainManager via the registry.
   - Expected effect: an automatic switch on every new frontier release without a code edit. **Exactly what you requested.**

6. **E2: "Quick-path" for trivial sub-Jarvis spawns**
   - The force-spawn heuristic (`_should_force_sub_jarvis`) gets a second stage: trivial actions ("öffne Notepad", "type X", "click Y") land on the Flash tier instead of Pro.
   - Complex visions ("baue mir eine App", "deploy auf Vercel") stay on Opus/Pro.
   - Expected effect: ~50 % of sub-Jarvis spawns become 5–8 s faster.

7. **A3 + A4: Pipeline optimizations**
   - Move the vision-capture path out of the critical path (its own background producer, the BrainManager reads the last frame from shared state).
   - Global `httpx.AsyncClient` instances per provider, connection pool, HTTP/2.

### Stage 3 — Premium mode (4+ weeks, optional)

8. **D1: Provider hedging as a voice toggle** ("Jarvis, Premium-Mode") — Anthropic+Gemini in parallel, first answer wins.
9. **B4: Vision-frame diff cache** — send only changing frames into the context; hash-diff against the last frame, re-encode only on deltas.

---

## 4. Frontier auto-update — detailed design

### Requirement (user mandate)
> "Ich möchte aber dass man recherchiert, wie man das regeln kann […]. Frontiermodelle behalten will. […] und wenn es sich updated, also wieder neue Frontiermodels rauskommen, natürlich auch wieder updated."

### Solution architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  data/frontier-models.json (versioned, in Repo)                 │
│  {                                                              │
│    "anthropic": {                                               │
│      "frontier-fast":  "claude-haiku-4-5-20251001",             │
│      "frontier-deep":  "claude-opus-4-7",                       │
│      "last_checked": "2026-04-30T..."                           │
│    },                                                           │
│    "gemini": { "frontier-fast": "gemini-3-flash-preview", … },  │
│    "openai": { "frontier-fast": "gpt-5.5", … },                 │
│    "grok": { … }                                                │
│  }                                                              │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     │ load at boot
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  jarvis/brain/frontier_registry.py                              │
│  - resolve("@frontier-deep", provider="anthropic")              │
│      -> "claude-opus-4-7"                                       │
│  - check_for_updates() -> diff against curated source           │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     │ used by BrainManager, factory.py            │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  jarvis.toml                                                    │
│  [brain.sub_jarvis]                                             │
│    provider = "gemini"                                          │
│    model    = "@frontier-deep"   # statt "gemini-3.1-pro-preview"│
└─────────────────────────────────────────────────────────────────┘
```

**Update mechanism (three ways, combinable):**

1. **Curated pull** — our own GitHub Pages YAML `https://<repo>/frontier-models.yaml`, hand-curated, signed via a git commit. A `frontier-update` skill (cron weekly) pulls it, compares, and writes `data/frontier-models.json`. Advantage: editorial control (not every preview version = "frontier", the LMArena score is factored in).

2. **Provider-API pull** — against `/v1/models` (Anthropic/OpenAI), `aiplatform.list_models` (Google) sorted by release date + heuristic (`*-pro*`, `*-opus*`, `*-deep*` etc.). Advantage: no central maintainer. Disadvantage: providers list many snapshots, "frontier" is heuristic.

3. **Drift-notify mode** — instead of switching automatically, only a `frontier_drift_detected` event on the bus + a toast in the UI: "Anthropic has released Opus 4.8, do you want to switch?" Click → update of `models.toml`. Advantage: no surprise quality drift on a bad release; active user confirmation.

**Recommendation:** hybrid — curated pull as default + a drift-notify toast on major version jumps (pattern-match a `\d+\.\d+\.\d+` jump in the major component).

---

## 5. Quality protection — What must NOT happen?

User mandate: "die Qualität der Antworten unter Mitleidenschaft" must not suffer. Delineation:

| Measure | Quality impact |
|----------|-----------------|
| Sentence-streaming TTS | **Identical answer, just audible sooner** |
| Provider caching | **Bit-identical to the uncached version** (the cache is transparent) |
| Connection pooling | **0** |
| Thinking-budget reduction on the router | The router makes a tool choice, needs no reasoning. **0 risk in the router tier.** |
| Thinking-budget reduction on sub-Jarvis | **Careful here.** Keep the default budget for sub-Jarvis Pro/Opus conservative. Divert trivial actions (E2) to the Flash tier via heuristic instead of blocking Opus with a low budget. |
| Hedging | **0** (same frontier provider, the fastest answer wins) |

**Red line:** never statically cap the reasoning budget on Opus/Pro for complex requests. Instead, route the *class* of the request correctly (E2). The mandate "keep frontier models" is consistent with thinking-budget control as long as the budget fits the class of the task.

---

## 6. Anti-patterns — What NOT to do

| AP | Anti-pattern | Why not |
|----|--------------|-------------|
| **AP-1** | Replace frontier with a fast model ("Gemini Flash instead of Pro everywhere") | Violates the user mandate. The quality drop is not the latency problem. |
| **AP-2** | Caching without a heartbeat | The cache expires, then periodic cold starts with full latency. A heartbeat cron is needed. |
| **AP-3** | Disable vision to save tokens | Vision is an explicitly desired feature (CLAUDE.md, BUG-004 fix). Instead **cache vision correctly** (B4) + frame diff. |
| **AP-4** | Build speculative decoding yourself | Provider-internal, not user-facing. Effort ↑↑↑, the effect is already covered by caching/streaming. |
| **AP-5** | Sentence-streaming only in the voice path, leaving the chat path | Then two code paths, drift guaranteed. Sentence streaming belongs in the BrainManager as a second public API; both paths consume it. |
| **AP-6** | Commit cache IDs into `data/` | Cache IDs are ephemeral (at Anthropic 5min/1h, at Gemini also time-bound). State, not source. `.gitignore`. |
| **AP-7** | Frontier auto-update without a pin override | If a new "frontier" release gets worse (it happens!), a user pin in `jarvis.toml` must remain possible (`model = "claude-opus-4-7"` instead of `@frontier-deep`). |
| **AP-8** | Hedging as the default | Cost doubling without user awareness. Hedging is premium mode, opt-in. |

---

## 7. Expected end result after stage 1

Expected new voice-session latency for the request above ("Was geht ab?"):

| Phase | Before | After (stage 1) | Reason |
|-------|--------|-------------------|-------------|
| Brain TTFT (first token) | ~3.0 s | ~1.0 s | B2 Gemini Context Caching + C1 thinking budget |
| Brain → first sentence | ~5.0 s | ~1.5 s | TTFT + token generation on the cached prefix |
| Time-to-first-audio | ~9.0 s | ~2.5 s | A1 sentence-streaming TTS starts on sentence 1 in parallel with brain generation of sentence 2 |
| Total brain-stream end | ~8.4 s | ~3.5 s | Generation on the cached prefix |
| Total audio end | ~22.6 s | ~5.5 s | TTS streams in parallel with the brain |

**Estimate:** **22.6 s → 5.5 s** (perceived latency: 9 s → 2.5 s). **Without switching a single model.**

---

## 8. Roadmap

| Sprint | Deliverable | Effort | Owner |
|--------|-----------|--------|-------|
| S1 (week 1) | A1 sentence-streaming TTS + A2 incremental filter | 3 days | Hauptjarvis |
| S1 | B2 Gemini Context Caching | 2 days | Hauptjarvis |
| S1 | B1 verify Anthropic cache | 1 day | Hauptjarvis |
| S1 | C1 Gemini thinking-budget config | 1 day | Hauptjarvis |
| S2 (week 2-3) | E1 frontier registry with auto-update | 3 days | Hauptjarvis |
| S2 | E2 quick-path heuristic for trivial sub-Jarvis spawns | 2 days | Hauptjarvis |
| S2 | A3+A4 pipeline optimizations | 2 days | Hauptjarvis |
| S3 (optional) | D1 hedging as premium mode | 3 days | Hauptjarvis |
| S3 (optional) | B4 vision-frame diff cache | 3 days | Hauptjarvis |

**ADRs to write:**
- ADR-0013: Sentence-streaming-TTS pipeline architecture
- ADR-0014: Provider-caching strategy (Gemini Context Cache + Anthropic Prompt Cache + OpenAI Auto-Cache)
- ADR-0015: Frontier registry with tag resolution + auto-update

---

## 9. Validation

After each stage, run the following smoke tests:

1. **A latency regression test** exists: `tests/integration/test_tier1_speed.py`. Extend it to:
   - Time-to-first-audio ≤ 3 s (P95) on smalltalk
   - Time-to-first-audio ≤ 5 s (P95) on a sub-Jarvis spawn
   - Total latency ≤ 8 s (P95) on smalltalk
2. **Quality regression test:** 20 fixed voice prompts (deterministic seed), compare the brain outputs before/after. Diff score (cosine similarity) ≥ 0.95.
3. **Cost regression test:** compare the cost meter before/after caching on the same prompt set. Expectation: cost reduction > 50 % at stable quality.

---

## 10. Open questions

1. **Anthropic prompt-cache status:** CLAUDE.md says "Phase L.5 1h-TTL beta header set automatically", but no test verifies it. Does a cache-hit-counter integration test exist? — Check before S1 whether it is already active.
2. **Gemini quota:** Context Caching has its own token limits per cache. Does the daily free tier tolerate the persona+tool cache? — Check on the paid tier.
3. **Real vision-token budget:** confirm that the 20501 input tokens come primarily from vision + persona. A one-off voice trace with `usage.input_tokens_breakdown` would show this exactly. Pre-S1 diagnostics.
4. **Auto-update source of truth:** curate it ourselves via a GitHub repo, or dock onto an existing source (LMArena, Artificial Analysis, Hugging Face Open LLM Leaderboard)? — User decision; recommendation: hybrid (our own `frontier.yaml` + LMArena API as a pre-filter).

---

## 11. TL;DR

- **Problem:** 8.4 s thinking + 14.21 s speaking, because (1) the pipeline is serial, (2) caching is missing, (3) the thinking budget is unconfigured, (4) 20501 tokens are reprocessed every turn.
- **Solution:** sentence-streaming TTS (A1) + Gemini Context Caching (B2) + verify Anthropic cache (B1) + thinking budget (C1). 1 week, all models stay frontier.
- **Frontier auto-update:** tag resolver (`@frontier-deep`) + curated `frontier.yaml` + a weekly skill + a drift toast on major jumps.
- **Quality:** strictly protected — the A/B/C levers are transparent or class-aware, no model downgrade.
- **Expected effect:** 22.6 s → 5.5 s. Time-to-first-audio: 9 s → 2.5 s.
