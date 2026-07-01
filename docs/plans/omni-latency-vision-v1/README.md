# Omni-Latency & Vision Optimization Suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (inline) — implement wave-by-wave with TDD. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Cut perceived voice latency (time-to-first-audio) by measuring the hot path honestly, sending the screenshot only when needed, making the already-enabled prompt cache actually hit, and streaming the ack-brain word-by-word — without losing screen context or regressing the 2026-04-28 "blind hallucination" failure.

**Architecture:** Four independently gated, independently verifiable waves. Wave 0 (measurement) ships first and changes no behavior; Waves 1–3 each sit behind a `[performance]`/`[ack_brain]` flag for instant rollback. Every optimization is validated with `scripts/latency_bench.py` before/after numbers, not just "code written".

**Tech Stack:** Python 3.11 stdlib (`time.perf_counter`, `asyncio`), existing `EventBus` (frozen-slots dataclasses), Pydantic config (`extra="allow"`), `pytest` (asyncio_mode=auto). No new hard deps; VPS-safe (no GPU/audio/Windows requirement in new code).

**Branch:** `feat/omni-latency-vision`. Pre-existing uncommitted WIP (ZDF-STT fix in `pipeline.py`, critic runner) is NOT mine — never `git add -A`; only add my own files.

---

## Verified baseline (corrects the brief)

| Brief assumption | Reality | Evidence |
|---|---|---|
| Ack blocks ~4s synchronously | Ack is already `create_task` (parallel); blocks *itself*: up to 4000ms provider + 2000ms gate = **up to 6s** to first ack-audio | `pipeline.py:1187,1206-1235`; `jarvis.toml:923,933` |
| ~9KB screenshot in prompt-middle | 100–400KB **image block on the user message**, uncapped (`max_image_kb` is dead config) | `manager.py:2013`; `config.py:307` |
| Caching is destroyed | Caching is **already on** but defeated by per-turn churn (awareness/wiki/CoreMemory-reload in the system prompt) | `jarvis.toml:410,418`; `manager.py:722,737-742,832` |
| No perf_counter in hot path | Correct. Only `ack_brain/generator.py:285`. `BrainTTFT` event exists but is **never published** | `events.py:702`; `recorder.py:75` |
| Awareness never on voice path | Awareness snapshot injected **synchronously every turn** into the system prompt | `manager.py:737-742` |

## Decisions (locked)
- **Vision gate = conservative skip-when-safe.** Drop the image ONLY for confidently text-only turns (smalltalk allowlist + knowledge questions, no visual marker). When in doubt → keep the image. Anti-regression vs. 2026-04-28.
- **Rollout = all four waves autonomously**, tests + bench numbers after each wave, final report.
- **All optimizations ship on-by-default with a rollback flag**, matching the existing `[performance]` Sprint pattern.

---

## File structure

**New**
- `jarvis/telemetry/latency.py` — `LatencyPhase` (single source of truth), `LatencyTracker` (perf_counter marks → `LatencySpan`), `LatencyAggregator` (p50/p95 for the bench).
- `scripts/latency_bench.py` — text-in E2E benchmark reusing `build_default_brain(tier="router")`.
- `tests/unit/telemetry/test_latency.py`, `tests/unit/telemetry/test_latency_span_event.py`
- `tests/unit/brain/test_vision_gate.py`, `tests/unit/brain/test_prompt_cache_layout.py`
- `tests/integration/test_ack_streaming.py`

**Modified**
- `jarvis/core/events.py` — add `LatencySpan`; (Wave 0) publish the dead `BrainTTFT` stub.
- `jarvis/core/config.py` — `LatencyConfig` + `[performance].conditional_vision`, `cache_optimized_prompt`; `[ack_brain].streaming`.
- `jarvis/speech/pipeline.py` — Wave 0 spans; Wave 3 ack-stream consumption + suppress-gate rewrite.
- `jarvis/brain/manager.py` — Wave 1 vision gate + image cap (`_collect_vision_images`); Wave 2 prompt reorg (`_build_system_prompt` static-only + dynamic-context-to-user-message + CoreMemory mtime cache).
- `jarvis/brain/router.py` — Wave 1 conditional vision line in `SYSTEM_PROMPT`.
- `jarvis/brain/ack_brain/providers/base.py`, `.../gemini.py`, `.../generator.py` — Wave 3 `run_stream()`.
- `jarvis.toml` — enable new flags.

