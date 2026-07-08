# API-Keys two-mode redesign + Gemini Live realtime provider — Design

**Date:** 2026-07-08
**Status:** approved (brainstorming), pending spec review → writing-plans

## Goal

Restructure the **API Keys & Providers** desktop screen from one flat tab row
into **two top-level modes**, and add **Gemini Live** as a second real realtime
provider next to the already-shipped OpenAI Realtime.

- **Pipeline mode** (the classic STT→brain→TTS engine) shows: Brain · Voice
  Output · Voice Input · Jarvis-Agents · Advanced.
- **Realtime mode** (a single full-duplex speech-to-speech model) shows:
  Realtime · Jarvis-Agents · Advanced.

Realtime replaces STT+Brain+TTS with one model, so those three tabs do not
apply in Realtime mode — that is the whole reason for the split.

## Background (already shipped, do not rebuild)

- `jarvis.realtime` plugin group + orchestrator (`jarvis/realtime/*`: scrub-hold
  gate, session, factory) + OpenAI Realtime provider
  (`jarvis/plugins/realtime/openai_realtime.py`, id `openai-realtime`).
- `[voice].mode` (`pipeline`|`realtime`) + `[brain].realtime` (`BrainTierConfig`)
  config already exist.
- A `tier="realtime"` provider category already exists in `provider_spec.py` +
  `provider_routes.py` (`_spec_to_payload` realtime branch, `_active_realtime`,
  section-health `realtime`, `POST /api/realtime/switch`,
  `config_writer.set_realtime_provider`), and a frontend "Realtime" tab was
  added to the flat row. **This design MOVES that Realtime tab under a new
  Realtime *mode*** and adds Gemini Live beside OpenAI Realtime.

## Decisions

### D1 — The mode switch is a VIEW switch, not a live-engine switch (BINDING)

A segmented control at the top of the API-Keys screen — **Pipeline | Realtime**
— switches only which set of provider tabs is shown. It **must NOT** write
`[voice].mode` or otherwise flip the running voice engine.

**Why:** the realtime audio path is not wired yet (Phase 2). If selecting the
"Realtime" segment flipped `[voice].mode=realtime`, the user's live voice would
break the moment they tapped it to *look*. Configuration and activation are
separate. The segment that matches the current `[voice].mode` carries a small
**"Active"** badge so the user always sees which engine is live. Actual
activation stays the existing deliberate, gated path (Settings / the
`brain.reply_language`-style pins), unchanged by this work.

### D2 — Realtime mode lists exactly two providers: OpenAI Realtime + Gemini Live

OpenRouter is NOT a realtime provider: it exposes discrete TTS
(`/api/v1/audio/speech`) and STT (`/api/v1/audio/transcriptions`) endpoints —
already surfaced in Pipeline mode as OpenRouter Voice Output / Voice Input —
but no full-duplex realtime session. It stays a Pipeline-mode provider. The
Realtime tab shows `openai-realtime` (reuses `openai_api_key`) and the new
`gemini-live` (reuses `gemini_api_key`).

### D3 — Gemini Live is a real backend provider, key-aware factory

Add `jarvis/plugins/realtime/gemini_live.py` implementing the same realtime
provider protocol as `openai_realtime.py`, over the google-genai **Live API**
(`client.aio.live.connect`, 16 kHz PCM in / 24 kHz PCM out). The realtime
factory (`jarvis/realtime/factory.py`) becomes **key-aware and cross-family**
(AP-22): it builds the `[brain.realtime].provider` if that provider's key is
present, else crosses to the other realtime family whose key IS present, else
returns `None` (caller falls back to the classic pipeline). Gate on
capability/key presence, never a hardcoded provider name (AP-21).

### D4 — Cross-OS + off-the-boot-path (BINDING)

- The `google-genai` Live import is **lazy**, inside the adapter methods only —
  never at module load or on the boot path (AP-26). Same discipline as
  `openai_realtime.py`'s lazy `openai` import.
- No key / no SDK → a clean, logged no-op / `can_open_duplex_session()==False`,
  never a crash. Base `pip install` + boot on headless `python:3.11-slim` must
  be unaffected (`google-genai` already ships in the base deps — confirm it is
  not moved behind an extra).
- The Realtime section-health for `gemini-live` is **credential-presence only**
  (no live probe), exactly like `openai-realtime`.

## Components

### Frontend (`jarvis/ui/web/frontend/src/`)

