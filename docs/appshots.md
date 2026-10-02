# Appshots

An appshot shows the assistant the window you are working in — or exactly
the part of the screen you select. It captures once — picture and on-screen
text — and hands it to the conversation as context. Settings live under
**Settings > Appshots**.

## Ways to take one

| Trigger | What happens |
|---|---|
| **Window shortcut** — both Alt keys at once by default (both Option keys on a Mac) | The front window is captured and delivered per **Appshot destination**. |
| **Area shortcut** — Alt+Win+A by default (Option+Command+A on a Mac) | Every screen dims; drag a rectangle and exactly that part is captured and delivered the same way. Esc or a right-click cancels and sends nothing. |
| **Voice or chat** — "take an appshot", "mach einen Appshot", "haz un appshot" | The turn that asked captures the front window and answers with it. Only an explicit request for the whole screen ("an appshot of my full screen") captures the monitor the cursor is on instead — the live model's `take_appshot` passes `scope: "screen"` for it. |
| **Try it** buttons on the Appshots page | "Take appshot in 3 s" waits three seconds so you can switch windows; "Select area" opens the area picker at once. Both then behave like the shortcuts. |

"Front window" means the app window you work in. Jarvis's own floating
overlays (the mascot, the bar) take the focus when you click them, so the
capture looks past them to the app window underneath (BUG-228); with no app
window there, it takes the whole screen instead.

## Selecting an area

The area picker is a short-lived PySide6 process (`python -m
jarvis.appshot.picker`) that starts on the shortcut and exits after one
selection, so nothing stays resident. Its look follows ShareX's region
capture:

- every screen is frozen and dimmed the moment the picker opens, so nothing
  moves under the selection (where a frozen frame cannot be grabbed, a dim
  layer over the live desktop is used instead, without the magnifier);
- hovering highlights the window under the pointer, and a click without a
  drag takes exactly that window;
- a drag cuts the area out of the dim layer with a marching-ants border and
  its size in real pixels;
- a magnifier beside the pointer shows a zoomed pixel grid with the centre
  pixel marked, plus the position (or the selection size while dragging).

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
| `hotkey` | `"alt+alt"` | Window appshot. `alt+alt` = both Alt keys; any other combo in the shared hotkey syntax; `""` = off |
| `region_hotkey` | `"alt+win+a"` | Area appshot, same syntax. Must differ from `hotkey` (the app refuses one key for both) |
| `target` | `"auto"` | `auto` · `message` · `voice` (table above) |
| `sound` | `true` | Shutter sound; also needs `[ui].sound_effects` |
| `effect` | `true` | Flash and corner thumbnail |

## Operating systems

| | Windows | macOS | Linux/X11 | Wayland / headless |
|---|---|---|---|---|
| Both-Alt shortcut | `GetAsyncKeyState` (AltGr counts as right Alt) | `CGEventSourceKeyState`, needs the Input Monitoring grant | `XQueryKeymap` via python-xlib | Unavailable, reason shown on the page; voice and the button still work where capture works |
| Other shortcuts (incl. the area shortcut) | Shared hotkey backends (`jarvis/trigger/backends`) | same | same | same as above |
| Area picker | PySide6 overlay; a global Esc also cancels because Windows may not hand it keyboard focus until the first click | PySide6 overlay | PySide6 overlay | Unavailable, reason shown on the page; the window appshot still works |
| Flash + thumbnail | PySide6 overlay, excluded from capture | PySide6 overlay | PySide6 overlay | No overlay; the appshot is still taken where capture works |
| Capture | Screen Context engine | Needs Screen Recording | X11 | Honest refusal |

Only the instance that owns ambient duties (the default app, not the dev
instance) arms the shortcuts.

## Code

`jarvis/appshot/` — `service.py` (take and deliver), `store.py` (pending and
last appshot, memory only), `gesture.py` (both-Alt watcher), `hotkey.py`
(both shortcuts' lifecycle), `region.py` (area selection and its coordinate
mapping), `picker/` (the area picker sidecar), `effect.py` (shutter hook),
`delivery.py` (voice calls).
The live model's `take_appshot` tool is `jarvis/plugins/tool/appshot.py`; the
REST surface is `jarvis/ui/web/appshot_routes.py`; the page is
`frontend/src/views/AppshotsView.tsx`.
