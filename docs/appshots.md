# Appshots

An appshot shows the assistant the window you are working in — or exactly
the part of the screen you select. It captures once — picture and on-screen
text — and hands it to the conversation as context. Settings live under
**Settings > Appshots**.

## Ways to take one

| Trigger | What happens |
|---|---|
| **Window shortcut** — both Alt keys at once by default (both Option keys on a Mac) | The front window is captured and delivered per **Appshot destination**. |
| **Area shortcut** — both Shift keys at once by default | Every screen dims; drag a rectangle and exactly that part is captured and delivered the same way. Esc or a right-click cancels and sends nothing. |
| **Voice or chat** — "take an appshot", "mach einen Appshot", "haz un appshot" | The turn that asked captures the front window and answers with it. Only an explicit request for the whole screen ("an appshot of my full screen") captures the monitor the cursor is on instead — the live model's `take_appshot` passes `scope: "screen"` for it. |
| **Try it** buttons on the Appshots page | "Take appshot in 3 s" waits three seconds so you can switch windows; "Select area" opens the area picker at once. Both then behave like the shortcuts. |

"Front window" means the app window you work in. Jarvis's own floating
overlays (the mascot, the bar) take the focus when you click them, so the
capture looks past them to the app window underneath (BUG-228); with no app
window there, it takes the whole screen instead.

## Selecting an area

The area picker is a short-lived PySide6 process (`python -m
jarvis.appshot.picker`) that starts on the shortcut and exits after one
selection, so nothing stays resident. The picker provides:

- every screen is frozen and dimmed the moment the picker opens, so nothing
  moves under the selection (where a frozen frame cannot be grabbed, a dim
  layer over the live desktop is used instead, without the magnifier);
- hovering highlights the window under the pointer, and a click without a
  drag takes exactly that window;
- a drag cuts the area out of the dim layer with a marching-ants border and
  its size in real pixels;
- a round magnifier beside the pointer shows the pixels around it with the
  centre pixel outlined, and a small pill underneath with the position (or the
  selection size while dragging) and the zoom; the mouse wheel zooms it from
  2x to 24x, and the last zoom is remembered for the next pick.

The picker reports the rectangle as fractions of the screen it was drawn on;
the app maps that back to capture pixels (`jarvis/appshot/region.py`), so
mixed-DPI setups capture exactly what was outlined. A selection stays on one
screen. The window list for snapping is read before the overlay appears and
skips minimized and (on Windows) cloaked windows; where no list is available
(Wayland) only dragging works. The picker gives up after two minutes without
a selection.

A selected area is about pixels, not about the window in front: unlike a window
appshot it is not voided when focus moves while the picker closes. Its privacy
guard is the denylist check on every visible window that overlaps the
rectangle (see below).

A spoken "what do you see?" is the same look (Screen Context); it also plays
the shutter and shows up as the last appshot.

## The card in the corner, and the editor

After the shutter the picture flies into the bottom-right corner of its
screen and rests there as a card for about six seconds (longer while the
pointer is on it):

- **Click** opens the app on the Appshots page with the **appshot editor**:
  arrow, rectangle, ellipse, pen, highlighter, text, pixelate and crop, with
  undo/redo (Ctrl+Z / Ctrl+Y) and one-key tools (A, R, E, P, H, T, B, C).
  **Copy** puts the result on the clipboard, **Save** downloads a PNG, and
  **Use this version** replaces the held appshot, so the next message carries
  the edited picture (a picture already sent into a voice call stays as it
  was). The last appshot on the Appshots page opens the same editor.
- **Drag** the card into any app that accepts files or images (chat, mail,
  Explorer/Finder) to drop the picture there.
- **Right-click** dismisses it.

Only the finished, privacy-filtered appshot can leave by drag — the card's
own thumbnail is cut from the raw frame and never does. A drag is the one
moment an appshot touches disk: the picture is written to
`<temp>/jarvis-appshots/` just then, and files older than an hour are removed
on the next drag. With `[screen_context].deck_preview_s = 0` (keep nothing)
the card only opens the editor and shares nothing.

## Where a shortcut appshot goes

| Destination | Running voice call | No voice call |
|---|---|---|
| **Automatic** (default) | Into the call | Onto your next message |
| **Next message** | Onto your next message | Onto your next message |
| **Voice call only** | Into the call | Not sent |

