# Desktop pets

A desktop pet is an animated pixel-art companion that floats on the desktop
while Jarvis runs. It is the overlay style `pet`, an alternative to the slim
Jarvis bar (`jarvis_bar`), the Gigi mascot (`mascot`) and the voice orb
(`voice_orb`). Users pick the style and the pet under
**Settings → My Pets**.

The pet sits in the same frameless, always-on-top window as the mascot and the
voice orb (`ui/orb/overlay.py`, `OrbOverlay`). Switching between those three
styles, or between pets, applies live. Switching between the bar and any
orb-window style needs an app restart, because a second Tk root created at
runtime aborts the process (BUG-031). The settings page offers the restart in
one click.

## What the user sees

- **The figure.** It reacts to Jarvis in real time (see *States*). It can be
  dragged anywhere, and its position is remembered per monitor
  (`[overlay.mascot] position_*`, shared with the mascot).
- **The control strip** under the figure. At rest only the figure shows; the
  strip appears while Jarvis listens, thinks or talks, while notification cards
  are up, and while the pointer is on the pet. With the pet "None", or with
  **Always show the buttons** on (My Pets → Customize, `[ui] pet_strip_always`),
  it always shows. From left to right:
  - bell: notifications on or off for this run (see *Notifications*). Off
    shows a red, struck-through bell and sends the cards on screen away; on
    rings the bell. Like the speaker mute it never survives a restart, so a
    bell forgotten off cannot hide tomorrow's "your agent is done";
  - microphone: mute Jarvis's microphone (`VoiceMuteToggleRequested`, mirrored
    from `VoiceMuteChanged`);
  - talk indicator (three strokes, the Jarvis bar's equalizer cut down to
    three, each a light-blue sky with soft white clouds in it): start a conversation, or hang up the running one. The
    strokes stand still and dimmed at rest, follow the live audio level while
    listening, dictating or talking, and carry a travelling highlight while
    thinking or transcribing;
  - speaker: silence the assistant's voice for this session (TTS volume 0,
    mirrored from `VoiceSpeakerMuteChanged`).
- **The thinking card** under the strip: a rounded pill with a bold title
  (what Jarvis is working on) and one muted detail line (the current thought
  or step). It appears ONLY while Jarvis is really thinking and shows what it
  thinks — never the live transcript and never the reply, which the user hears
  anyway (see *The thinking card* below). It can be switched off
  (`[ui] pet_bubble`).
- **The pet "None"** (`pet_id = "none"`) shows the control strip and the
  card without a figure.

- **Notification cards** under the strip (under the thinking card when one
  shows; above the pet when there is no room below). See *Notifications*.

The pet stays on screen while Jarvis is idle. The global shortcut
(`[trigger] hotkey_pet_toggle`, default `alt+win+p`) hides it or brings it
back and to the front; hiding lasts until the next app start. The shortcut is
changed on the My Pets page (Customize); an empty value switches it off. On
Wayland, global shortcuts are a no-op, as for every other shortcut.

## Notifications

When something happens that the user waits for while looking elsewhere, the
bell swings and a card drops out from under the strip: a round icon (a check
that ticks itself for `done`, `!` for `attention`, a cross for `error`, a bell
for `info`), a bold title and one muted detail line. What becomes a card is
decided in `jarvis/ui/pets/notices.py` (titles per interface language):

| Source | Card |
| --- | --- |
| Agentic IDE pane entry (`NotificationCenter.subscribe`) | `<agent> finished` / `needs your answer` / `exited` / `could not start` |
| `JarvisAgentBackgroundCompleted` | Background task done / failed |
| `MissionCompleted` | Mission complete / failed / timed out (a cancel stays quiet) |
| `DelegationResultReady` | `<agent> has a result` |
| `ActionApprovalRequired` | Approval needed (not when a chat answers its own card) |
| `WorkflowCompleted` | Routine finished / failed |
| `ErrorOccurred(recoverable=False)` | Something went wrong |

Cards stack (`ui/orb/notice_stack.py`): the newest in front, up to two older
ones peeking out behind it, narrower and darker; at most four are kept.
Hovering the stack fans it out into a column; leaving folds it back. A click
dismisses the card under the pointer, which slides away. Every card leaves on
its own (`done` 8 s, `error` 14 s, `attention` 20 s, `info` 6 s), never while
the pointer rests on the stack. The same card twice within 3 s refreshes
instead of stacking. Motion runs on springs at ~60 fps only while something
moves; a resting stack costs a timer every 400 ms. Cards are dropped while the
bell is off, the pet is hidden, or the look is not the pet. The strip stays in
sight while cards show.

