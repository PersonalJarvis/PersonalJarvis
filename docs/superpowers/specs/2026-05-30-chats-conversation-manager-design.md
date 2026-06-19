# Chats Conversation Manager — Design

**Date:** 2026-05-30
**Status:** Approved (scope confirmed by maintainer: unified text+voice history, two-pane ChatGPT-style layout)
**Author:** Claude Code session

## Goal

Add a first-class **"Chats"** experience to the desktop app: a section where the user sees their
conversation history (text *and* voice), can re-open any past conversation and **continue it by
typing**, and can press **"Speak in this conversation"** to start a Jarvis voice session that already
has that conversation loaded into the brain — i.e. exactly like saying "Hey Jarvis", but Jarvis
remembers where you left off.

## Problem / current state (verified in code)

- **Text chat is ephemeral.** `jarvis/state/chat_store.py` is an in-memory dict (`_threads`), a single
  `"default"` thread, lost on restart. Its own docstring says SQLite persistence was always the Phase-2
  plan ("Persistenz (SQLite) kommt in Phase 2 zusammen mit `jarvis.memory.recall`").
- **Voice sessions are already durable.** `data/sessions.db` (`voice_sessions`/`voice_turns`/`voice_events`),
  exposed via `GET /api/sessions` + `GET /api/sessions/{id}` (`jarvis/ui/web/sessions_routes.py`) and an
  existing `SessionsView`.
- **The brain history is a single global buffer.** `BrainManager._history: list[BrainMessage]`
  (`jarvis/brain/manager.py:608`), capped at 40, with `clear_history()` (2723) but **no** seed method.
  It is *not* keyed by thread — so re-opening an old text chat will not make the brain "remember" it
  unless we seed the buffer.
- **Voice session start hangs on `self._call_event`** (`jarvis/speech/pipeline.py:2475`), set by the wake
  word and PTT. There is **no** bus event / API to start listening yet — but there is a clean precedent
  (`VoiceMuteToggleRequested`, commented "future hotkey/REST").
- **Single event loop.** uvicorn (FastAPI routes), the speech pipeline, `ChatStore`, and the brain all run
  on the *same* orchestrator loop in desktop mode (`server.py:71`; `desktop_app.py:986/1018/1020`). The
  TTS-switch route already calls `pipeline.set_tts()` directly — same-loop calls into the pipeline are an
  established pattern. (Headless/VPS mode has **no** pipeline — voice features must degrade to a 503.)

## Non-goals (YAGNI)

- No unification of the two on-disk schemas. Voice sessions stay in `sessions.db`; text chats get their
  own persistence. The "unified list" is a read-time merge, not a storage migration.
- No per-thread brain instances. One global brain buffer + seed-on-activate is correct for a single-user
  desktop app with one active conversation at a time.
- No retro-migration of the old in-memory `"default"` thread.

## Architecture

Five vertical slices, built in order. Each is independently testable.

### Slice 1 — Brain seeding primitive
`BrainManager.seed_history(turns)` replaces `_history` with a capped list of prior turns, accepting
`(role, text)` pairs or `BrainMessage`s. Sits next to `clear_history()`. No LLM call, no I/O — safe for
the voice path. This single primitive powers both "continue by text" (seed the web brain) and "Speak in
this conversation" (seed the pipeline brain).

### Slice 2 — Durable, segmented text chats
Back `ChatStore` with SQLite (`data/chats.db`: `chat_threads` + `chat_messages`) while keeping the exact
async API (`create_thread`/`ensure_thread`/`add_message`/`list_threads`/`get_thread`) so launcher +
desktop wiring is unchanged except for an `await store.open()` at boot. Adds: `updated_at_ms`, a derived
`preview` (first user message), title auto-derivation, ordering newest-first, optional
`prune_older_than(days)`. Bus publishing (`ThreadCreated`/`MessageSent`) is preserved so the
`MessageRecorder` → recall path is unaffected.

