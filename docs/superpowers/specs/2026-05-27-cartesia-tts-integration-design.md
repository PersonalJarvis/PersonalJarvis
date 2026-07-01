# Cartesia TTS Integration — Design

**Date:** 2026-05-27
**Author:** Jarvis-Agents (background session)
**Status:** Pending user review
**Related plan:** `~/.claude/plans/also-er-muss-auch-lexical-pond.md` (Phase L.4 — Streaming TTS) <!-- i18n-allow: literal filename identifier, must match the real file -->
**Related code-pattern:** `jarvis/plugins/tts/grok_voice_tts.py` (template)

---

## 1. Goal

Add Cartesia.ai as a fully functional TTS provider option in Personal Jarvis, selectable
in the desktop app under **API Keys → TTS**, alongside Gemini Flash TTS and Grok Voice.

Success criteria from the user:

1. Cartesia appears in the API Keys view, the API key can be entered and saved.
2. Selecting Cartesia as active TTS provider routes synthesis through the new plugin.
3. Verified by screenshot of the API Keys view with the Cartesia card visible.

## 2. Current state

- **Entry-point stub exists** in `pyproject.toml:149`:
  `cartesia-sonic3 = "jarvis.plugins.tts.cartesia_sonic3:CartesiaSonic3TTS"`
- **Plugin module does NOT exist** — `jarvis/plugins/tts/cartesia_sonic3.py` is missing.
- **`ProviderSpec` entry missing** in `jarvis/ui/web/provider_spec.py:PROVIDERS` — without
  it, the API Keys UI cannot render the Cartesia card, even if the plugin is implemented.
- **Stray TOML comment** in `jarvis.toml:100-102` mentions an unused `voice_id` for an
  imagined active `cartesia-sonic3` — comment-only, no actual `[tts.cartesia]` section.

The integration is therefore: write the missing plugin, register it in the UI spec,
add a config section, build the frontend, verify.

## 3. Cartesia API contract (confirmed via docs)

- **Endpoint:** `POST https://api.cartesia.ai/tts/bytes` (unary, returns raw audio body)
- **Auth:** `Authorization: Bearer sk_car_...`
- **Required header:** `Cartesia-Version: 2026-03-01` (date-based version pin)
- **Body (minimal):**
  ```json
  {
    "model_id": "sonic-3.5",
    "transcript": "Hallo, ich bin Jarvis.",
    "voice": { "mode": "id", "id": "<voice-uuid>" },
    "output_format": {
      "container": "raw",
      "encoding": "pcm_s16le",
      "sample_rate": 24000
    },
    "language": "de"
  }
  ```
- **Output:** raw `pcm_s16le @ 24000 Hz mono` — **bit-identical** to what `sounddevice`
  already consumes from Gemini Flash TTS and Grok Voice. No decoder, no resampling.
- **Languages:** Sonic-3.5 supports 42 languages including German (`de`) and English
  (`en`). Matches the bilingual-default user preference.

## 4. Architecture decisions

### AD-CT1 — Plugin name decouples from model generation

The pre-existing entry-point name `cartesia-sonic3` couples plugin identity to a model
generation. Sonic-3.5 already ships; sonic-4 will follow. We **rename** the entry-point
to `cartesia` and keep the class `CartesiaTTS`. Model selection becomes a config value
(`[tts.cartesia].model_id`), matching the pattern used by `gemini-flash-tts` (whose
plugin name never embedded `gemini-3.1`).

### AD-CT2 — Dedicated secret slot, not shared

Cartesia is its own organization with its own billing. There is no Cartesia brain
provider in Jarvis to share a key with (unlike the xAI Grok case, where one token
serves brain + voice). The secret slot is therefore `cartesia_api_key` (env fallback
`CARTESIA_API_KEY`), stored in the Windows Credential Manager under service
`personal-jarvis`. This keeps the API Keys card unambiguous in the UI.

### AD-CT3 — Pseudo-streaming first, real SSE later

