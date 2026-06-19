# Voice Boot: Fast Start + Loading Indicator — Design

**Date:** 2026-06-16
**Status:** Approved, in implementation

## Problem

The desktop app window becomes visible quickly, but the voice feature ("Hey Jarvis")
takes ~20 s afterward to become ready. During that window the user has no signal that
voice is still booting, and the delay itself is dominated by avoidable sequential work.

### Root cause (from boot-sequence analysis)

`SpeechPipeline.run()` → `_warmup()` (`jarvis/speech/pipeline.py`) runs heavy steps
**sequentially** before voice is declared ready:

1. Audio device stabilization (PortAudio polling, BUG-014 guard) — 2–4 s
2. Silero VAD + OpenWakeWord model loads — 1–3 s
3. TTS client init (network auth to Google) — 2–5 s
4. ACK phrase ("Ja?") pre-render — 2–5 s (network TTS)
5. **~20 task-ack phrases pre-rendered in a sequential loop** — 5–15 s (network TTS ×20) — the dominant cost

The actual *listening* path (wake + VAD + STT) is ready long before the confirmation-audio
cache finishes. "Voice ready" is artificially coupled to the slowest, least-critical step.

## Decisions

- **Boot strategy:** "Listen first." Voice declares ready as soon as the *critical* path is up
  (audio stable + VAD + wake + STT + TTS client). Confirmation-audio pre-render moves to the
  background.
- **Indicator:** A plain badge/spinner at the existing voice-status dot in the sidebar:
  "Voice starting…" while not ready, gone once ready.

## Architecture

### Two-phase warmup (`jarvis/speech/pipeline.py`)

**Phase A — critical, parallelized.** Run these concurrently via `asyncio.gather` instead of
in series (they are mutually independent):
- audio device stabilization (logic untouched — BUG-014 guard preserved; only moved into the gather)
- Silero VAD load + OpenWakeWord load
- TTS client init

When the slowest of Phase A completes → emit `VoiceBootStatus(ready=True)` → wake listener
active. Target: ~5 s.

**Phase B — background, fire-and-forget.** After ready is signaled, pre-render confirmation audio
concurrently (not in the critical path):
- ACK phrase "Ja?" rendered first (highest priority); if the wake word fires before it is cached,
  the existing ready/chime cue plays instead of the spoken phrase.
- ~20 task-ack phrases rendered concurrently (replace the sequential loop).

`VoiceBootStatus(ready=False)` is emitted at the very start of `_warmup()`.

### Contract (the seam between the two agents — FROZEN)

**Event** (`jarvis/core/events.py`):
```python
@dataclass(frozen=True, slots=True)
class VoiceBootStatus(Event):
    ready: bool = False
    detail: str = ""   # optional human-readable note, not a UI enum
```
Flows to the browser automatically via the existing wildcard `_forward` subscriber
(`jarvis/ui/web/server.py`) → `event_to_ws_envelope` → WS `event_name: "VoiceBootStatus"`,
`payload: { ready, detail }`.

Deliberately a **bool**, not a string enum — avoids the multi-layer enum-drift trap (AP-4/BUG-008).

**REST** (`jarvis/ui/web/server.py`): `GET /api/voice/status` → `{ "ready": <bool> }`.
Required because WS events are not persistent: a frontend that connects *after* the ready event
must still learn the current state on mount. Server subscribes to `VoiceBootStatus` and stores
`app.state.voice_ready` (default `False`); the endpoint returns it.

### Frontend (Agent 2)

- `store/events.ts`: add `voiceReady: boolean` (default `false`) + setter.
- `hooks/useWebSocket.ts`: on `VoiceBootStatus`, call `setVoiceReady(payload.ready)`.
- REST `GET /api/voice/status` fetched on mount (mirrors the `useBrainStatus` REST+WS pattern)
  to seed the initial value.
- `components/layout/Sidebar.tsx`: while `connected && !voiceReady`, show a subtle
  "Voice starting…" state with a spinner at the existing voice-status dot; revert to the normal
  voice-state label once `voiceReady`.
- English i18n source keys only (artifact-language policy).

## Testing

- **Backend (pytest, TDD):** Phase-A parallelization, `VoiceBootStatus(ready=False→True)` emission
  order, background pre-render does not block ready, `GET /api/voice/status` reflects state, chime
  fallback when ACK not yet cached. Per-phase timing logs for a measurable before/after.
- **Frontend (vitest, TDD):** store transition, WS-event handling, REST-seed on mount, Sidebar
  badge render in loading vs ready.
- **UI end-to-end:** the Chrome Checkout Loop drives the running app — verify the badge appears on
  boot and clears once ready, with no console errors / failed requests.
- **Live boot proof:** requires `POST /api/settings/restart-app` (interrupts active sessions /
  missions) — timing confirmed with the maintainer before pulling it.

## Non-goals

- No change to the audio-stabilization logic itself (BUG-014 guard stays intact).
- No new string enum / no `SystemStateChanged` state added.
- No multi-phase progress UI (plain badge only).
