# Chat Mic Dictation — Design Spec

**Date:** 2026-05-31
**Branch:** `feat/chat-mic-dictation`
**Status:** approved-for-implementation

## Goal

A microphone button inside the chat composer that runs the project's **own STT
model** and streams the transcript **live into the chat text input** (text
appears progressively as the user speaks, Claude-style). The transcript is
**not** auto-dispatched to the brain — it lands in the input box, where the user
edits and sends it manually (Enter / send button).

This is **dictation**, not a voice turn. It must never trigger the Router-Brain,
TTS, or a mission. It is orthogonal to the existing "Hey Jarvis" voice path.

## Non-goals

- True word-by-word streaming (the active STT — Groq / faster-whisper — is
  non-streaming; partials are poll-based at ~1 s cadence). Word-level streaming
  would need a streaming STT (e.g. Deepgram Flux) and is out of scope here.
- Browser-mic capture. This version uses the **server-side** mic (the user asked
  for "our STT model"). On a headless VPS with no server mic the button is
  hidden / disabled — graceful no-op (CLOUD.md doctrine).

## Architecture

### The isolation principle (critical)

The live voice path (`SpeechPipeline._handle_utterance` → brain) is the
single highest-risk surface in the codebase (BUG-020 class: silent voice
breakage). **We do not add a flag inside `_handle_utterance`.** Instead we add a
**separate, self-contained `_dictation_session()`** on the pipeline that:

1. opens the mic (only if the pipeline is IDLE — refuse otherwise),
2. captures PCM while active,
3. periodically runs `stt.transcribe_pcm(buffer)` (~1 s cadence) and publishes
   `TranscriptionUpdate(text=..., is_final=False)`,
4. on stop, runs one final `transcribe_pcm` and publishes
   `TranscriptionUpdate(text=..., is_final=True, dictation=True)`,
5. returns — **never** calls the brain, TTS, or mission dispatch.

The existing `TranscriptionUpdate` event already fans out to all browser clients
over the WS wildcard subscriber, so **no new outbound transport is needed**.

### Distinguishing dictation transcripts from voice transcripts

`TranscriptionUpdate` is also emitted by the real voice path. To stop the chat
input from absorbing live-voice transcripts, the dictation path tags its events
with a marker (`payload.dictation = true`). The frontend only routes events with
that marker into the input box.

This is the five-layer enum-drift concern (`docs/anti-drift-three-layer.md`):
the `dictation` boolean is a new wire field. It is additive and optional, so old
clients ignore it — low drift risk, but documented here.

### WS command (client → server)

New `WSCommand.action` value `"stt_dictate"`, payload `{ "mode": "start" | "stop" }`.

- **Python source of truth:** `jarvis/ui/web/schema.py` `WSCommand.action` Literal.
- **TS mirror:** `jarvis/ui/web/frontend/src/schema/ws.ts` `WSCommand.action` enum.
- Both must be updated together (drift guard).

Server handler: new `elif cmd.action == "stt_dictate":` branch in
`server.py:_handle_command` (line ~988). It resolves the live pipeline via
`jarvis.core.runtime_refs.get_speech_pipeline()`:

- `None` (headless / voice disabled) → publish an `ErrorOccurred`
  (recoverable) + a `ToastNotification` ("dictation needs the desktop voice
  extra") and return. No crash.
- present → call `pipeline.start_dictation(session_id)` / `stop_dictation()`.

### Frontend

1. **Mic button** in the composer (`ChatInput.tsx`), to the left of send.
   - idle: outline mic icon
   - recording: filled / pulsing (reuses `animate-jarvis-pulse`)
   - click toggles → sends `{type:"command", action:"stt_dictate", payload:{mode}}`.
2. **Live-text binding:** `ChatInput` subscribes to a small store field
   `dictationText`. The WS handler (`useWebSocket.ts`) writes
   `TranscriptionUpdate` events **that carry `payload.dictation === true`** into
   `dictationText` (interim) and, on `is_final`, appends it to the textarea
   value and clears `dictationText`.
   - While recording, the textarea shows committed text + the live interim tail.
3. **Capability gate:** the mic button only renders if the backend reports a
   server mic. A `GET /api/voice/capabilities` (or an existing health flag) tells
   the UI whether dictation is available; if not, no button (cloud-first).

### Empty state (already done in main, mirror here)

The empty chat is now Claude-style: centered mascot + greeting, **no** prompt
cards. This worktree's baseline already has that. No further change unless the
user wants the composer centered in the empty state (separate follow-up).

## Files touched

| File | Change |
|---|---|
| `jarvis/speech/pipeline.py` | add `start_dictation` / `stop_dictation` / `_dictation_session` (additive; no edit to `_handle_utterance`) |
| `jarvis/ui/web/schema.py` | add `"stt_dictate"` to `WSCommand.action` |
| `jarvis/ui/web/server.py` | add `elif cmd.action == "stt_dictate":` handler |
| `jarvis/ui/web/frontend/src/schema/ws.ts` | mirror `"stt_dictate"` |
| `jarvis/ui/web/frontend/src/store/events.ts` | add `dictationText` + setter |
| `jarvis/ui/web/frontend/src/hooks/useWebSocket.ts` | route `dictation`-tagged `TranscriptionUpdate` → `dictationText` |
| `jarvis/ui/web/frontend/src/components/ChatInput.tsx` | mic button + live-text binding |

## Testing

- Unit: `pipeline.start_dictation` refuses when not IDLE; emits
  `TranscriptionUpdate(dictation=True)`; never calls brain (assert no
  `MessageSent` with role=assistant / no brain dispatch).
- Schema parity: `WSCommand.action` Literal (py) == ws.ts enum (extend the
  existing parity test if one exists; else a new one).
- Frontend: vitest — `dictation`-tagged interim updates write to `dictationText`,
  final appends to textarea; non-dictation `TranscriptionUpdate` does NOT touch
  the input.

## Biggest risk

Contaminating the live voice critical path. Mitigation: a **separate** session
method, never a branch inside `_handle_utterance`; refuse to start unless the
pipeline is IDLE; wrap the whole dictation loop in fail-open `try/except` so a
dictation error can never suppress a later real voice turn.
