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
- **The control strip** under the figure, from left to right:
  - pen: raise the main window and open a new chat (`ComposeRequested`);
  - microphone: mute Jarvis's microphone (`VoiceMuteToggleRequested`, mirrored
    from `VoiceMuteChanged`);
  - orb: start a conversation, or hang up the running one; it pulses with the
    live audio level;
  - speaker: silence the assistant's voice for this session (TTS volume 0,
    mirrored from `VoiceSpeakerMuteChanged`).
- **The status bubble** under the strip. It shows short, condensed lines of
  what Jarvis is doing: the live transcript while listening, a "Thinking …"
  header with the current reason, tool or progress line while thinking, and
  the condensed reply while talking. It collapses to two lines, expands to six
  on click, and fades out six seconds after the last update while idle. It can
  be switched off (`[ui] pet_bubble`).
- **The pet "None"** (`pet_id = "none"`) shows the control strip and the
  bubble without a figure.

The pet stays on screen while Jarvis is idle. The global shortcut
(`[trigger] hotkey_pet_toggle`, default `alt+win+p`) hides it or brings it
back and to the front; hiding lasts until the next app start. On Wayland,
global shortcuts are a no-op, as for every other shortcut.

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
| `success` (one-shot, 1.5 s) | `ActionExecuted(success=True)`, `SpeechSpoken.spoken_kind` in `action_done`/`completion`, `JarvisAgentBackgroundCompleted(success=True)` |
| `error` (one-shot, 2 s) | `ErrorOccurred`, `ActionExecuted(success=False)`, `SpeechSpoken.spoken_kind` in `timeout`/`unavailable`/`stt_unavailable`, `VoiceSessionEnded(hangup_reason="error")` |
| `sleeping` | five minutes without any Jarvis event; any event wakes it |

States come only from real bus events (`ui/orb/bus_bridge.py`); nothing is
simulated. The classic voice pipeline does not publish `ErrorOccurred`, so on
that path an error shows through the `SpeechSpoken` kinds.

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
`success`, `error`, `sleeping`.

```json
{
  "format": "jarvis-pet/1",
  "id": "gigi",
  "name": "Gigi",
  "description": "The Jarvis ghost, eight bits tall.",
  "frame_size": 48,
  "sheet": "sheet.png",
  "animations": {
    "idle":      {"row": 0, "frames": 4, "fps": 4, "loop": true},
    "listening": {"row": 1, "frames": 4, "fps": 8, "loop": true},
    "thinking":  {"row": 2, "frames": 6, "fps": 8, "loop": true},
    "talking":   {"row": 3, "frames": 4, "fps": 10, "loop": true},
    "success":   {"row": 4, "frames": 6, "fps": 10, "loop": false},
    "error":     {"row": 5, "frames": 4, "fps": 8, "loop": false},
    "sleeping":  {"row": 6, "frames": 2, "fps": 2, "loop": true}
  }
}
```

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
| `gigi` | Gigi | The Jarvis ghost as a pixel sprite (default) |
| `miso` | Miso | A cat: ears up while listening, curls up to sleep |
| `brew` | Brew | A teapot: steams while thinking, whistles while talking |
| `bolt` | Bolt | A battery: charges while thinking, sparks on success, runs flat asleep |
| `mochi` | Mochi | A jelly blob that wobbles while listening |
| `shelly` | Shelly | A snail: the shell spins while thinking, it withdraws to sleep |

## Adding a pet

- **As a user:** Settings → My Pets → Create pet. Upload a sheet in the
  layout above (a template PNG is offered for download), add a name and a
  one-sentence description, optionally a `pet.json`. Without a manifest the
  rows are read in `PET_STATES` order and the frame count per row is the
  number of non-empty cells.
- **As a developer:** add a builder to `scripts/pets/build_pets.py`, run it,
  and commit the generated folder. The parity and completeness tests require
  every built-in pet to provide all seven states.

## Interfaces

### Surface methods

`OrbOverlay` gains these methods. The bridge calls them through `getattr`, so
the bar and `NullOverlay` stay untouched. The macOS companion process
(`jarvis/ui/jarvisbar/host.py`) forwards each one as a command of the same
name.

| Method | Meaning |
|---|---|
| `set_pet(pet_id)` | Swap the figure live; `"none"` shows the strip only |
| `set_pet_look(scale, bubble)` | Apply size and bubble on/off live |
| `set_pet_outcome(kind)` | Play the one-shot `success` or `error` |
| `show_status(header, line)` | Update the status bubble (already condensed) |
| `set_muted(muted)` | Mirror the microphone mute on the strip |
| `set_speaker_muted(muted)` | Mirror the speaker mute on the strip |
| `set_visible(visible)` | Hide or show the whole pet (shortcut, settings) |
| `toggle_visible()` | Shortcut action: hide, or show and raise |

Control-strip actions reported back through callbacks: `compose`, `mic_mute`,
`talk`/`hangup` (the orb) and `speaker`.

### Events (`jarvis/core/events.py`)

- `VoiceSpeakerMuteChanged(muted, source)`: published by
  `SpeechPipeline.set_tts_volume` when the muted-ness flips.
- `ComposeRequested(source)`: the pen control; DesktopApp raises the window,
  the frontend opens a new chat.
- `PetChanged(pet_id, scale, bubble, visible, source)`: published by the pets
  routes after a change is on disk and applied.

### Configuration

| Key | Default | Meaning |
|---|---|---|
| `[ui] orb_style` | `jarvis_bar` | `pet` selects the pet |
| `[ui] pet_id` | `gigi` | Active pet (built-in id, `u…` id or `none`) |
| `[ui] pet_scale` | `1.0` | Size multiplier, 0.5–2.0 |
| `[ui] pet_bubble` | `true` | Show the status bubble |
| `[trigger] hotkey_pet_toggle` | `alt+win+p` | Hide / show the pet; empty disables it |

### REST (`jarvis/ui/web/pets_routes.py`)

| Method and path | Body / result |
|---|---|
| `GET /api/pets` | `{active, scale, bubble, visible, pets: [{id, name, description, builtin, frame_size, animations, sheet_url}]}` |
| `GET /api/pets/{id}/sheet.png` | The sprite sheet |
| `PUT /api/pets/active` | `{pet_id}` → saves `[ui] pet_id`, applies live |
| `PUT /api/pets/settings` | `{scale?, bubble?}` → saves, applies live |
| `POST /api/pets/visibility` | `{visible}` → runtime only |
| `POST /api/pets` | multipart: `sheet` (PNG), `name`, `description`, optional `manifest` (JSON), optional `frame_size` → the new pet |
| `DELETE /api/pets/{id}` | user-created pets only |

## Performance budget

The pet must cost next to nothing while idle:

- every frame is scaled once (nearest-neighbour, integer factor from the
  monitor DPI times `pet_scale`) and cached as a Tk image;
- the window repaints only when the frame index changes, and the timer sleeps
  until the next frame boundary: 4 fps idle, 2 fps asleep, 8–10 fps active,
  nothing while hidden;
- the control strip's orb animates only while listening or talking.

Target: an idle pet adds less than 1 % of one CPU core to the app process.
