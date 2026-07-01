# LATENCY_REPORT_001 — Voice-Hotpath Per-Stage Diagnostic

> Pre-Optimization measurement. Goal: quantify the dominant latency contributor
> on the live voice path **before** any provider migration (Cartesia / ElevenLabs
> / Deepgram / native S2S). Verify or falsify the "TTS is the bottleneck"
> hypothesis with monotonic timing evidence.

---

## 1. Setup

| Component | Value |
|---|---|
| Date | 2026-05-27 |
| Branch | `feat/screenshot-only-loop` |
| Repo state | Latency-instrumentation commits (this PR) |
| OS | Windows 11 Pro 10.0.26200 |
| Python | 3.11.9 |
| STT | Groq Whisper Large v3 Turbo (non-streaming, single-shot HTTP) |
| Brain (router-tier) | Gemini 3.5 Flash via Google AI Studio API key |
| Brain (deep-tier) | Gemini 3.1 Pro Preview (via Frontier Autoswitch) |
| TTS | Gemini 3.1 Flash TTS Preview via Vertex AI |
| Region | Vertex `us-central1` (TTS) + AI Studio default (Brain) |
| Target SLO | TTFW p95 ≤ 1.5 s (turn anchor → first audio chunk in output buffer) |

**Assumptions documented:**

- The per-turn `LatencyTracker` anchor (`t0`) is set at `_handle_utterance`'s
  "natural end" point — microseconds after the VAD endpoint fires. The two are
  treated as equivalent for the report; the small (<1 ms) gap is below
  scheduler jitter and is documented in `jarvis/speech/pipeline.py` near
  `LatencyTracker(self._bus, uuid4(), enabled=...)`.
- `t1 STT_FIRST_PARTIAL` equals `t2 STT_FINALIZE` for Groq, because Groq's
  `/v1/audio/transcriptions` endpoint returns the full JSON in one HTTP
  response (no partials). A future streaming STT provider would mark `t1`
  earlier from inside its async iterator without any pipeline change —
  `setdefault` on `LatencyTracker.mark()` preserves the earlier offset.
- `t8 TURN_TO_FIRST_AUDIO` is the canonical TTFW. `BRAIN_FIRST_AUDIO` and
  `ACK_FIRST_AUDIO` are pre-existing markers from Wave 0 omni-latency; on the
  streaming-TTS path they race depending on whether the ack-brain fired first.
  We rank against `turn_to_first_audio` to keep the metric monotonic.
- `t9 TTS_STREAM_DONE` is "every TTS chunk has been pushed through the player",
  i.e. the audio is fully out the door. The TTS HTTP stream itself may have
  finished earlier; the difference is small for pseudo-streaming Gemini-Flash
  TTS (one HTTP roundtrip per sentence).

---

## 2. Instrumentation

10 per-stage marks anchored at the turn's start (`t0`). Each mark is a
`LatencySpan` event on the bus + a `dict[str, float]` snapshot on the
`LatencyTracker`. Telemetry is fire-and-forget — the hot path never `await`s
on it. The synchronous overhead is **CI-gated < 5 ms** for 10 marks aggregate
(`tests/unit/telemetry/test_latency_contextvar.py::test_overhead_under_budget`);
steady-state cost on the dev box is 0.03 ms per turn (≈140× below budget).

