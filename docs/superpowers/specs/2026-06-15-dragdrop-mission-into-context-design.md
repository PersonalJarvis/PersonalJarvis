# Drag-and-drop a mission into the live conversation context

**Date:** 2026-06-15
**Status:** Design approved (concept pre-approved by maintainer via `/goal`)

## 1. Goal & user intent

The maintainer wants to grab a sub-agent **mission / output card** (currently
click-selectable in the Outputs view) and **drag-and-drop it onto the "Jarvis
bar" / the active display style / the "Jarvis ghost"**. On drop, Jarvis should:

1. **Speak about the dropped mission** conversationally, and
2. **Pull the mission into the live conversation context window**, so the user
   can keep talking about that sub-agent task ("what did it find?", "continue
   this", "summarise the risks") and the brain has the content in `_history`.

In the maintainer's words: *"bring your sub-agent task back into the context
window."*

## 2. Key decision — the drop surface

The on-screen "Jarvis bar" and "Jarvis ghost/mascot" are **separate Tk overlay
windows** (`jarvis/ui/whisperbar/overlay.py`, the Orb/mascot surface), driven by
`[ui].orb_style ∈ {whisper_bar, mascot, none}`. They are **not** part of the
React DOM and **do not exist on a headless VPS**.

Per the **cloud-first doctrine (CLAUDE.md RULE #1)** the feature must work in any
browser on any OS, including a headless VPS with no Tk overlay. An HTML5 drag
from the pywebview browser window onto a separate Tk window is also a
cross-toolkit OS drag that is Windows-fragile.

**Decision:** the drop target is an **in-app "Jarvis presence dock"** rendered
inside the React tree (mounted globally in `App.tsx`, next to `ToastLayer`, so it
is visible across *every* view including Outputs). It visually **mirrors the
active display style**:

- `whisper_bar` → a slim bar dock
- `mascot` (and `none`) → the existing `MascotGigi` ghost component

This is the universal, testable, doctrine-compliant surface and still *feels*
like dropping onto the bar/ghost. The dock is subtle at rest and **lights up
while a mission card is being dragged**.

**Out of scope (noted, not built):** wiring the native Tk overlay windows to
accept OS-level drops. That is a Windows-only power-user extra and must never be
the *primary* surface. Can be added later behind the `[desktop]` capability.

## 3. Architecture & data flow

The feature **reuses the entire existing reply pipeline** — no new speech
plumbing, no new context-injection API. A dropped mission becomes a normal
`MessageSent(role="user")` brain turn.

```
[Outputs SessionRow] --HTML5 drag (dataTransfer JSON)--> [JarvisDock onDrop]
        |
        v
WS command  { type:"command", action:"mission.inject",
              payload:{ slug, mission_id?, thread_id? } }
        |
        v
server._handle_command → mission.inject branch
   1. fetch mission text (prompt + bounded summary/output, capped ~4000 chars)
   2. compose a clean, human-readable directive
   3. publish MessageSent(role="user",
                          source_layer="ui.web.ws.mission_inject",
                          text=<directive>, thread_id=<thread>)
        |
        v
existing _on_user_message  (launcher.py / desktop_app.py)
   → brain.generate(text)  → ResponseGenerated
        |                         |
        |                         +--> voice build: pipeline speaks reply
        |                              (scrub_for_voice → TTS)
        +--> chat: assistant reply bubble
   → mission text now in BrainManager._history  (the context window)
```

### Why `MessageSent` rather than a bespoke event

- The existing brain dispatcher already turns `MessageSent(role="user")` into a
  spoken (voice) + displayed (chat) reply, and appends to `_history`. Re-using
  it gives us "speak about it" and "in the context window" for free, on **both**
  the voice and the text surface, with zero new latency-path code (honours
  AP-11 / no LLM on the voice scrub path).
- The composed directive is bounded and well-formed, so it reads naturally as a
  conversational turn.

### The composed directive (backend)

Human-readable, language-aware (mirror the mission language), e.g.:

> Pull the sub-agent task "<utterance>" (status: <status>) into our
> conversation. Here is what it produced:
>
> <bounded summary / output excerpt>
>
> Give me a short recap and let's talk about it.

Bounded to a hard char cap (~4000) to protect the token budget and the
`_WS_SEND_TIMEOUT_S` circuit-breaker.

## 4. Components & files

### Frontend (`jarvis/ui/web/frontend/src/`)
- **`components/JarvisDock.tsx` (new)** — the global presence dock. Reads the
  active overlay style (`useOverlayStyle`), renders a bar or `MascotGigi`,
  handles `onDragOver` (highlight) + `onDrop` (parse payload → send
  `mission.inject` WS command → brief "added" pulse). Mounted in `App.tsx`.
- **`views/OutputsView.tsx`** — `SessionRow` gains `draggable`, `onDragStart`
  (write `{slug, mission_id, utterance}` JSON to `dataTransfer`), and a grip
  affordance/cursor. No change to the click-to-select behaviour.
- **`schema/ws.ts`** — add `"mission.inject"` to the `WSCommand` action enum
  (zod mirror).
- **chat rendering** — the dropped mission appears as a normal user bubble whose
  composed text is **emoji-prefixed and human-readable** (`📎 …`). No chat-store
  model change is needed. A dedicated `source_layer = "ui.web.ws.mission_inject"`
  marker is kept for traceability and a possible future "chip" render (deferred
  polish, out of scope for v1).
- **i18n** — EN source strings for the dock label, drag affordance, and chip.

### Backend (`jarvis/ui/web/`)
- **`schema.py`** — add `"mission.inject"` to the `WSCommand.action` Literal.
- **`server.py`** — new branch in `_handle_command`: validate payload, fetch
  mission text, compose directive, publish `MessageSent`. Guard: mission not
  found → `ToastNotification` (warning) + no brain turn.
- **mission text fetch** — reuse the Outputs/missions read path (the same source
  `GET /api/outputs` and the missions store use): prompt from the mission row,
  summary/result text from `MissionApproved.summary_*` / the result artifact,
  bounded.

## 5. Five-layer enum discipline (BUG-008 guard)

`mission.inject` and the `ui.web.ws.mission_inject` source-layer marker are
wire-format strings crossing Python↔TS. Apply the anti-drift pattern:

- Python `WSCommand.action` Literal (`schema.py`) — single source of truth.
- zod `WSCommand` action enum (`schema/ws.ts`) — mirror.
- A parity assertion/test if the repo already has a ws-action parity test;
  otherwise a focused unit test asserting the server accepts the new action and
  the zod schema validates it.

## 6. Voice / speech behaviour

No new speech code. The dropped mission's reply is spoken exactly the way a
typed chat message's reply is spoken on the voice build (`ResponseGenerated` →
`_speak` → `scrub_for_voice` → TTS). On a pure text/headless surface it appears
as a chat reply. This keeps behaviour consistent with typed messages and avoids
double-speak (we do **not** also emit a canned `AnnouncementRequested`).

## 7. Error handling & edge cases

- **Mission not found / no text yet (still running):** server emits a warning
  `ToastNotification`, no brain turn. (A running mission has a prompt but maybe
  no result — we still inject the prompt + "still running" status so the user
  can discuss it.)
- **Large output:** hard char cap before composing the directive.
- **Drop while brain is thinking:** the `MessageSent` is queued by the normal
  path; no special handling beyond what typed messages already get.
- **Bad/empty `dataTransfer`:** dock `onDrop` ignores payloads it can't parse.
- **`orb_style = none`:** the dock still renders (minimal ghost) so the feature
  stays usable.

## 8. Testing

- **Backend unit:** `mission.inject` schema acceptance; `_handle_command`
  publishes a `MessageSent` with the composed directive + marker source_layer;
  mission-not-found → toast, no `MessageSent`; char-cap enforced.
- **Frontend vitest:** `SessionRow` dragstart writes the expected JSON payload;
  `JarvisDock` `onDrop` parses payload and sends the `mission.inject` command;
  dragover toggles the highlight; chip rendering for the marker source_layer.
- **Practical live test (mandatory — maintainer asked for it):** drive the
  running app in Chrome (claude-in-chrome / chrome-checkup-loop), perform a real
  drag from an Outputs card onto the dock, and verify: the WS command fires, a
  brain turn happens (a reply appears), and a follow-up question shows the
  mission is in context. Capture console + network clean.

## 9. Anti-patterns respected

- Cloud-first RULE #1 (in-app dock, not Tk overlay).
- AP-11 (no LLM in the voice scrub path — we reuse the existing reply path).
- BUG-008 / multi-layer enum drift (mirror the new action string both sides).
- AP-5/AP-14 (no spawn tool added; this is pure context injection, not a worker
  spawn — the mission is *discussed*, not re-dispatched).
- English-only artifacts (Output Language Policy).