---

## Wave 0 — Measure first (no behavior change)

### Task 0.1: `LatencyPhase` + `LatencySpan` event
**Files:** Create `jarvis/telemetry/latency.py`; Modify `jarvis/core/events.py`; Test `tests/unit/telemetry/test_latency_span_event.py`.
- [ ] `LatencyPhase` = `StrEnum` with: `STT_FINALIZE`, `INTENT_DECISION`, `ACK_FIRST_TOKEN`, `ACK_FIRST_AUDIO`, `BRAIN_FIRST_TOKEN`, `BRAIN_FIRST_AUDIO`, `TURN_TO_FIRST_AUDIO`. Single source of truth.
- [ ] `LatencySpan(Event)`: frozen/slots; `phase: str`, `duration_ms: float`, `t_start_ns: int`, `t_end_ns: int`, `detail: str = ""`. Runtime-assert `phase in LatencyPhase` values (drift guard).
- [ ] Test: event is frozen, inherits `trace_id`/`timestamp_ns`, rejects unknown phase.

### Task 0.2: `LatencyTracker`
**Files:** `jarvis/telemetry/latency.py`; Test `tests/unit/telemetry/test_latency.py`.
- [ ] `LatencyTracker(bus, trace_id, enabled)` with `mark(phase)` (records `perf_counter_ns`, computes delta from the turn anchor or prior mark) and `span(phase)` context manager. Emission is fire-and-forget (`asyncio.create_task` on the bus) so the hot path never awaits telemetry.
- [ ] `enabled=False` → `mark()`/`span()` are near-zero no-ops (guarded before any allocation).
- [ ] Test: marks produce spans with monotonic timestamps; disabled tracker emits nothing; subscriber exception never propagates.

### Task 0.3: `LatencyConfig`
**Files:** `jarvis/core/config.py`; `jarvis.toml`.
- [ ] `class LatencyConfig(BaseModel)` `model_config={"extra":"allow"}`: `enabled: bool = True`. Add `latency: LatencyConfig` to `JarvisConfig`. Add `[latency]\nenabled = true` to `jarvis.toml`.

### Task 0.4: Instrument the hot path
**Files:** `jarvis/speech/pipeline.py` (`_handle_utterance`), `jarvis/brain/manager.py` (`generate_stream` first token), `jarvis/audio/player.py` (first-audio already emits `AudioOutFirst`).
- [ ] Anchor a `LatencyTracker` at utterance finalize; `mark(STT_FINALIZE)` after STT (`~pipeline.py:1950`), `mark(INTENT_DECISION)` at PROCESSING (`~:2040`), `mark(BRAIN_FIRST_TOKEN)` on first streamed chunk (`~:2360`), `mark(TURN_TO_FIRST_AUDIO)` on first audio.
- [ ] Publish the dead `BrainTTFT` stub at brain-first-token (wire `model` + `cache_hit` if known).
- [ ] Mind the pre-existing WIP reorder around `pipeline.py:1956-1990` — instrument relative to the *current* file state.

### Task 0.5: Benchmark script
**Files:** Create `scripts/latency_bench.py`.
- [ ] `sys.stdout.reconfigure(encoding="utf-8")`; build `EventBus` + `build_default_brain(tier="router", bus=bus)`; subscribe a `LatencyAggregator`; feed N utterances (smalltalk + screen-ref + action mix) via `await bm(text)` / `generate_stream`; print p50/p95 per phase + router-decision; `--assert-slo` checks 1.2s/3.0s/150ms.
- [ ] Verify: `python scripts/latency_bench.py --runs 10` prints a table. Record baseline.

**Wave 0 verification:** `pytest tests/unit/telemetry/ -v` green; bench runs and prints baseline p50/p95. Commit.

---

## Wave 1 — Conditional vision + image cap