## The thinking card

The card mirrors Jarvis's real thinking, fed by bus events in
`ui/orb/bus_bridge.py` and condensed by `jarvis/ui/pets/status_line.py`:

| Source | Title | Detail |
|---|---|---|
| `ReasoningSummaryUpdated` — the thinking model behind GPT-Live (`jarvis/live/session.py`, streamed as cumulative snapshots a few times per second) | the newest section heading of the summary (`**Heading**`) | the last complete sentence of that section |
| `ActionProposed` / `ToolCallStarted` — a tool step | the current thought's heading, else the running agent task, else "Working" | the step's rationale, else the humanized tool name |
| `ActionExecuted` while a card is up | unchanged | "Step done" / "Step failed" |
| `JarvisAgentTaskStarted` / `JarvisAgentTaskCompleted` / `JarvisAgentBackgroundCompleted` — an agent task | the task (the user's request, condensed) | "Working …", then "Done" / "Failed" |
| `SystemStateChanged(THINKING)` with no thought after 1.5 s | "Thinking …" | empty |

When it goes away (a 1.5 s linger, then the surface fades it out):

- Jarvis starts talking out loud (`AudioOutFirst`), the turn ends (`IDLE`,
  `ERROR`, `PAUSED`), a fresh turn starts listening, or the session ends;
- a thought that arrives while Jarvis is already talking (the thinking model
  working on behind the voice model) clears itself after four quiet seconds;
- a finished agent task shows Done / Failed for three seconds.

A running agent task keeps its card across all of these until it completes;
a completion that never arrives stops holding the card after 30 minutes.
Spoken lines (acks, progress readbacks), the transcript and the reply never
reach the card. Updates are rate-limited (0.3 s) and deduplicated, and the
newest held-back update is always shown once the limit allows. The fixed
labels follow the interface language (`[ui] language`: en / de / es). Title
and detail are clipped to 60 and 110 characters; markdown, code, URLs, file
paths and JSON dumps are stripped.

## States

The vocabulary lives in `jarvis/ui/pets/states.py` (a module with no imports)
and is mirrored in `frontend/src/lib/petStates.ts`; a parity test keeps them
equal.

| State | Driven by |
|---|---|
| `idle` | `SystemStateChanged(IDLE)`, no session |
| `listening` | wake word, `VoiceSessionStarted`, `SystemStateChanged(LISTENING)`, dictation |
| `thinking` | `SystemStateChanged(THINKING)` |
| `talking` | `AudioOutFirst`, `SystemStateChanged(SPEAKING)` with audible output |
| `success` (one-shot, 1.5 s) | `SpeechSpoken.spoken_kind` in `action_done`/`completion`, `JarvisAgentBackgroundCompleted(success=True)`, `ActionExecuted(success=True)` while no turn is running |
| `error` (one-shot, 2 s) | `ErrorOccurred(recoverable=False)`, `SpeechSpoken.spoken_kind` in `timeout`/`unavailable`/`stt_unavailable`, `VoiceSessionEnded(hangup_reason="error")`, `ActionExecuted(success=False)` while no turn is running |
| `sleeping` | five minutes without any Jarvis event; any event wakes it |
| `working` | a tool step that changes, sends or runs something (`ActionProposed` / `ToolCallStarted`) while thinking; also an agent task running in the background while Jarvis is otherwise idle (`JarvisAgentTaskStarted` until `JarvisAgentTaskCompleted`) |
| `searching` | a tool step that looks something up: its name contains a word like search, find, read, fetch, browse, query, list or wiki (`jarvis/ui/pets/actions.py`) |
| `held` | the user drags the pet with the mouse (from the first real movement until the release) |

Precedence, highest first: `held`, a one-shot, `listening` / `talking`, an action state, background work, `thinking` / `idle`, `sleeping`. An action ends with the tool's `ActionExecuted`, when Jarvis starts talking or the turn ends, and at the latest after 12 seconds without a newer tool call. A pet whose sheet has no row for a new state borrows one: `working` -> `thinking`, `searching` -> `working`, `held` -> `listening`.

States come only from real bus events (`ui/orb/bus_bridge.py`); nothing is
simulated. The classic voice pipeline does not publish `ErrorOccurred`, so on
that path an error shows through the `SpeechSpoken` kinds.

The one-shots report what the user experiences, not every internal step:

- a tool result inside a running voice or typed turn (any state other than
  `IDLE`, `ERROR` or `PAUSED`) is a step of that turn and plays nothing; the
  turn's own end reports how it went;
- a recoverable `ErrorOccurred` (a retry or fallback the user never notices)
  plays nothing;
- at most one one-shot plays per three seconds. The only exception is an error
  right after a success, because the failure is the news.

## Sprite format `jarvis-pet/1`

A pet is a folder with two files:

```text
<pet-id>/
  pet.json    manifest
  sheet.png   sprite sheet (RGBA, real transparency)
```

The sheet is a grid of square cells. `frame_size` is 32, 48 or 64 source
pixels. Each state is one row with up to eight frames. By convention the rows
follow the order of `PET_STATES`: `idle`, `listening`, `thinking`, `talking`,
`success`, `error`, `sleeping`, `working`, `searching`, `held`. The last three
rows are optional for user-made pets (a 64 px sheet has room for only eight
rows); the missing ones fall back as described under *States*.

```json
{
  "format": "jarvis-pet/1",
  "id": "gigi",
  "name": "Gigi",
  "description": "The Jarvis ghost, eight bits tall.",
  "frame_size": 48,
  "sheet": "sheet.png",
  "animations": {
    "idle":      {"row": 0, "frames": 8, "fps": 6, "loop": true,
                  "accent_frames": 1, "accent_every": 3},
    "listening": {"row": 1, "frames": 6, "fps": 8, "loop": true},
    "thinking":  {"row": 2, "frames": 8, "fps": 8, "loop": true},
    "talking":   {"row": 3, "frames": 4, "fps": 10, "loop": true},
    "success":   {"row": 4, "frames": 8, "fps": 10, "loop": false},
    "error":     {"row": 5, "frames": 6, "fps": 9, "loop": false},
    "sleeping":  {"row": 6, "frames": 6, "fps": 3, "loop": true}
  }
}
```

Two row conventions make the pet feel alive:

- **Accent frames.** `accent_frames` (optional) marks the last cells of a
  looping row as an accent, such as a blink, that plays only on every
  `accent_every`-th pass. The built-in idle rows breathe on seven cells and
  blink on the eighth every third breath.
- **Talking by voice.** The `talking` row is ordered by mouth openness, from
  closed to widest. While a live output level is fresh (under 250 ms old),
  the renderer picks the frame from that level, lightly smoothed, so the mouth
  follows the real voice. Without a level the row swings back and forth.

Rules the loader enforces (`jarvis/ui/pets/manifest.py`):

- `format` must be `jarvis-pet/1`; `id` matches `^[a-z0-9][a-z0-9-]{0,31}$`
  (built-in) or `^u[0-9a-f]{16}$` (user-created).
- `name` is 1–40 characters, `description` at most 140.
- `idle` is required. A missing state borrows another row through
  `STATE_FALLBACKS` (for example `sleeping` → `idle`).
- `frames` is 1–8, `fps` is 1–12, and every referenced cell lies inside the
  sheet. The sheet is at most 512 × 512 pixels and 2 MB.
- Transparency is binary on screen: a pixel is either opaque or gone
  (alpha ≥ 128 counts as opaque). The overlay keys out pure magenta
  (`#FF00FF`), so the loader nudges any exact-magenta sprite pixel to
  `#FE00FE`.

Built-in pets live in `jarvis/ui/pets/builtin/<id>/` and are generated from
pixel data by `scripts/pets/build_pets.py` (deterministic; a test checks the
committed PNGs match the script). User-created pets live in
`<data dir>/pets/<u…id>/`.

### Built-in pets

| Id | Name | Idea |
|---|---|---|
| `gigi` | Gigi | The Jarvis ghost in brand black and white: it floats, cups its hands to listen, scans while it thinks, its bands light up as it talks, its bits burst on success, it glitches on errors and its tail fades out asleep (default) |
| `miso` | Miso | A cat: ears up while listening, curls up to sleep |
| `brew` | Brew | A teapot: steams while thinking, whistles while talking |
| `bolt` | Bolt | A battery: charges while thinking, sparks on success, runs flat asleep |
| `mochi` | Mochi | A jelly blob that wobbles while listening |
| `shelly` | Shelly | A snail: the shell spins while thinking, it withdraws to sleep |
| `pip` | Pip | A baby: babbles while talking, cries on errors, naps under a blanket |

## Adding a pet

- **As a user:** Settings → My Pets → Create pet. Upload a sheet in the
  layout above (a template PNG is offered for download), add a name and a
  one-sentence description, optionally a `pet.json`. Without a manifest the
  rows are read in `PET_STATES` order and the frame count per row is the
  number of non-empty cells.
- **As a developer:** add a builder to `scripts/pets/build_pets.py`, run it,
  and commit the generated folder. The parity and completeness tests require
  every built-in pet to provide all ten states.

## Interfaces

### Surface methods

`OrbOverlay` gains these methods. The bridge calls them through `getattr`, so
the bar and `NullOverlay` stay untouched. The macOS companion process
(`jarvis/ui/jarvisbar/host.py`) forwards each one as a command of the same
name.

| Method | Meaning |
|---|---|
| `set_pet(pet_id)` | Swap the figure live; `"none"` shows the strip only |
| `set_pet_look(scale, bubble, strip_always)` | Apply size, card on/off and the always-on strip live |
| `set_pet_outcome(kind)` | Play the one-shot `success` or `error` |
| `set_pet_action(kind)` | `working` / `searching` for the running tool step, `None` when it ended |
| `set_pet_busy(busy)` | An agent task is running in the background |
| `show_status(title, detail="")` | Show or update the thinking card (already condensed) |
| `clear_status(linger_s=1.5)` | Take the card down after `linger_s` |
| `wants_status_lines` (attribute) | True only for the pet: the bridge feeds the card to nothing else |
| `set_muted(muted)` | Mirror the microphone mute on the strip |
| `set_speaker_muted(muted)` | Mirror the speaker mute on the strip |
| `set_visible(visible)` | Hide or show the whole pet (shortcut, settings) |
| `toggle_visible()` | Shortcut action: hide, or show and raise |
| `push_notice(kind, title, detail)` | One notification card; rings the bell |
| `set_notifications_enabled(enabled)` | Switch the bell without a click (a respawned host) |
| `set_on_notifications_toggle(cb)` | `cb(enabled)` after the bell was clicked |

Control-strip actions: `bell` (handled in the surface, reported through
`set_on_notifications_toggle`; the macOS host sends it up as `notify_toggle`),
`mic_mute`, `talk`/`hangup` (the orb) and `speaker`.

### Events (`jarvis/core/events.py`)

- `VoiceSpeakerMuteChanged(muted, source)`: published by
  `SpeechPipeline.set_tts_volume` when the muted-ness flips.
- `ComposeRequested(source)`: a new typed chat; DesktopApp raises the
  window, the frontend opens a new chat. (The strip's pen became the bell.)
- `PetVisibilityToggleRequested(source)`: the `pet_toggle` shortcut; the
  bridge calls `surface.toggle_visible()`.
- `PetChanged(pet_id, scale, bubble, visible, source)`: published by the pets
  routes after a change is on disk and applied.

### Configuration

| Key | Default | Meaning |
|---|---|---|
| `[ui] orb_style` | `jarvis_bar` | `pet` selects the pet |
| `[ui] pet_id` | `gigi` | Active pet (built-in id, `u…` id or `none`) |
| `[ui] pet_scale` | `1.0` | Size multiplier, 0.5–2.0 |
| `[ui] pet_bubble` | `true` | Show the thinking card |
| `[ui] pet_strip_always` | `false` | Keep the control strip up even at rest |
| `[trigger] hotkey_pet_toggle` | `alt+win+p` | Hide / show the pet; empty disables it |

### REST (`jarvis/ui/web/pets_routes.py`)

| Method and path | Body / result |
|---|---|
| `GET /api/pets` | `{active, scale, bubble, visible, pets: [{id, name, description, builtin, frame_size, animations, sheet_url}]}` |
| `GET /api/pets/{id}/sheet.png` | The sprite sheet |
| `GET /api/pets/template.png` | The empty sprite-sheet template (48 px cells, rows in state order) |
| `PUT /api/pets/active` | `{pet_id}` → saves `[ui] pet_id`, applies live |
| `PUT /api/pets/settings` | `{scale?, bubble?, strip_always?}` → saves, applies live |
| `POST /api/pets/visibility` | `{visible}` → runtime only |
| `POST /api/pets` | multipart: `sheet` (PNG), `name`, `description`, optional `manifest` (JSON), optional `frame_size` → the new pet |
| `DELETE /api/pets/{id}` | user-created pets only |

## Performance budget

The pet must cost next to nothing while idle:

- every frame is scaled once (nearest-neighbour, an integer factor chosen
  from the figure's visible size so it reads about 180 logical px tall,
  times the monitor DPI and `pet_scale`) and cached as a Tk image; the strip
  (about 0.3 × the figure) and the card scale with it;
- the window repaints only when the frame key changes (frame index, plus the
  level bucket while talking), and the timer sleeps until the next frame
  boundary: 6 fps idle, 3 fps asleep, 8–10 fps active, nothing while hidden;
- the control strip's indicator animates only while listening, talking or thinking.

Target: an idle pet adds less than 1 % of one CPU core to the app process.