### Slice 3 — Unified index + REST (`jarvis/ui/web/chats_routes.py`)
- `conversation_kind` vocabulary as a documented frozenset + parity test (open `str`, **not** a Pydantic
  `Literal` — mirrors the BUG-008 lesson in `sessions/models.py`).
- `GET /api/chats?days=N` → merged `ConversationSummary[]` (text threads + voice sessions), newest-first.
  `days` is a soft recent-window filter (default: all). Each item: `kind`, `id`, `title`, `preview`,
  `created_ms`, `updated_ms`, `message_count`.
- `GET /api/chats/{kind}/{id}` → `ConversationDetail` with a normalized `messages: [{role, text, ts_ms}]`
  (voice turns flattened to user/assistant message pairs).
- `POST /api/chats` → create a new empty text thread (returns its id).
- `POST /api/chats/{kind}/{id}/resume` → seed `app.state.brain` (web brain) with the conversation and
  return its messages so the UI makes it the active conversation.
- `POST /api/chats/{kind}/{id}/speak` → seed the pipeline brain + arm the mic. **503** when
  `app.state.speech_pipeline` is absent (headless/VPS).
- `DELETE /api/chats/text/{id}` → delete a text thread.
- DI via `getattr(request.app.state, X, None)` + 503 (the established pattern). Registered in
  `server.py` next to the other routers.

### Slice 4 — Pipeline voice-spawn entry point
`SpeechPipeline.request_voice_session(*, seed_messages=None) -> bool`: if the brain supports
`seed_history`, seed it; then, guarded by `_activation_allowed()` + the post-hangup wake-lock + a
`state == IDLE` check, set `self._call_event` (same-loop, safe). Returns whether a session was armed.
The `/speak` route calls this directly (TTS-switch precedent). Wake-style (`_ptt_mode=False`), so the
session behaves like "Hey Jarvis".

### Slice 5 — Frontend two-pane Chats manager
Evolve `ChatsView.tsx` into: **left** a history list (fetch `/api/chats`, grouped by day, type badge
💬/🎙, "New chat" button, active highlight); **right** the active conversation (existing live-chat
message list + `ChatInput`, plus a "Speak in this conversation" button in the header). New zustand state
in `store/events.ts`: `conversations`, `activeThreadId`, and actions `loadConversations`,
`selectConversation` (calls `/resume`), `newChat` (calls `POST /api/chats`). `ChatInput` sends the active
`thread_id`. i18n keys in `en/de/es`. Matte-black/gold Tailwind per existing views. `npm run build` +
app restart to take effect (pywebview caches the bundle in RAM).

## Data flow

```
Resume (text):  click chat → POST /resume → seed web brain + load messages active
                → user types → existing MessageSent WS round-trip (thread_id) → coherent reply
Speak (voice):  click "Speak in this conversation" → POST /speak
                → pipeline.request_voice_session(seed_messages) → seed pipeline brain
                → _call_event.set() → mic opens (like wake) → Jarvis continues by voice
List:           GET /api/chats → merge ChatStore.list_threads() + SessionStore.list_sessions()
```

## Error handling & invariants

- Voice features 503 cleanly without a pipeline (cloud-first doctrine: voice is a desktop extra).
- Seeding is pure in-memory, regex/LLM-free → never on or blocking the voice critical path (AP-9/AP-11).
- Every new subprocess/bus rule respected; `EventBus` subscriber exceptions stay swallowed (AP-18).
- `conversation_kind` uses the documented-frozenset + parity-test pattern (BUG-008 defense).
- Output artifacts are English (code/comments/strings/commit); chat reply to the user stays German.

## Testing

`seed_history` unit; `ChatStore` SQLite round-trip + segmentation + ordering; unified-index merge/sort;
`conversation_kind` parity; `chats_routes` happy paths + 503s; `request_voice_session` arms `_call_event`
and seeds the brain; frontend events-store reducer. Then: full `pytest` subset, `npm run build`, restart,
live verification in the running app.