### Task 1.1: Vision relevance gate (skip-when-safe)
**Files:** `jarvis/brain/manager.py` (`_collect_vision_images`, pass `user_text`); new `jarvis/brain/vision_gate.py`; Test `tests/unit/brain/test_vision_gate.py`.
- [ ] `should_attach_screenshot(text, *, smalltalk_allowlist, visual_markers) -> bool`: returns `False` only if text matches smalltalk/knowledge allowlist AND contains no visual marker; else `True`. Markers: `das hier/da, schau, siehst du, auf dem bildschirm, hier, this, look, on screen, what's this, klick, fenster, …` (configurable). <!-- i18n-allow: speech input vocabulary DE -->
- [ ] Wire into `_collect_vision_images`: after the `vision is None or paused` check, if `cfg.performance.conditional_vision` and `not should_attach_screenshot(...)` → return `()` and publish `VisionSkipped`.
- [ ] Tests: "wie spät ist es" <!-- i18n-allow: test content — user voice utterance DE --> → skip; "was siehst du hier" <!-- i18n-allow: test content — user voice utterance DE --> → keep; "repariere den bug" (action) → keep; marker beats allowlist.

### Task 1.2: Enforce `max_image_kb`
**Files:** `jarvis/brain/manager.py` (`_collect_vision_images`); reuse `_resize_for_budget` from `jarvis/plugins/tool/screen_snapshot.py:49`.
- [ ] Before building `ImageBlock`, if encoded size > `cfg.brain.router.vision.max_image_kb*1024`, downscale via LANCZOS and re-encode. Log old→new size.
- [ ] Test: oversize fake image is capped under budget.

### Task 1.3: Fix the system-prompt vision lie
**Files:** `jarvis/brain/router.py` (`SYSTEM_PROMPT` line ~62).
- [ ] Make the "Du siehst Alexs Bildschirm permanent als Bild" instruction <!-- i18n-allow: product system prompt DE --> conditional/softened so the model does not hallucinate a screen when no image was attached (e.g. move to a per-turn note appended only when an image is present, or reword to "wenn ein Screenshot anhängt" <!-- i18n-allow: product system prompt DE -->).

**Wave 1 verification:** `pytest tests/unit/brain/test_vision_gate.py tests/unit/brain/test_routing.py -v`; bench shows lower TTFT on smalltalk turns, unchanged on screen-ref turns. Commit. Flag `[performance].conditional_vision = true`.

---

## Wave 2 — Prompt-cache reorg

### Task 2.1: Static-only system prompt + dynamic-to-user-message
**Files:** `jarvis/brain/manager.py` (`_build_system_prompt`, `generate`); Test `tests/unit/brain/test_prompt_cache_layout.py`.
- [ ] `_build_system_prompt()` returns ONLY static blocks (Soul, Persona, UserProfile, People, CoreMemory, router `SYSTEM_PROMPT`, base voice rules, tools). Remove awareness snapshot (`:737-742`) and wiki suffix (`:832-833`) from it.
- [ ] In `generate()`, assemble a per-turn dynamic context string (awareness snapshot + wiki suffix + current date/time) and prepend it to the **user message** content (or a separate non-cached part), not the system prompt.
- [ ] Gate on `cfg.performance.cache_optimized_prompt`; when `False`, keep legacy layout.
- [ ] Tests: system prompt is byte-identical across two turns with different awareness state (when flag on); awareness/wiki/date appear in the user message; date present (fixes missing BUG-005 injection).

### Task 2.2: Stop per-turn CoreMemory disk reload
**Files:** `jarvis/brain/manager.py` (`:717-732`).
- [ ] Replace unconditional `self._core_memory.reload()` with an mtime-guarded reload (reload only when the backing file changed). Keeps fresh facts without breaking the cache prefix every turn.
- [ ] Test: two consecutive `_build_system_prompt()` calls with unchanged file do not re-read from disk and produce identical bytes.

**Wave 2 verification:** `pytest tests/unit/brain/test_prompt_cache_layout.py tests/unit/brain/test_output_filter.py -v`; bench shows TTFT drop on repeat turns (cache hit). Commit. Flags `cache_optimized_prompt = true`.

---

## Wave 3 — Ack-brain real streaming

### Task 3.1: `run_stream()` on the provider protocol (back-compatible)
**Files:** `jarvis/brain/ack_brain/providers/base.py`; Test `tests/contract/test_ack_provider_protocol.py` (extend).
- [ ] Add `run_stream(...) -> AsyncIterator[str]` to `AbstractAckProvider` with a **default** that wraps existing `run()` (single yield). Existing adapters keep working; contract test still passes.

