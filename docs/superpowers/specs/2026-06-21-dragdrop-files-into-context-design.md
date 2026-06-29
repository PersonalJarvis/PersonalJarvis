# Drag-and-drop files & content onto Jarvis → proactive reaction in context

**Date:** 2026-06-21
**Status:** Design approved (maintainer pre-approved the concept; one hard
condition — works on macOS, Windows AND Linux).

## 1. Goal & user intent

The maintainer wants to drop **anything droppable** — a file, an image, a
document, selected text, a URL — onto the on-screen Jarvis presence (the bar /
the mascot ghost) and have the dropped content **become part of Jarvis'
conversation context**, with Jarvis **reacting proactively** to it. The reaction
is *decided by the model from the content*, not hard-coded: an image → comment
on what stands out / "how does this look to you?" / analyse it; a document →
"let me take a look". In the maintainer's words: trust the model's intelligence,
and *"it must work across all operating systems — Mac, Windows, Linux."*

This extends the shipped 2026-06-15 *mission*-drop feature
(`docs/superpowers/specs/2026-06-15-dragdrop-mission-into-context-design.md`)
from dropping **mission cards** to dropping **arbitrary OS content**.

## 2. Key decision — surfaces (cross-platform is the hard constraint)

The bar / mascot are separate **Tk overlay windows**; a native OS file-drop onto
a Tk window is platform-specific and historically Windows-fragile. The
2026-06-15 spec already resolved this for missions by making the **in-app React
`JarvisDock`** the primary, cloud-first, all-OS drop surface (it mirrors the
active display style, so it *feels* like dropping onto the bar/ghost). We reuse
that decision:

- **PRIMARY (must-ship, fully cross-platform): extend `JarvisDock` to accept OS
  file/content drops via HTML5 `dataTransfer`.** HTML5 drag-and-drop in the
  pywebview/browser window natively yields files, `text/plain`, `text/uri-list`
  and images on Windows, macOS, Linux **and** any headless browser. This is the
  faithful, robust realisation of "drop anything onto Jarvis" and satisfies the
  hard cross-platform condition by construction.
- **DESKTOP EXTRA (cross-platform, gated, graceful): native file/text drop on
  the floating Tk bar + mascot** via the cross-platform `tkdnd` Tcl extension
  (`tkinterdnd2`), behind the `[desktop]` extra. Gives the literal "drop onto the
  always-on-top bar" on a desktop install; degrades to a logged no-op where
  `tkdnd` is unavailable (AD-6). Reuses the **same** backend intake.

Both surfaces feed one shared backend intake. The web dock alone fully delivers
the feature on every OS; the overlay extra is additive convenience.

## 3. Architecture & data flow

Reuse the entire existing reply pipeline (as mission-drop does): a drop becomes a
normal `MessageSent(role="user")` brain turn → the brain reacts (chat reply, and
spoken on the voice build exactly as a typed message is) → the content lands in
`BrainManager._history`. **No new speech plumbing** (AP-11). The one genuinely
new mechanic is carrying **images** into that turn so the multimodal brain can
*see* a dropped picture.

```
Web dock:   [OS drag] → JarvisDock onDrop → FormData(files[], text, thread_id)
                       → POST /api/chat/drop ─┐
Tk overlay: [OS drag] → drop_target callback (Tk thread)                │
                       → marshal to loop → ingest_drop(...) ────────────┤
                                                                        v
        jarvis/brain/drop_context.py
          1. classify each item by MIME → (directive text, ImageBlocks)
          2. brain.inject_images_for_turn(trace_id, images)      # image seam
          3. bus.publish(MessageSent(role="user", trace_id=trace_id,
                         source_layer="ui.drop", text=directive, thread_id))
                                                                        v
        _on_user_message (desktop_app.py / launcher.py)
          → brain.generate(text, trace_id, source_layer="ui.drop")
              → _collect_vision_images(trace_id) pops the injected ImageBlocks
                (bypassing the screen-vision gate) → dispatcher.dispatch(images=)
          → reply: chat bubble (+ spoken on the voice build)
          → directive text now in _history (the context window)
```

### The image-injection seam (the one new BrainManager mechanic)

`BrainManager` gains a tiny per-turn buffer keyed by `trace_id`:

- `inject_images_for_turn(trace_id: UUID, images: tuple[ImageBlock, ...]) -> None`
  stores into `self._pending_turn_images: dict[UUID, tuple[ImageBlock, ...]]`.
- `_collect_vision_images(trace_id=...)` **first** pops
  `self._pending_turn_images.pop(trace_id, None)`; if present, returns it
  immediately — **before** the `vision is None / paused / conditional` gate — so a
  dropped image always reaches the brain even with screen-vision off. Cleared on
  use; never carries to the next turn. `trace_id` is unique per turn → race-free.

`source_layer = "ui.drop"` is added to `_NON_SPAWN_SOURCE_LAYERS` (parity with
`mission_inject`) so a dropped file is *reacted to / discussed*, never
auto-force-spawned into a worker (AP-5/AP-14, anti-doom-loop). The model may
still choose tools/spawn through normal routing.

### The proactive directive (English artifact; model replies in user's language)

Bounded (hard char cap, reuse the ~4000 mission cap pattern), e.g.:

