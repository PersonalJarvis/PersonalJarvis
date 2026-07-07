# TTS Quality & Provider Curation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship only vetted, natural, low-latency TTS voices — a hard allowlist over the aggregated catalog, an objective eval suite, Inworld as the new premium default, and a native-premium-first / OpenRouter-last fallback.

**Architecture:** A dependency-light `curated_catalog` module is the single source of truth; the OpenRouter aggregate list and the UI voice picker are filtered through it. A new Inworld httpx plugin (Cartesia-shaped) becomes the default. An offline eval suite scores every model on WER / naturalness / drift / latency and feeds the allowlist. Three AP-22 fallback leaks are fixed.

**Tech Stack:** Python 3.11+, httpx, pydantic, pytest (fakes, not mock), onnxruntime (torch-free MOS floor), faster-whisper (round-trip WER, `[local-voice]`/eval extra).

## Global Constraints

- **English-only artifacts** (code, comments, docstrings, tests, docs). de/en/es strings only inside the product surface / test fixtures, `# i18n-allow` on load-bearing lines.
- **Cross-platform, torch-free base.** Eval ML deps live in a new optional extra; the DNSMOS(onnxruntime)+faster-whisper path is the headless floor. Never break `pip install` on `python:3.11-slim`.
- **AP-22:** every tier resolves through one key-aware chain that skips keyless/dead families and crosses families; primary+fallback never the same family. Gate on capability, never a hardcoded provider name.
- **AP-11:** no LLM call in `scrub_for_voice`. **Streaming-first:** TTS methods return `AsyncIterator[AudioChunk]`.
- **Plugins** structurally typed against `TTSProvider` (`jarvis/core/protocols.py`), no `jarvis.*` import in the plugin *module top-level* only where the entry-point rule requires; existing plugins DO import `jarvis.core` — follow the Cartesia pattern exactly.
- Tests run with `.venv/Scripts/python.exe -m pytest`. Commit hunk-isolated (shared tree), never `git add -A`.

---

### Task 1: Curated catalog module — DONE (commit aecea3f7)

`jarvis/plugins/tts/curated_catalog.py` + `tests/unit/plugins/tts/test_curated_catalog.py`.
Produces: `is_allowed(family, model_id, voice=None)`, `allowed_models(family=None, language=None) -> list[ModelEntry]`, `allowed_voices(family, model_id, language=None) -> list[VoiceEntry]`, `allowed_openrouter_model_ids(list[str]) -> list[str]`, `ModelEntry(family, model_id, quality_tier, languages, latency_class, streaming, voices, status)`, `VoiceEntry(id, language)`, `MULTILINGUAL`.

---

### Task 2: Inworld TTS plugin

**Files:**
- Create: `jarvis/plugins/tts/inworld_tts.py`
- Test: `tests/unit/plugins/tts/test_inworld_tts.py`

**Interfaces:**
- Consumes: `AudioChunk` (`jarvis/core/protocols.py`), `cfg.get_secret` (`jarvis/core/config.py`), `DEFAULT_LOCALE` (`jarvis/core/turn_language.py`), SAPI5 helper from `gemini_flash_tts`.
- Produces: `InworldTTS(default_voice_de=..., default_voice_en=..., default_voice_es=..., model="inworld-tts-2", language="auto", speed=1.0, allow_sapi5_fallback=False)`, `name="inworld"`, `supports_streaming=True`, `async synthesize(text, voice=None, language_code=None) -> AsyncIterator[AudioChunk]`, `list_voices(language=None) -> list[str]`, `INWORLD_TTS_SAMPLE_RATE=24000`, `INWORLD_TTS_ENDPOINT`, `INWORLD_TTS_STREAM_ENDPOINT`, `DEFAULT_VOICE_DE/EN/ES`, `strip_wav_header(bytes) -> bytes`.

API facts (mid-2026): `POST https://api.inworld.ai/tts/v1/voice` (non-stream) + `/tts/v1/voice:stream` (NDJSON). Auth `Authorization: Basic <INWORLD_API_KEY>` (key already base64, do NOT re-encode). Body `{text, voiceId, modelId, language(BCP-47), audioConfig:{audioEncoding:"LINEAR16", sampleRateHertz:24000, speakingRate}}`. Non-stream response `{"audioContent": base64}` — **WAV-wrapped PCM**, strip the 44-byte RIFF header to get raw s16le. Stream: NDJSON lines `{"result":{"audioContent": base64-chunk}}`. Voices multilingual: DE Josef/Johanna, EN Dennis/Ashley, ES Diego/Lupita. ≤2000 chars/request. 401/403/429 → cooldown+fallback.