### Task 3.2: Gemini true streaming
**Files:** `jarvis/brain/ack_brain/providers/gemini.py`.
- [ ] Implement `run_stream()` via `generate_content_stream`, yielding text deltas.

### Task 3.3: Sentence-granular orchestration
**Files:** `jarvis/brain/ack_brain/generator.py`; Test `tests/integration/test_ack_streaming.py`.
- [ ] `AckGenerator.run_stream()`: accumulate deltas until a sentence boundary (`_STREAM_SENTENCE_END` pattern, reuse pipeline's), run full post-processing (`scrub_for_voice(ack_mode=True)`, self-answer filter, language) per sentence, yield scrubbed sentences. Circuit-breaker + timeout preserved.
- [ ] Test: a multi-sentence fake stream yields the first scrubbed sentence before the stream completes.

### Task 3.4: Pipeline consumes the stream + suppress-gate rewrite
**Files:** `jarvis/speech/pipeline.py` (`_spawn_flash_brain_ack`, `:1166-1237`).
- [ ] If `cfg.ack_brain.streaming`: `async for sentence in self._ack_brain.run_stream(...)`: on the **first** ready sentence, evaluate the suppress decision (has the deep brain already reached `JARVIS_SPEAKING`/`LISTENING`? measured from utterance start) — if not suppressed, speak it immediately via `_speak`/announcement; stop if the deep brain takes over mid-stream.
- [ ] Remove the post-buffer 2000ms poll-then-speak; the decision now happens at first-sentence-ready.
- [ ] `mark(ACK_FIRST_TOKEN)` / `mark(ACK_FIRST_AUDIO)` spans from Wave 0.

**Wave 3 verification:** `pytest tests/integration/test_ack_streaming.py tests/integration/test_ack_flow.py tests/contract/test_ack_provider_protocol.py -v`; bench shows ack-first-audio drop. Commit. Flag `[ack_brain].streaming = true`.

---

## Outcome (2026-05-24) — all four waves landed

Branch `feat/omni-latency-vision`. Commits: plan → LatencySpan+Tracker → [latency]
config → hot-path instrumentation → benchmark → vision gate+cap → manager wiring →
cache-optimized prompt → ack streaming.

**Measured (live Gemini 3.5 Flash, `scripts/latency_bench.py --real`):**

| Metric | Before | After (W0+1+2) | Note |
|---|---|---|---|
| router_decision p95 | 0.03 ms | 0.02 ms | local heuristic — trivially < 150 ms SLO |
| brain first_token p50 | 9348 ms | 8059 ms | ~14% offline; **understated** (bench wires no live awareness churn) |
| brain first_token p95 | 11891 ms | 11512 ms | first call is cold cache-creation |
| system prompt | 18.3 KB, re-keyed every turn | byte-stable static prefix | cache can now actually hit |

**The decisive finding:** the residual ~5–6 s warm TTFT is dominated by **Gemini
"thinking" time** (uncapped per the user's no-reasoning-throttle mandate), NOT
prompt processing. Wave 2 fixes the cache (proven byte-stable); the cache benefit
is larger live than in the bench (the bench has no per-turn awareness/wiki churn,
so its "before" was already partly cacheable). To get under the 3 s SLO one would
additionally need a router-tier thinking cap (a user decision) — OR rely on the
fast streaming ack (Wave 3) to bridge the 5–11 s wait.

**Flags (all default true, instant rollback by setting false):**
`[latency].enabled`, `[performance].conditional_vision`,
`[performance].cache_optimized_prompt`, `[ack_brain].streaming`.

**Tests:** 758 passed across brain/speech/telemetry/vision + ack/vision integration.
New: `test_latency*`, `test_vision_gate`, `test_image_budget`, `test_prompt_cache_layout`,
`test_conditional_vision`, `test_run_stream`. Also fixed a pre-existing stale-stub
red test (`test_ack_flow` `_hangup_event`) via defensive getattr in `_on_announcement`.

**Open follow-ups:** (1) router-tier thinking-budget decision; (2) CoreMemory
mtime-guard (micro-opt, deferred — deterministic render keeps the cache valid);
(3) wire LatencySpan/BrainTTFT into a CI SLO-gate; (4) expose the four flags in
`jarvis.toml` + `scripts/config-soll.json` (today they ride model defaults, which  <!-- i18n-allow -->
is cloud-first-correct but invisible to the maintainer's drift-guard).