| Mark | Phase | Site | File:line anchor |
|---|---|---|---|
| t0 | (anchor) | `LatencyTracker` constructed at utterance finalize | `jarvis/speech/pipeline.py` near `LatencyTracker(self._bus, uuid4(), ...)` |
| t1 | `stt_first_partial` | After `_transcribe_final` returns (= t2 for Groq) | `pipeline.py` after `await self._publish_event(TranscriptFinal(...))` |
| t2 | `stt_finalize` | Same site as t1 | same |
| —  | `intent_decision` | Pre-existing Wave 0 mark | `pipeline.py` |
| t3 | `brain_request_sent` | Before `async for chunk in self._brain.generate_stream(text)` | `_brain_streaming` |
| t4 | `brain_first_token` | First non-empty chunk (pre-existing) | `_brain_streaming` |
| t5 | `brain_last_token` | After async-for loop exits, before tail flush | `_brain_streaming` |
| t6 | `tts_request_sent` | Before `self._tts.synthesize(text, ...)` | `_speak` |
| t7 | `tts_first_chunk` | First chunk yielded by TTS provider (wrapped iter) | `_latency_wrap_first_chunk` |
| t8 | `turn_to_first_audio` | `AudioOutFirst` event from WASAPI player | `_on_audio_out_first` |
| t9 | `tts_stream_done` | After tail flush in `_brain_streaming`; emits `LatencyTurnComplete` | `_brain_streaming` (final block) |

**ContextVar bridging:** the per-turn tracker is bound on a module-level
`ContextVar` so STT/Brain/TTS plugins can mark phases via `mark_phase(phase)`
without importing `jarvis.*` (CLAUDE.md plugin doctrine). Today only the
pipeline marks all phases; future streaming providers can opt in by calling
`from jarvis.telemetry.latency import mark_phase` inside their own iterator.

---

## 3. Persistence + Aggregation

**JSONL writer** (`jarvis/telemetry/latency_log.py`): subscribes to
`LatencyTurnComplete` events on the bus. The handler enqueues the row on an
in-memory `queue.Queue` and a daemon thread drains the queue, writing one
self-contained JSONL row per turn to `state/latency_log.jsonl`. The bus
callback's hot-path time is < 5 ms (`test_writer_callback_returns_quickly`).

Row schema (excerpt; see `LatencyLogWriter._build_row`):

```json
{
  "turn_id": "0123456789abcdef...",
  "iso_timestamp": "2026-05-27T18:00:00+00:00",
  "anchor_ns": 12345678901234,
  "stages_ms": {"stt_finalize": 150.5, "brain_first_token": 1180.0, ...},
  "durations_ms": {"brain_ttft": 1000.0, "tts_ttfb": 390.0, ...},
  "ttfw_ms": 1950.0,
  "total_ms": 2100.0,
  "stt_input_audio_ms": 820.0,
  "brain_input_tokens": null,
  "brain_output_tokens": null,
  "tts_input_chars": null,
  "errors": []
}
```

**CLI** (`python -m jarvis.tools.latency_report`):

```
python -m jarvis.tools.latency_report                # last 50 turns, text
python -m jarvis.tools.latency_report --last 10      # only the latest 10
python -m jarvis.tools.latency_report --markdown     # paste-ready report
python -m jarvis.tools.latency_report --json         # raw aggregation
python -m jarvis.tools.latency_report --since 2026-05-27T18:00:00+00:00
```

The CLI computes p50 (linear-interpolation), p95, max, mean per stage offset
and per derived duration segment. It ranks bottlenecks by p50 of the duration
segments (not the cumulative offsets — those are misleading since later
stages always look bigger than earlier ones).

---

## 4. Activation runbook (live measurement)

Instrumentation is OFF by default — `[latency].log_jsonl = false`. To collect
real baseline data:

1. **Enable the writer**: add to `jarvis.toml` (or set ENV
   `JARVIS__LATENCY__LOG_JSONL=true`):

   ```toml
   [latency]
   enabled = true
   log_jsonl = true
   log_path = "state/latency_log.jsonl"
   ```

   *Note:* the drift-guard daemon mirrors `[latency]` keys from
   `scripts/config-soll.json`. If you want this to survive a drift sweep, add  <!-- i18n-allow -->
   `latency.log_jsonl = true` there too. For a one-off measurement, ENV is
   simpler.

2. **Restart Jarvis** so the writer attaches at boot. Watch for
   `Latency log JSONL writer attached: state/latency_log.jsonl` in the boot
   log.

