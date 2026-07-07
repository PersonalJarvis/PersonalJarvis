# TTS Quality & Provider Curation — Design

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Scope:** Analysis + architecture. Non-destructive: no model/provider code is
deleted; low-quality models become *unlisted*, not removed.

---

## 1. Goal & context

Deliver a high-quality, natural assistant voice — no "AI-sounding" artifacts,
no lags — and enforce it with a **hard allowlist** so only vetted, high-quality
models/voices are ever selectable. The aggregated catalog (OpenRouter's
`?output_modalities=speech` list) currently drags along low-quality open-source
models; they must disappear from the selectable surface.

Languages are equal per the repo language doctrine: **de / en / es** are all
first-class in every ranking and test.

### Decisions locked in (from brainstorming)

- **Cloud-only.** No local/offline neural TTS in this effort. SAPI5 remains the
  opt-in emergency brake only.
- **Latency *and* quality.** Real streaming for time-to-first-audio, but every
  shipped model must clear an objective quality gate.
- **Hard allowlist.** Only vetted models/voices are selectable; everything else
  vanishes from UI and selection.
- **Native premium first, OpenRouter last.** The cross-family fallback order
  puts native premium families ahead of OpenRouter, which is the last resort
  before SAPI5.
- **New provider: Inworld only** in this rollout; **Inworld becomes the
  recommended premium default.** (Hume Octave 2 / Gradium are deferred.)

---

## 2. Current state (relevant facts)

- **Five TTS families** wired through `build_tts_from_config`
  (`jarvis/plugins/tts/__init__.py`): Gemini Flash TTS (default), ElevenLabs,
  Cartesia (Sonic), Grok Voice, OpenRouter TTS.
- **Key-aware cross-family fallback** already exists (AP-22):
  `_resolve_keyed_tts_provider`, `_TTS_SECRET_CANDIDATES`,
  `_TTS_CROSS_FAMILY_ORDER`.
- **The only "aggregated" catalog** is OpenRouter's speech-model list
  (`openrouter_speech_models.py`), curated loosely via `CURATED_SPEECH_MODELS`
  / `MODEL_VOICES` — still includes low-quality models.
- **No cross-provider voice catalog.** Voice listing + preview REST endpoints
  are hard-gated to OpenRouter only (`_VOICE_PICKER_PROVIDER`,
  `provider_routes.py`).
- **No objective eval.** Two subjective, Gemini-only seeds exist:
  `jarvis/speech/voice_compare.py` (plays Gemini voices, dumps WAVs to
  `data/voice_compare/`) and `scripts/voice_compare.py` (10 de/en persona
  scenarios, text only). `data/voice_compare/` and `assets/audio/` are empty.
- **Config:** one shared `[tts]` block (`TTSConfig`, `jarvis/core/config.py`),
  `extra="allow"` for per-provider sub-tables; `streaming=True` default.

### Competitive landscape (mid-2026, researched 2026-07-07)

The field moved: the incumbents (ElevenLabs, OpenAI) are no longer the quality
apex. Independent blind-preference arenas are led by Inworld, MiniMax, xAI Grok,
Cartesia, and Gemini 3.1.

**Shortlist for a natural, low-latency de/en/es assistant voice:**

| Rank | Model | Rationale | In Jarvis? |
|---|---|---|---|
| 1 | Inworld TTS 1.5 Max | Arena-#1 realtime, P90 TTFA <250 ms, true streaming, cheapest top tier, native de/en/es | **missing** |
| 2 | Cartesia Sonic 3.5 | 188 ms TTFA, 42 langs, faithful readback, arena top-3 | yes |
| 3 | Gemini 3.1 Flash TTS | arena #2, ~$0.012/1k (≈4× cheaper), 70+ langs | yes (default) |
| 4 | ElevenLabs Flash/Turbo v2.5 | most robust, best-documented streaming | yes |
| 5 | Grok TTS | #1 on the Vapi "humanness" blind test | yes |
| 6 | Hume Octave 2 | most expressive/emotional, ~100 ms, de/es | deferred |

**Slop to discard** (real-time headline voice): OpenAI tts-1/-hd (1–2 s, batch),
PlayHT/Play 3.0 (being sunset by Meta), Rime (latency variance), Azure
Standard/Polly (synthetic), Deepgram Aura-2 (highest WER). Benchmarks consulted:
Coval latency (via Gradium, 2026-05-05), Artificial Analysis Speech Arena, TTS
Arena v2, Vapi Humanness Index (2026-07-07).

**OpenRouter catalog verdicts (mid-2026):**

| Model | de/en/es | Verdict |
|---|---|---|
| google/gemini-3.1-flash-tts-preview | yes | **KEEP** |
| x-ai/grok-voice-tts-1.0 | yes | **KEEP** |
| microsoft/mai-voice-2 | yes | **KEEP** |
| mistralai/voxtral-mini-tts-2603 | de/es | **KEEP (verify mini vs full on eval)** |
| hexgrad/kokoro-82m | en-centric, de weak | **DISCARD** |
| canopylabs/orpheus-3b-0.1-ft | en strong, de/es research | **DISCARD** |
| sesame/csm-1b | en, experimental | **DISCARD** |
| zyphra/zonos-v0.1-transformer | no es, beta, GPU | **DISCARD** |
| zyphra/zonos-v0.1-hybrid | no es, beta, GPU | **DISCARD** |