- *Into the call* is silent: GPT-Live puts the picture into its thinking
  backend's context, native live models (Gemini, local servers with image
  input) receive it as a video frame. Your next words are the question.
- *Onto your next message*: the next spoken turn uses it, or — while the
  front-page chat is open — it appears in the composer as an attachment you
  can remove. It is single use and expires after `[screen_context].ttl_s`.
- Scheduled tasks, workflows and background agents never take a parked
  appshot; it waits for a person.

## Privacy

Appshots capture through the Screen Context engine
([screen-context.md](screen-context.md)), so everything there applies
unchanged: the app denylist, redaction of password fields and sensitive
patterns, and no image ever written to disk. For an area, a denylisted window
that overlaps the rectangle refuses the capture; one elsewhere on the screen
does not. The one difference: an appshot
shows no gold border before the shutter — the flash over the captured window
is the visible signal (maintainer directive 2026-09-29). **Allow appshots** on the Appshots page is `[screen_context].enabled`
— one switch for every screen look.

The shutter effect's thumbnail is cut from the frame in memory and piped only
to the local overlay process, which is excluded from screen capture on
Windows and hidden before any capture elsewhere. The last appshot shown on the
Appshots page is kept in memory for `[screen_context].deck_preview_s` seconds
and served with `Cache-Control: no-store`.

## Settings

`[appshot]` in `jarvis.toml`, written only through the app or
`jarvis api appshot put-settings`:

| Key | Default | Meaning |
|---|---|---|
| `hotkey` | `"alt+alt"` | Window appshot. `alt+alt` / `shift+shift` / `ctrl+ctrl` = both keys of that pair; any other combo in the shared hotkey syntax; `""` = off |
| `region_hotkey` | `"shift+shift"` | Area appshot, same syntax. Must differ from `hotkey` (the app refuses one key for both) |

Both shortcuts are changed on the Appshots page: **Change** records the next
key gesture (hold the keys, then let go; Esc cancels), the X turns the
shortcut off. Pressing the same modifier on both sides records the matching
two-sided gesture. For Shift and Ctrl, which are held all the time while
typing, the second key has to follow the first within half a second, so a
capital letter typed with one Shift never fires it; both-Alt keeps its
original any-order behaviour.
| `target` | `"auto"` | `auto` · `message` · `voice` (table above) |
| `sound` | `true` | Shutter sound; also needs `[ui].sound_effects` |
| `effect` | `true` | Flash and corner thumbnail |

## Operating systems

| | Windows | macOS | Linux/X11 | Wayland / headless |
|---|---|---|---|---|
| Both-Alt shortcut | `GetAsyncKeyState` (AltGr counts as right Alt) | `CGEventSourceKeyState`; whether this read needs the Input Monitoring grant is **unverified** (not measured on a Mac), so the page shows no Input Monitoring row for it | `XQueryKeymap` via python-xlib | Unavailable, reason shown on the page; voice and the button still work where capture works |
| Other shortcuts (incl. the area shortcut) | Shared hotkey backends (`jarvis/trigger/backends`) | same | same | same as above |
| Area picker | PySide6 overlay; a global Esc also cancels because Windows may not hand it keyboard focus until the first click | PySide6 overlay | PySide6 overlay | Unavailable, reason shown on the page; the window appshot still works |
| Flash + thumbnail | PySide6 overlay, excluded from capture | PySide6 overlay | PySide6 overlay | No overlay; the appshot is still taken where capture works |
| Capture | Screen Context engine | Needs Screen Recording; macOS asks for it at the first appshot you take | X11 | Honest refusal |

Only the instance that owns ambient duties (the default app, not the dev
instance) arms the shortcuts.

## Code

`jarvis/appshot/` — `service.py` (take and deliver), `store.py` (pending and
last appshot, memory only), `gesture.py` (both-Alt watcher), `hotkey.py`
(both shortcuts' lifecycle), `region.py` (area selection and its coordinate
mapping), `picker/` (the area picker sidecar), `effect.py` (shutter hook),
`delivery.py` (voice calls). The corner card lives in the indicator sidecar
(`jarvis/cu/indicator/renderer.py`, `_CardWindow`); the editor is
`frontend/src/views/AppshotEditor.tsx` with its document model in
`lib/appshotEditorModel.ts`.
The live model's `take_appshot` tool is `jarvis/plugins/tool/appshot.py`; the
REST surface is `jarvis/ui/web/appshot_routes.py`; the page is
`frontend/src/views/AppshotsView.tsx`.