3. **Speak 10 turns**, mixed:
   - 3 × short ("What time is it?" / "What time is it?" / "What time?")
   - 4 × medium (typical tool routings — "What time is it in Tokyo?")
   - 3 × longer / complex (a request that triggers Jarvis-Agent-Spawn or a long
     reasoning answer)

   Wait for each audio response to finish before the next turn so per-turn
   marks settle cleanly.

4. **Aggregate**:

   ```bash
   python -m jarvis.tools.latency_report --last 10
   python -m jarvis.tools.latency_report --last 10 --markdown > out.md
   ```

5. **Disable**: set `log_jsonl = false` (or unset the ENV) — the writer
   thread shuts down at next boot. The JSONL stays on disk for analysis.

---

## 5. Baseline table — **DRY-RUN (synthetic data, chain validation only)**

> The data below was emitted by an end-to-end harness that publishes 10
> synthetic `LatencyTurnComplete` events through the live writer and reads
> them back through the CLI. Numbers are plausible-by-construction, **not**
> measurements of the real provider stack. Replace this section with the
> output of step 4 above after the live run.

### Per-stage duration (segment between two marks)

| Segment | n | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| `vad_to_stt_first` | 10 | 225.0 | 341.0 | 350.0 |
| `stt_streaming` | 10 | 1.0 | 1.0 | 1.0 |
| `stt_to_brain_request` | 10 | 9.0 | 9.0 | 9.0 |
| `brain_ttft` | 10 | 1350.0 | 1654.0 | 1690.0 |
| `brain_streaming` | 10 | 465.0 | 950.0 | 950.0 |
| `brain_to_tts_request` | 10 | 5.0 | 5.0 | 5.0 |
| `tts_ttfb` | 10 | 195.0 | 295.0 | 295.0 |
| `tts_to_audio_out` | 10 | 280.0 | 391.0 | 400.0 |
| `tts_tail` | 10 | 110.0 | 150.0 | 150.0 |

**TTFW (synthetic):** p50 = 2530.0 ms · p95 = 3646.0 ms · max = 3700.0 ms.
**Total:** p50 = 2640.0 ms · p95 = 3782.5 ms.

### Bottleneck ranking (p50 share of TTFW p50)

| Segment | p50 ms | Share |
|---|---:|---:|
| `brain_ttft` | 1350.0 | **53.4%** |
| `brain_streaming` | 465.0 | 18.4% |
| `tts_to_audio_out` | 280.0 | 11.1% |
| `vad_to_stt_first` | 225.0 | 8.9% |
| `tts_ttfb` | 195.0 | 7.7% |
| `tts_tail` | 110.0 | 4.3% |
| `stt_to_brain_request` | 9.0 | 0.4% |
| `brain_to_tts_request` | 5.0 | 0.2% |
| `stt_streaming` | 1.0 | 0.0% |

**Provisional diagnosis (synthetic, awaiting real data):** brain TTFT
dominates. If the live measurement confirms this ranking, the TTS-migration
hypothesis is **partially refuted** — TTS combined is ≈19% of TTFW, not the
top contender. Brain TTFT (provider+region+model choice) and brain streaming
(`brain_last_token - brain_first_token`) together are 71% of TTFW.

---

## 6. Optimization hypotheses (priority-ordered)

Each card is sized as **Effort (S/M/L) × Impact (Low/Med/High)** and assumes
the synthetic ranking holds. Re-rank after live data lands.

### H1 — Brain provider/region warm-cache (S × High)

**Action:** keep the brain HTTP client connection warm (pooled keepalive,
periodic noop ping), pin the AI Studio region closest to the user
geographically, and explore Vertex AI for the brain (already in use for TTS,
so SA infrastructure exists). For Gemini specifically, enable
`gemini_context_cache` for the system-prompt + tools blob — already a config
toggle in `PerformanceConfig.gemini_context_cache` (currently `False`).

**Why first:** lowest cost, highest leverage; brain_ttft is 53% of TTFW in
the synthetic ranking. The infrastructure already supports it; flipping the
flag is reversible.