- [ ] **Step 1: failing test** — `strip_wav_header` removes a RIFF header; `synthesize` with a fake httpx client yields one `AudioChunk` (raw PCM, 24 kHz) for a mono base64-WAV `audioContent`; `list_voices("de")` returns `["Josef", ...]`; missing key → falls back (no raise). Use a fake transport (`tests/fakes`), not real HTTP.
- [ ] **Step 2: run — expect fail** (`InworldTTS` undefined).
- [ ] **Step 3: implement** the plugin, Cartesia-shaped: `_resolve_voice(text, override, language_code)` (per-lang default voices, `_detect_lang_from_text` reuse via a small local copy or import from cartesia), `_ensure_client` with the Basic header, `_synthesize_one` (POST non-stream, b64decode, `strip_wav_header`), sentence chunking + parallel synth, `_fallback` via the **key-aware chain** (Task 3 helper), 900 s cooldown on 401/403/429. `strip_wav_header`: if bytes start with `b"RIFF"` and contain `b"data"`, return bytes after the `data`+size (8) offset; else return unchanged.
- [ ] **Step 4: run — expect pass.**
- [ ] **Step 5: commit** `feat(tts): add Inworld TTS plugin (arena-#1 realtime, new default)`.

---

### Task 3: Factory integration + AP-22 fallback fixes

**Files:**
- Modify: `jarvis/plugins/tts/__init__.py` (aliases, `_TTS_SECRET_CANDIDATES`, `_TTS_CROSS_FAMILY_ORDER`, `_build_provider`, `build_tts_from_config` same-family guard, new `resolve_keyed_fallback` helper)
- Modify: `jarvis/plugins/tts/{cartesia,elevenlabs,grok_voice}_tts.py` (`_fallback` → key-aware chain instead of hardcoded `GeminiFlashTTS`)
- Modify: `jarvis/core/config_writer.py` (`_TTS_DEFAULTS` add `inworld`)
- Test: `tests/unit/plugins/tts/test_inworld_factory.py`, extend `tests/unit/plugins/tts/test_tts_keyaware_fallback.py`

**Interfaces:**
- Produces: `_INWORLD_ALIASES = {"inworld", "inworld-tts", "inworld-tts-2"}`; `_canonical_tts_name("inworld*") -> "inworld"`; `_TTS_SECRET_CANDIDATES["inworld"] = (("inworld_api_key","INWORLD_API_KEY"),)`; `_TTS_CROSS_FAMILY_ORDER = ("inworld","gemini-flash-tts","elevenlabs","cartesia","grok-voice","openrouter")`; `resolve_keyed_fallback(exclude_family, tts_cfg) -> tuple[str, cfg] | None` (first key-having family ≠ excluded, in cross-family order).

- [ ] **Step 1: failing tests** — (a) `provider="gemini"`, `fallback="gemini-flash-tts"` builds a bare provider, NOT `FallbackTTS` (canonical same-family guard); (b) a config with only an `INWORLD_API_KEY` set and `provider="cartesia"` crosses to `inworld` via the factory; (c) `resolve_keyed_fallback("cartesia", cfg)` returns a family the host has a key for, skipping keyless Gemini.
- [ ] **Step 2: run — expect fail.**
- [ ] **Step 3: implement** — add Inworld to aliases/candidates/order/`_build_provider` (build `InworldTTS` with per-lang voices from config `[tts.inworld]` or the plugin defaults); canonicalize both sides before the `fallback_name == primary_name` guard; add `resolve_keyed_fallback`; rewrite the three plugins' `_fallback` to call it (build the returned family, else SAPI5/mute) instead of importing `GeminiFlashTTS` unconditionally; add `_TTS_DEFAULTS["inworld"]`.
- [ ] **Step 4: run — expect pass**, plus `test_tts_defaults_parity.py`, `test_tts_keyaware_fallback.py`, `test_fallback_tts.py` still green.
- [ ] **Step 5: commit** `fix(tts): key-aware internal fallback + canonical same-family guard; wire Inworld`.

---

### Task 4: OpenRouter aggregate filter (drop the slop)

**Files:**
- Modify: `jarvis/brain/model_catalog.py` (`TTS_CATALOG["openrouter-tts"]` → allowlisted ids only; drop dead `openai-tts`/`google-neural2` legacy rows or mark them; keep gemini/grok/cartesia/elevenlabs rows)
- Modify: `jarvis/plugins/tts/openrouter_tts.py` (`list_voices` / model validation already narrows per model — ensure `coerce_speech_model` + listing pass through `curated_catalog.is_allowed`)
- Modify: `jarvis/ui/web/provider_routes.py` `list_tts_voices` (filter model list via `allowed_openrouter_model_ids`)
- Test: `tests/unit/plugins/tts/test_openrouter_curation.py`, extend `tests/review/test_ui_routes.py`

**Interfaces:** consumes `curated_catalog.allowed_openrouter_model_ids`, `is_allowed`.

