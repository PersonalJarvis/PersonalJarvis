# JarvisDock drag-and-drop polish — design

Date: 2026-06-16
Branch: `feat/dock-dnd-polish-20260616` (worktree off `main`)
Scope: frontend-only (`jarvis/ui/web/frontend/`). No server, schema, or WS contract change.

## Problem (from the bridgespace recording + code)

Dragging a sub-agent mission/output card onto the `JarvisDock` "works" — the WS
`mission.inject` command fires and Jarvis recaps the task — but the *feel* is
broken:

1. **Giant native drag ghost.** `OutputsView.tsx` `SessionRow` sets a drag
   payload but never calls `dataTransfer.setDragImage(...)`, so the browser
   snapshots the whole card `<button>` (mission prompt + RESTART/ERROR buttons)
   as a large, opaque, low-fidelity drag image.
2. **The 🚫 "no-drop" cursor the entire drag.** HTML5 DnD shows the not-allowed
   cursor over any element that is not a registered drop target. The only drop
   target is the small `JarvisDock` pill (`fixed bottom-4 right-4`), so the long
   travel from the Outputs sidebar to the dock reads as "you can't drop this".
3. **The dock barely reacts** — it only `armed` (scale + ring) when the card is
   directly over its ~40px pill; there is no global "a drag is in progress, drop
   here" affordance.
4. **Weak success feedback** — a single 1200 ms emerald ring (`flash`). No
   motion and no audio confirmation.

The server contract (`mission_inject.py`, `compose_mission_inject_text`, WS
`mission.inject`) is correct and unchanged.

## Solution

Frontend-only, cloud-first (works in any browser), **no new dependency**
(WebAudio + an off-screen DOM node are browser built-ins).

### Units (each isolated, independently testable)

- `lib/sound.ts` — `playDropConfirm()`. A pure WebAudio confirmation chime:
  two soft sine voices, low gain (~0.06), short attack + smooth exponential
  decay (~250 ms). No-op (never throws) when `AudioContext` is unavailable
  (headless/jsdom) or when the `jarvis.ui.sound` localStorage flag is off.
  One lazily-created shared `AudioContext`, `resume()`d on use (the drop is a
  user gesture, so autoplay policy allows it).
- `store/missionDrag.ts` — a tiny zustand store with `dragging: boolean` and
  `begin()/end()`. Window-level `dragstart`/`dragend`/`drop` listeners
  (installed once) flip `dragging` when the active drag carries
  `MISSION_DND_MIME`. This is what lets the dock and catch layer react to a
  drag that started anywhere in the app.
- `components/MissionDragChip` helper — builds the compact off-screen drag
  image (📎 + truncated title) and returns a cleanup handle. Used by
  `SessionRow.onDragStart`.
- `JarvisDock.tsx` — subscribes to `missionDrag`. While `dragging`: render the
  **bloom** (enlarged, lifted, glowing dock + "Drop to brief Jarvis" label) and
  a soft full-window **catch layer** (`fixed inset-0`, `onDragOver`
  `preventDefault` so the cursor is `copy` everywhere, `onDrop` → same inject).
  On a successful drop: run the **absorb** success sequence (ripple ring +
  bounce + 📎→✓), call `playDropConfirm()`, keep the existing emerald `flash`.
  The existing `data-testid="jarvis-dock"` element and `onDrop` semantics are
  preserved so current tests stay green.
- `OutputsView.tsx` `SessionRow` — `onDragStart` now also sets the custom drag
  image and calls `missionDrag.begin()`; `onDragEnd` calls `missionDrag.end()`.

### Drop routing (shared)

A single `injectMissionFromDataTransfer(dt)` helper (in `JarvisDock.tsx` or a
small shared module) parses the MIME payload, resolves the active thread, and
sends the WS command — used by both the dock `onDrop` and the catch-layer
`onDrop` so behaviour is identical wherever the user releases.

### Safety / non-regressions

- Catch layer only mounts while a *mission* drag is active (our own MIME), so it
  never swallows unrelated interactions.
- Sound and `AudioContext` access are fully guarded — silent, never throwing, in
  tests and headless.
- WS payload, MIME constant, and `mission.inject` action are unchanged.
- i18n: extend the existing `jarvis_dock` block (en/de/es) with `drop_active`
  ("Drop to brief Jarvis") and `dropped` ("Added to conversation"). English
  source, per the Output Language Policy.

## Testing

- `lib/sound.test.ts` — no-op without `AudioContext`; creates oscillator/gain
  and starts/stops when a fake `AudioContext` is present; respects the mute flag.
- `store/missionDrag.test.ts` — a `dragstart` carrying the MIME sets
  `dragging`; `dragend`/`drop` clears it; a non-mission drag is ignored.
- `JarvisDock.test.tsx` — existing two tests stay green; add: a valid drop runs
  the success path (sound helper called via mock, success state shown), and the
  catch layer routes a drop to the same `mission.inject` send.
- `OutputsView` drag-start test — `setDragImage` is invoked and `missionDrag`
  begins.

## Verification

`npm run test` (vitest) green, `npm run build` clean, then load the running app
and drag a mission card to confirm: compact chip, no 🚫 cursor, dock bloom,
absorb animation, soft chime, and Jarvis recapping the task.