> 📎 You just dropped these onto me: `report.pdf`, `photo.png`.
> [report.pdf — text excerpt …] [photo.png — image attached for you to see]
> Take a look at what I gave you and react naturally: point out what stands out,
> analyse it, or ask me what I'd like done with it.

Reply language is resolved by the existing output-language path, not the
directive language (same as mission-drop).

### Content classification (`drop_context.py`, dependency-light)

- `image/*` → `ImageBlock` (base64) for the multimodal brain + a `[image: name]`
  note. Per-image byte cap (reuse `vision.image_budget.cap_image_b64`).
- text / code / json / csv / markdown / yaml / toml (by MIME or extension) →
  decoded UTF-8 text, per-file char cap, inlined.
- `application/pdf` → best-effort: if `pypdf` imports, extract bounded text; else
  a `[PDF: name — not extracted]` note. **No new hard dependency.**
- anything else → `[file: name (type, size)]` note only.
- total request cap (count + bytes) to protect the token budget / WS broadcast.

## 4. Components & files

**Backend (new):**
- `jarvis/brain/drop_context.py` — pure classify+compose + `ingest_drop(bus,
  brain, thread_id, items, dragged_text)`; no FastAPI import.
- `jarvis/ui/web/drop_routes.py` — `POST /api/chat/drop` (multipart `files[]` +
  form `thread_id`, `text`, `surface`); reads UploadFiles → items → `ingest_drop`.
  Mirrors the avatar-upload pattern (size cap, bytes validation).
- `jarvis/overlay/drop_target.py` — cross-platform `DropTarget` seam: `Protocol`
  + `tkinterdnd2` impl + null no-op + `make_drop_target()` factory (AD-6 shape,
  mirrors `jarvis/overlay/surface.py`). Lazy imports; never raises.

**Backend (touched):**
- `jarvis/brain/manager.py` — `inject_images_for_turn` + the pop in
  `_collect_vision_images`; add `"ui.drop"` to `_NON_SPAWN_SOURCE_LAYERS`.
- `jarvis/ui/web/server.py` / route registration — mount `drop_routes`.
- `ui/orb/overlay.py` + `jarvis/ui/jarvisbar/overlay.py` — attach the drop
  target after the Tk root is built (additive; color-key rendering untouched, AD-7).
- `jarvis/ui/desktop_app.py` — wire the overlay drop callback → marshal → ingest.
- `pyproject.toml` — `tkinterdnd2` in the `[desktop]` extra.

**Frontend (touched):**
- `components/JarvisDock.tsx` — accept native file/content drags (a file-drag
  `armed` state independent of the mission-drag store; `dataTransfer.files` +
  `text/uri-list` + `text/plain`); build `FormData`; `POST /api/chat/drop`;
  reuse the existing bloom/flash/chime feedback.
- i18n EN source strings for the file-drop affordance.

## 5. Five-layer enum discipline (BUG-008 guard)

`source_layer = "ui.drop"` is the new wire-format string. Single source of truth
in `drop_context.py`, mirrored in `manager._NON_SPAWN_SOURCE_LAYERS`, guarded by
a parity test alongside the existing `mission_inject` parity test in
`tests/unit/brain/test_routing.py`. No new TS enum (REST multipart, not a WS
action), so no zod mirror needed.

## 6. Error handling & edge cases

- Empty / unparseable drop → ignored, no brain turn (dock `onDrop` guards;
  route returns 400 with no `MessageSent`).
- Oversized file → 413, per-file + total caps; never blows the token budget.
- Non-image binary → name/type note only (no decode attempt).
- Drop while brain is thinking → queued by the normal path (no special handling,
  same as a typed message / dropped mission).
- No brain / headless → chat-only reaction; no crash.
- `tkdnd` missing (overlay extra) → logged no-op; the web dock still works.

## 7. Testing

- **Backend unit:** `drop_context` classification (image→ImageBlock, text inline,
  pdf note, binary note, caps); `ingest_drop` publishes a `MessageSent` with the
  `ui.drop` marker + injects images keyed by trace_id; empty drop → no publish.
- **BrainManager unit:** `inject_images_for_turn` + `_collect_vision_images`
  pops the buffer and bypasses the vision gate; clears after one turn.
- **Routing parity:** `"ui.drop"` exempt from force-spawn.
- **Route unit:** `POST /api/chat/drop` multipart → ingest called; size cap → 413.
- **DropTarget seam:** factory returns null no-op without `tkinterdnd2`; never raises.
- **Frontend vitest:** dock arms on a native file drag; `onDrop` builds the
  expected `FormData` and POSTs; ignores empty payloads.
- **Verify:** full pytest of new tests + ruff + `tsc -b` + vitest + a headless
  import/boot smoke; live chrome-checkup of the dock drop if the extension is up.

## 8. Anti-patterns / doctrine respected

- Cloud-first RULE #1 — web dock is the all-OS primary; overlay extra is gated +
  graceful (AD-6). Hard cross-platform condition satisfied by the HTML5 surface.
- AP-11 — no LLM / no new code on the voice scrub path; reuse the reply pipeline.
- AP-5 / AP-14 — no spawn tool added; `ui.drop` exempt from force-spawn.
- BUG-008 — `ui.drop` mirrored + parity-tested.
- AP-1 / subprocess hygiene — n/a (no new subprocess).
- English-only artifacts (Output Language Policy).