- [ ] **Step 1: failing test** — the OpenRouter model pick list contains the four KEEP ids and NONE of {kokoro, orpheus, csm-1b, zonos×2}; a raw live-catalog list filtered through the boundary keeps only allowed ids in order.
- [ ] **Step 2: run — expect fail.**
- [ ] **Step 3: implement** — replace the 9-id `TTS_CATALOG["openrouter-tts"]` list with the four allowlisted ids; route any listing/coercion through `allowed_openrouter_model_ids`.
- [ ] **Step 4: run — expect pass.**
- [ ] **Step 5: commit** `feat(tts): filter OpenRouter catalog through the allowlist (drop 5 slop models)`.

---

### Task 5: Cross-provider voice picker + preview

**Files:**
- Modify: `jarvis/ui/web/provider_routes.py` (`_VOICE_PICKER_PROVIDER` gate removed; `/tts/voices`, `/tts/preview` serve every allowed family via `curated_catalog.allowed_voices` + the active provider's `list_voices`)
- Test: extend `tests/review/test_ui_routes.py`

**Interfaces:** consumes `curated_catalog.allowed_voices(family, model_id, language)`, `_canonical_tts_name`.

- [ ] **Step 1: failing test** — `GET /tts/voices?provider=inworld` returns Inworld's curated voices (200, not 400); `?provider=elevenlabs` returns ElevenLabs voices; an unknown provider is a clean 400; `/tts/preview` for `inworld` returns `audio/wav` (with a fake TTS) instead of 400.
- [ ] **Step 2: run — expect fail.**
- [ ] **Step 3: implement** — generalize both routes: resolve the family via `_canonical_tts_name`, list voices from `curated_catalog.allowed_voices` (fallback to the built provider's `list_voices` narrowed by `is_allowed`), build the preview through `build_tts_from_config` for that provider rather than hardcoding `OpenRouterTTS`.
- [ ] **Step 4: run — expect pass.**
- [ ] **Step 5: commit** `feat(tts): cross-provider voice picker + preview (not OpenRouter-only)`.

---

### Task 6: Objective eval suite (offline)

**Files:**
- Create: `jarvis/speech/tts_eval/__init__.py`, `corpus.py`, `metrics.py`, `harness.py`, `__main__.py`
- Create/modify: `pyproject.toml` — new `[project.optional-dependencies] tts-eval` (faster-whisper, plus DNSMOS onnxruntime already in base)
- Test: `tests/unit/speech/tts_eval/test_corpus.py`, `test_metrics_gate.py`, `test_harness_fake.py`

**Interfaces:**
- Produces: `HARD_CORPUS: tuple[EvalItem, ...]` (de/en/es incl. numbers/acronyms/code/long-passage/proper-names); `EvalItem(id, language, text, tags)`; `Thresholds(wer_max=0.06, dnsmos_min=3.0, drift_min=0.85, ttfa_ms_max=300, rtf_max=1.0)`; `MetricResult(wer, mos, drift, ttfa_ms, rtf)`; `gate(result, thresholds) -> tuple[bool, list[str]]` (pass + failed-metric reasons); `evaluate(synth_fn, items, metrics, thresholds) -> EvalReport`. `synth_fn` and the metric backends are injectable (fakes in tests → no model download).

- [ ] **Step 1: failing tests** — corpus covers all three languages and the five hard-input tags; `gate` fails when `wer > 0.06` and lists `"wer"`; `evaluate` with a fake synth + fake metrics returns per-item pass/fail and an overall verdict; import path works torch-free (metrics backends lazy-imported).
- [ ] **Step 2: run — expect fail.**
- [ ] **Step 3: implement** — `corpus.py` (the curated hard set, `# i18n-allow` on the de/es fixture lines); `metrics.py` (protocol-typed backends: `WerBackend` via faster-whisper lazy, `MosBackend` via DNSMOS-onnx lazy, `DriftBackend` speaker-embedding lazy, `LatencyBackend` timing; each guarded so a missing dep degrades to `None` + logged, never crashes); `harness.py` (`evaluate` + `gate` + `Thresholds`); `__main__.py` CLI that builds providers via `build_tts_from_config` and writes `data/tts_eval/latest.json`.
- [ ] **Step 4: run — expect pass** (torch-free, fakes only).
- [ ] **Step 5: commit** `feat(tts): objective offline eval suite (WER/MOS/drift/latency gates)`.

---

### Task 7: Verify in Chrome (checkup loop)

After Tasks 2–5 land, run the `chrome-checkup-loop` skill against the desktop Settings → Voice/TTS view: confirm the provider list, the curated model picker (no slop), the cross-provider voice picker + preview all work with no console errors / failed requests / broken interactions. Fix whatever it surfaces; re-run until one clean pass.

## Self-Review notes
- Spec §3.1 → T1; §3.2 filter → T4; §3.3 picker → T5; §3.4 Inworld → T2/T3; §3.5 fallback fixes → T3; §3.6 eval → T6; §3.7 anti-slop/latency measures → covered by T6 gates + T4; Chrome verify → T7.
- Threshold values copied verbatim from spec §3.6 (WER ≤ 6 %, DNSMOS OVRL ≥ 3.0, drift cosine ≥ 0.85, TTFA ≤ 300 ms, RTF < 1.0).