---

## 3. Design

### 3.1 Curated catalog module (single source of truth)

New module, provider-agnostic, dependency-light (no `httpx`, no `jarvis.*`
runtime imports — same discipline as `openrouter_speech_models.py`):

`jarvis/plugins/tts/curated_catalog.py`

It holds one entry per vetted **model** and per vetted **voice**, each carrying:

- `family` (canonical provider name, e.g. `inworld`, `gemini-flash-tts`)
- `model_id`
- `quality_tier` (`S` | `A`)
- `languages` (subset/superset of de/en/es and others)
- `latency_class` (`realtime` | `standard` | `batch`)
- `streaming` (true streaming vs pseudo)
- `last_eval` (score snapshot: WER per lang, naturalness MOS, drift, TTFA/RTF,
  eval date) — populated by the eval suite (§3.2)
- `status` (`allowed` | `provisional` | `unlisted`)

Provides pure predicates the rest of the system calls:

- `is_allowed(family, model_id, voice) -> bool`
- `allowed_models(family=None, language=None) -> list[...]`
- `allowed_voices(family, model_id, language=None) -> list[{id, language}]`

**Tiers:** S = Inworld, Cartesia, Gemini 3.1 Flash · A = ElevenLabs Flash/Turbo,
Grok, (Hume when added). OpenRouter models are allowed only for the four KEEP
ids above.

### 3.2 OpenRouter aggregate filter

The live `?output_modalities=speech` fetch (and the offline
`CURATED_SPEECH_MODELS` snapshot) is filtered through
`curated_catalog.is_allowed`. A model not on the allowlist never reaches the UI
picker. The five DISCARD models drop out; nothing is deleted from
`openrouter_speech_models.py` (kept for provenance), it is only screened at the
listing boundary. This directly satisfies "throw away the slop, ship only the
best."

### 3.3 Cross-provider voice picker

Remove the OpenRouter-only hard gate (`_VOICE_PICKER_PROVIDER`) in
`provider_routes.py`. `GET /provider/tts/voices` and `/tts/preview` must serve
**every allowed family** via `curated_catalog.allowed_voices`, not just
OpenRouter. Each provider plugin's `list_voices()` stays the runtime source;
the catalog narrows it to allowed entries.

### 3.4 Inworld plugin