1. **`views/ApiKeysView.tsx`** — introduce a `VoiceEngineMode = "pipeline" |
   "realtime"` view-state (default `"pipeline"`, or seeded from the current
   `[voice].mode` for the "Active" badge, but NEVER written back). Render the
   segmented **Pipeline | Realtime** control above the tab row. The visible tab
   set is derived from the mode:
   - `pipeline` → `["brain","tts","stt","subagents","advanced"]`
   - `realtime` → `["realtime","subagents","advanced"]`
   Reuse the existing `CategoryTabs` / `ProviderCategory` / `SubagentCategory` /
   `AdvancedCategory` components — only the *tab list* and the mode wrapper are
   new. The Realtime provider cards render via the existing tier path.
2. **i18n** (`i18n/locales/{en,de,es}.json`) — `apikeys_view.mode_pipeline`,
   `apikeys_view.mode_realtime`, an `apikeys_view.mode_active_badge`, and a
   `gemini-live` card label if not catalog-driven. Real de/es translations;
   identical key sets across locales.
3. Keep `useProviders`/`useSectionHealth` as-is (already generic over tiers +
   `Record<string,SectionHealth>`).

### Backend

4. **`jarvis/ui/web/provider_spec.py`** — add a `gemini-live` `ProviderSpec`
   (`tier="realtime"`, `auth_mode="api_key"`, `secret_keys=("gemini_api_key",)`,
   `alt_credential=_GEMINI_VERTEX` reused, honest `credential_help`). No new
   `Tier` value (realtime already exists).
5. **`jarvis/plugins/realtime/gemini_live.py`** — the Gemini Live adapter
   (RealtimeProvider protocol: `name="gemini-live"`, `supports_realtime`,
   `can_open_duplex_session()` = key present, `open_session` → google-genai Live
   connect, `send_audio`/`receive`/`update_session`/`truncate`/`close`),
   mapping wire events to the shared `RealtimeEvent` union. Lazy import. Register
   its entry-point in `pyproject.toml` under `jarvis.realtime`.
6. **`jarvis/realtime/factory.py`** — key-aware cross-family resolution over
   `[brain.realtime].provider` + the two realtime families, honest `None` when
   neither key is present.
7. **`provider_routes.py`** — no structural change needed; `/realtime/switch`,
   `_active_realtime`, section-health already handle any `tier="realtime"`
   provider generically. Verify the `gemini-live` card activates + section-health
   reports for it.

## Testing

- Backend unit/contract: `gemini-live` spec present + tier; `gemini_live.py`
  passes `tests/contract/` realtime provider contract (mirror the openai one);
  factory key-aware selection (OpenAI-only key → openai; Gemini-only key →
  gemini; neither → None; the configured provider wins when both keyed); a
  fake/mocked google-genai Live so no network. `POST /realtime/switch
  gemini-live` 200 with a Gemini key, 409 without.
- Frontend vitest: `ApiKeysView` renders the segmented control; Pipeline mode
  shows 5 tabs (no Realtime), Realtime mode shows 3 tabs (Realtime + Agents +
  Advanced); the "Active" badge tracks the seeded mode; switching the segment
  does NOT call any `voice-mode` mutation. i18n parity.
- Cross-OS: the `google-genai` import stays lazy (an import-time test / boot-
  budget guard); no-key path is a logged no-op.

## Global Constraints (verbatim for reviewers)

- **D1 is binding:** the segment switch NEVER writes `[voice].mode` / flips the
  live engine. A test must assert switching the segment fires no voice-mode
  mutation.
- **D2:** only `openai-realtime` + `gemini-live` in Realtime mode; OpenRouter is
  not a realtime provider.
- **Capability-gated, cross-family (AP-21/22):** never pin realtime to a
  provider name; the factory crosses families by key presence and degrades to
  `None` (→ pipeline) honestly.
- **Off boot path / cross-OS (AP-26, §3):** `google-genai` Live import is lazy;
  no-key/no-SDK/headless is a clean no-op; base boot unaffected.
- **English-only artifacts;** de/es i18n VALUES are the allowed localized copy.
- **Turn-language resolver:** any realtime session language derives from
  `resolve_output_language`, never re-derived per layer.
- **Default-OFF unchanged:** `[voice].mode` stays `pipeline` by default; this
  work does not enable realtime as the live engine anywhere.

## Out of scope (explicit)

- Full Phase 2 browser audio-client wiring (the realtime engine still isn't
  end-to-end usable after this).
- Desktop flagship realtime voice path.
- Making the segment switch activate the live engine (deliberately excluded —
  D1).
- Any OpenRouter "realtime" card (D2).