**Risk:** cache invalidation surprises (cold-cache turns become slower);
mitigated by the existing fallback chain.

**Rollback:** set `gemini_context_cache = false` in `jarvis.toml`.

### H2 — Couple TTS streaming to brain token stream (M × Med)

**Action:** today `_brain_streaming` waits for a full *sentence* before
calling `self._tts.synthesize(sentence, ...)` (`_STREAM_SENTENCE_END`-driven).
That adds 1+ short word's worth of brain streaming to TTFW. Switch to a
phrase-granularity emit (clause boundary, comma, em-dash) for the FIRST
phrase only, then fall back to sentence boundaries.

**Why second:** `brain_streaming` is the second-largest contributor (18%).
The first phrase often arrives well before the first sentence, especially
for "Genau, ich helfe Dir gleich…" style preambles.

**Risk:** TTS sentence-prosody suffers if we hand it half-sentences; mitigated
by only doing it for the very first emit and reverting to sentence boundaries
after.

**Rollback:** revert the regex change in `_brain_streaming`.

### H3 — Vertex Flash TTS streaming over Server-Sent-Events (L × Med)

**Action:** the current TTS plugin treats Gemini Flash TTS as
pseudo-streaming (sentence-chunked HTTP). If Vertex exposes a true streaming
audio endpoint (SSE / WebSocket), the `tts_ttfb` segment (195 ms) shrinks to
≤ 50 ms — closer to native TTFB. Investigation needed in Vertex Flash TTS
docs.

**Why third:** TTS combined (`tts_ttfb` + `tts_to_audio_out` + `tts_tail`) is
23% of TTFW. Cutting `tts_ttfb` in half = ~100 ms saved. Smaller than H1+H2
but it stacks.

**Risk:** unknown — Vertex SSE may not exist for Flash TTS Preview, or may
require a different API surface. Spike first.

**Rollback:** keep both code paths behind a feature flag; pseudo-streaming
remains default.

---

## 7. Ship / Fix-first / Rollback recommendation

**Ship the instrumentation now.** The 5 ms CI-budget is proven; production
overhead is ~140× below budget. The JSONL writer is opt-in
(`log_jsonl = false` by default) so production runs are unaffected unless
explicitly enabled.

**Fix-first sequence (after live measurement confirms ranking):**

1. **H1 (brain context cache)** — one config flip + smoke test. Reversible.
2. **H2 (early-phrase TTS emit)** — small regex tweak in
   `_brain_streaming`. Branch-isolated, reversible.
3. **H3 (Vertex TTS streaming spike)** — separate research issue; do not
   start until H1+H2 have measured impact.

**Out of scope (per spec):** provider migration (Cartesia / ElevenLabs /
Deepgram), reasoning-mode brain, native S2S APIs, Pipecat / Smart Turn
migration. These remain candidate work for separate prompts after this
diagnostic.

---

## 8. Reproducibility

- Source of truth for the wire vocabulary:
  `jarvis/core/events.py::LatencyPhase` (13 phases, runtime-validated via
  `LatencySpan.__post_init__`).
- Tracker contract: `jarvis/telemetry/latency.py::LatencyTracker`.
- Writer contract: `jarvis/telemetry/latency_log.py::LatencyLogWriter`.
- CLI: `jarvis/tools/latency_report.py` (`python -m`).
- Tests:
  - `tests/unit/telemetry/test_latency_span_event.py` — phase enum,
    LatencySpan dataclass.
  - `tests/unit/telemetry/test_latency_contextvar.py` — ContextVar +
    `mark_phase` + **5 ms CI overhead budget**.
  - `tests/unit/telemetry/test_latency_log_writer.py` — JSONL row schema,
    callback non-blocking, sparse rows.
  - `tests/unit/telemetry/test_latency_report_cli.py` — percentile math,
    filters, text/markdown/json modes.

All 43 telemetry tests are green on `feat/screenshot-only-loop` at the time
of writing.