New `jarvis/plugins/tts/inworld_tts.py`, structurally typed against
`TTSProvider` (`jarvis/core/protocols.py`): `name="inworld"`,
`supports_streaming=True`, emits s16le mono PCM @ 24 kHz to match the playback
layer. True WebSocket/chunked streaming (Inworld's strength), per-turn
`language_code` honored (BCP-47 from the turn-language resolver). Register in
`_build_provider` with aliases, add `INWORLD_API_KEY` to
`_TTS_SECRET_CANDIDATES`, add per-provider defaults to `_TTS_DEFAULTS`
(`config_writer.py`) with parity test.

### 3.5 Fallback chain & degradation (AP-22 fixes)

New cross-family order (native premium first, OpenRouter last):

```
inworld → gemini-flash-tts → elevenlabs → cartesia → grok-voice → openrouter → (SAPI5, opt-in)
```

Three real robustness leaks the research surfaced, fixed here:

1. **Same-family guard is string-only.** `build_tts_from_config` compares
   `fallback_name == primary_name` as raw strings; a `gemini` / `gemini-flash-tts`
   pair builds a single-family brick. Fix: canonicalize both via
   `_canonical_tts_name` before the equality guard.
2. **Hardcoded `GeminiFlashTTS` internal fallback** in cartesia/elevenlabs/grok
   plugins. A user whose only key is Cartesia falls, on quota, to a keyless
   (mute) Gemini. Fix: route the internal stage-1 fallback through the same
   key-aware chain (skip keyless/dead families, cross to whatever the user
   actually has), never a name-hardcoded family.
3. Degradation ends at the **opt-in SAPI5** brake or an honest logged mute —
   never a silent swap.

### 3.6 Evaluation suite (objective, offline)

New package `jarvis/speech/tts_eval/` (offline, off the hot path), CLI entry
(e.g. `python -m jarvis.speech.tts_eval`). Cross-platform; the torch-free path
(DNSMOS via onnxruntime + `faster-whisper` CPU) runs on a headless VPS, the
richer path (UTMOSv2) when torch is present.

**Corpus** (`tts_eval/corpus.py`): representative de/en/es texts, built on the
existing 10 persona scenarios, plus a deliberate **hard set**: numbers,
acronyms, code tokens, one long passage (drift), and de/en/es proper names.

**Metrics** (backbone: the VERSA toolkit, which bundles WER + MOS + speaker-sim):

| Metric | Catches | Acceptance threshold |
|---|---|---|
| Round-trip ASR error (text → TTS → Whisper → compare) on the hard set | slurring, dropped/hallucinated words — primary anti-slop signal | **WER ≤ 6 % per language** (target ≤ 4 %) — hard gate |
| Naturalness MOS (UTMOSv2; DNSMOS OVRL torch-free fallback) | "sounds synthetic/flat" | ranking + floor **DNSMOS OVRL ≥ 3.0** |
| Speaker-embedding cosine similarity across chunks | voice drift on long answers | **cosine ≥ 0.85** — hard gate |
| TTFA + RTF | lags | **TTFA ≤ 300 ms, RTF < 1.0** — hard gate |

A model failing any hard gate is `unlisted`/`provisional`, never `allowed`.
Periodically, a heavier **TTSDS2** batch audit runs as ground-truth to re-rank
the shortlist (best human correlation, too heavy per-utterance).

### 3.7 Anti-slop & latency measures (measurable)

- True streaming across all premium providers (some are pseudo-streaming today)
  → measured **TTFA ≤ 300 ms** on the conversation path.
- Hard WER gate in eval → **no model with WER > 6 % is ever `allowed`**.
- Voice-consistency knobs (seed/temperature/whole-utterance) extended to the new
  provider → **drift cosine ≥ 0.85**.
- CI latency regression test with a **p95 TTFA budget** so no update slows the
  first syllable (aligns with the existing boot-budget gate discipline).

---

## 4. Rollout plan (phases, non-destructive)

1. **Eval suite** — metrics + hard corpus; baseline every current model.
2. **Curated catalog module + OpenRouter filter** — slop leaves the picker;
   nothing deleted.
3. **Inworld plugin** — fills the #1 gap; becomes the recommended premium
   default; vetted by the eval before it is shipped as default.
4. **Fallback-leak fixes** (§3.5) + cross-provider voice picker (§3.3).
5. *(Deferred)* Hume Octave 2 for emotional range.

---

## 5. Acceptance criteria (done when)

- Competition and options are evaluated, with a ranked shortlist and explicit
  keep/discard criteria (§2). ✔ captured in this spec.
- The eval suite exists, runs cross-platform (torch-free path on headless),
  scores every current + new model on the four metrics, and emits per-model
  pass/fail against the thresholds in §3.6.
- The curated catalog is the single source of truth; the OpenRouter aggregate
  and the UI picker show only `allowed` entries; the five DISCARD models are
  no longer selectable.
- Inworld is integrated, passes the eval, and is the recommended default; the
  cross-family fallback order is native-premium-first, OpenRouter-last, with the
  three AP-22 leaks fixed.
- Measured **TTFA ≤ 300 ms** on the conversation path for the default voice;
  **WER ≤ 6 %/lang**, **drift cosine ≥ 0.85** for every `allowed` model.

## 6. Non-goals

- No local/offline neural TTS (Piper/Kokoro-local) — cloud-only this round.
- No deletion of existing provider/model code — low-quality models are unlisted,
  not removed.
- No new voice cloning / custom-voice training.
- Hume Octave 2 and Gradium are deferred to a later stage.

## 7. Risks & open points

- **Arena Elo snapshots drift weekly** — the shortlist is a point-in-time
  ranking; the eval suite (not the marketing arena) is the durable gate.
- **Inworld is a young product** — vet hard via the eval before it becomes the
  shipped default; keep Cartesia/ElevenLabs as immediate cross-family fallback.
- **Voxtral mini vs full** — verify the mini variant on the eval; prefer the
  full model if naturalness lags.
- **Gemini preview RPD caps + SynthID watermark** — the Vertex path and the
  sibling-bridge already mitigate; keep documented.
- **VERSA / UTMOSv2 pull torch** — keep the DNSMOS + faster-whisper torch-free
  path as the headless floor so the eval runs on a bare VPS.

---

## 8. Key files

- Factory / fallback: `jarvis/plugins/tts/__init__.py`
- OpenRouter data: `jarvis/plugins/tts/openrouter_speech_models.py`
- New curated catalog: `jarvis/plugins/tts/curated_catalog.py` *(new)*
- New Inworld plugin: `jarvis/plugins/tts/inworld_tts.py` *(new)*
- New eval suite: `jarvis/speech/tts_eval/` *(new)*
- Protocol: `jarvis/core/protocols.py` (`TTSProvider`)
- Config: `jarvis/core/config.py` (`TTSConfig`), `jarvis/core/config_writer.py`
  (`_TTS_DEFAULTS`, `set_tts_provider`)
- Voice REST + picker gate: `jarvis/ui/web/provider_routes.py`
- UI pick lists: `jarvis/brain/model_catalog.py` (`TTS_CATALOG`)
- Pipeline TTS path / latency: `jarvis/speech/pipeline.py`
- Existing eval seeds: `jarvis/speech/voice_compare.py`, `scripts/voice_compare.py`