Cartesia exposes both a unary `/tts/bytes` endpoint and an SSE-streaming endpoint with
~90 ms TTFB. The unary path is **structurally identical** to the existing Grok-Voice
implementation (same return type, same PCM format, same sentence-chunking parallel
synthesis). Picking the unary path first means:

- Zero new dependencies (no SSE client gymnastics).
- Same fallback chain shape as Grok (Cartesia → Gemini → optional SAPI5).
- Same `_GrokFatalError`-style cooldown (15-min) on 401/403/429.

Real SSE is a follow-up if latency measurements (`scripts/voice_compare.py`) show the
unary path lags Grok by more than ~150 ms TTFB. Architecture-wise, SSE would replace
the body of `_synthesize_one()` and keep the public `synthesize()` API unchanged.

### AD-CT4 — Three-layer config pin (BUG-010 defense)

A second concurrent Claude session has historically rewritten `jarvis.toml` and rolled
back provider settings (BUG-010, three episodes). Any new TTS provider config must land
in **all three** layers simultaneously:

1. `jarvis.toml` — declared config (`[tts.cartesia]`).
2. `scripts/config-soll.json` — drift-guard target the daemon re-applies every 5 min. <!-- i18n-allow: literal filename identifier -->
3. Environment variable hint (documented in the new section's comment).

Without this pin, the daemon will silently roll back any cartesia-related edit.

### AD-CT5 — Fallback chain mirrors Grok

`Cartesia → Gemini Flash TTS → (optional) SAPI5`. The Gemini step reuses the existing
helper `jarvis.plugins.tts.gemini_flash_tts.GeminiFlashTTS`; the SAPI5 step reuses the
existing `_sapi5_synthesize` function. This means **zero new fallback infrastructure**
and keeps the AP-2/AD-OE6 invariant (never silently drop a voice turn).

### AD-CT6 — Default voice is configurable, not hardcoded

Cartesia's voice catalog uses opaque UUIDs (e.g. `694f9389-aac1-45b6-b726-9d9369183238`).
Hard-coding any UUID in code creates a hidden coupling to Cartesia's library state. The
plugin reads `voice_id` from `[tts.cartesia].voice_id`. Default in `jarvis.toml` is a
well-known stable Cartesia example voice; the user can swap it in the TOML or via a
runtime API later. Documenting the swap path in the section header comment is part of
the deliverable.

## 5. Implementation plan (delegated to writing-plans)

The implementation plan that follows this design will cover, in this order:

1. **Plugin module** `jarvis/plugins/tts/cartesia_tts.py` (class `CartesiaTTS`, name
   `"cartesia"`, structural copy of `GrokVoiceTTS` with Cartesia-specific endpoint,
   payload shape, and error mapping).
2. **Entry-point rename** in `pyproject.toml:149`
   (`cartesia-sonic3` → `cartesia`) + `pip install -e . --no-deps` to make it active.
3. **`ProviderSpec` entry** in `jarvis/ui/web/provider_spec.py:PROVIDERS` —
   `id="cartesia"`, `tier="tts"`, `auth_mode="api_key"`,
   `secret_keys=("cartesia_api_key",)`, `dashboard_url="https://play.cartesia.ai/keys"`.
4. **Config section** `[tts.cartesia]` in `jarvis.toml` with sensible defaults:
   `model_id="sonic-3.5"`, `language="auto"`, `voice_id="<documented Sarah UUID>"`,
   `chunk_by_sentence=true`, `speed=1.0`, `allow_sapi5_fallback=false`, plus
   inline help comments.
5. **Drift-guard pin** — add the same key/value triplet to `scripts/config-soll.json`. <!-- i18n-allow: literal filename identifier -->
6. **Unit tests** `tests/unit/plugins/tts/test_cartesia_tts.py`:
   - `test_synthesize_yields_pcm_24k_mono` (mocked httpx, 200 OK, single sentence)
   - `test_multiple_sentences_yield_in_order` (parallel synth, order preserved)
   - `test_401_triggers_cooldown_and_fallback` (Cartesia → Gemini path)
   - `test_429_triggers_cooldown_and_fallback`
   - `test_empty_body_does_not_raise` (soft-fail to fallback)
   - `test_missing_voice_id_raises_at_construction` (fail-fast config error)
   - `test_list_voices_returns_configured_id`
7. **Frontend build** `npm --prefix jarvis/ui/web/frontend run build`.
8. **Verification:** launch the desktop app, screenshot the API Keys view with the
   Cartesia card visible. Optionally run a smoke synthesis through
   `scripts/voice_compare.py` (cartesia-only) once a real key is present.

## 6. Out of scope (explicitly not in this PR)

- **Real SSE streaming** for Cartesia (see AD-CT3 — follow-up).
- **Cartesia voice cloning** (uses a different endpoint, requires its own UI flow).
- **Pronunciation dictionaries** (`pronunciation_dict_id` field — sonic-3+ feature,
  separate UX surface).
- **A/B latency benchmark vs. Gemini/Grok** (covered by existing
  `scripts/voice_compare.py` once the plugin is live).
- **Switching the default TTS provider away from Gemini** — Cartesia is added as an
  *option*, not promoted to default. User mandate from
  `feedback_tts_voice_consistency` keeps Gemini Flash TTS as Vertex-bound primary.

## 7. Risk register

| Risk | Mitigation |
|---|---|
| Cartesia returns 200 OK with empty body (silent failure) | Soft-fail to Gemini via the same `b""`-check used in `grok_voice_tts.py`. AD-OE6 invariant preserved. |
| Drift-guard rolls back the `[tts.cartesia]` section | AD-CT4 pins all three layers (jarvis.toml + config-soll.json + ENV doc). | <!-- i18n-allow: literal filename identifier -->
| User accidentally activates Cartesia without a key | `ProviderSpec.auth_mode="api_key"` + `secret_keys=("cartesia_api_key",)`; `ApiKeysView` already shows "needs key" badge and refuses activation (see `ProviderCard.activate` in `ApiKeysView.tsx:135`). |
| Plugin module name collides with Cartesia's official Python SDK (`cartesia`) | We import lazily inside `_ensure_client()` only and use `httpx` directly. No top-level `import cartesia`. |
| voice_id becomes invalid (Cartesia removes the example voice) | Plugin logs the upstream `400 invalid voice_id` body verbatim, falls back to Gemini. User updates `[tts.cartesia].voice_id`. |

## 8. Acceptance criteria

- [ ] `python -m jarvis --plugins` lists `cartesia` in the `jarvis.tts` group.
- [ ] `GET /api/providers` returns a `cartesia` descriptor with `tier="tts"`.
- [ ] API Keys view renders a Cartesia card under the TTS section.
- [ ] Entering a key + clicking the activation radio switches `[tts].provider` to
      `"cartesia"` and persists it (verified via `jarvis.toml` diff).
- [ ] Unit tests pass: `pytest tests/unit/plugins/tts/test_cartesia_tts.py -v`.
- [ ] Existing TTS tests still pass:
      `pytest tests/unit/plugins/tts/ tests/contract/ -v`.
- [ ] Frontend build clean: `npm --prefix jarvis/ui/web/frontend run build` exits 0.
- [ ] Screenshot captured showing the Cartesia card in the API Keys view.

## 9. Spec self-review notes

- No placeholders. The one variable left open (the actual default voice UUID) is
  explicitly documented as a config value with a doc-comment, not a code constant.
- No internal contradictions: the fallback chain is consistent across §4 (AD-CT5),
  §5 (test cases), and §7 (risk mitigation).
- Scope is single-PR: ~250 LOC plugin + 20 LOC config + 30 LOC UI spec + ~150 LOC
  tests. No decomposition needed.
- No ambiguity: every "where" is a concrete file path; every "what" is a named
  symbol or config key.
