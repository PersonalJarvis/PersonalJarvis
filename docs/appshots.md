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
selection, so nothing stays resident. Its look follows ShareX's region
capture:

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

- **Click** brings the app to the front with the **appshot editor** open
  over whatever was on screen (nothing navigates away). See
  [The editor](#the-editor).
- **Drag** the card into any app that accepts files or images (chat, mail,
  Explorer/Finder) to drop the picture there.
- **Right-click** dismisses it.

Only the finished, privacy-filtered appshot can leave by drag — the card's
own thumbnail is cut from the raw frame and never does. A drag is the one
moment an appshot touches disk: the picture is written to
`<temp>/jarvis-appshots/` just then, and files older than an hour are removed
on the next drag. With `[screen_context].deck_preview_s = 0` (keep nothing)
the card only opens the editor and shares nothing.

## The editor

The editor follows CleanShot X's annotate tool — same tools, same one-letter
keys, the same window shape — in this app's own look. It opens from the
corner card or from **Edit** on the last appshot (Settings → Appshots), as a
floating rounded window over the dimmed app; nothing navigates away.

- **Top bar:** close, crop and background, the tools as pills, the colour
  menu (presets + any colour) and the size slider, then **Save** and
  **Done**.
- **Options capsule:** under the top bar, only for tools that have options
  (text style, pixelate or blur, crop ratio, background) or a hint.
- **Bottom bar:** zoom (Fit, 50 %, 100 %, 200 %), undo and redo; a **Drag me**
  handle in the middle; delete (with a selection), save and copy on the right.

| Key | Tool | What it does |
|---|---|---|
| V | Select and move | Click an annotation to select it; drag moves it, arrow keys nudge it (Shift = 10 px), Delete removes it. Double-click a text to change it. |
| A / L | Arrow / Line | Shift snaps to 45°. |
| R / F / E | Rectangle / Filled rectangle / Ellipse | Shift makes a square or circle. |
| D | Draw | Freehand, smoothed. |
| M | Highlighter | Translucent marker that keeps text underneath readable. |
| T | Text | Plain, label (filled box) or outline style; Enter commits, Shift+Enter breaks the line. |
| C | Counter | Numbered badges; each click adds the next number. |
| H | Spotlight | Dims everything outside the dragged areas. |
| P | Pixelate or blur | Hides a region; the options bar switches between pixelate and blur. |
| K | Crop | Free, 1:1, 4:3 or 16:9; undo brings the rest back. |
| B | Background | Frames the picture on a backdrop (eight presets, padding, corner radius, shadow); applied on copy, save and use. |

Colours: eight presets plus any colour from the system picker; the last one
is remembered. Sizes: the slider or keys 1–5. History: Ctrl+Z, Ctrl+Shift+Z
or Ctrl+Y (⌘ on macOS).

Results: **Copy** (Ctrl+C) puts the PNG on the clipboard — natively through
`POST /api/appshot/clipboard` on the desktop, the browser clipboard
elsewhere. **Save** (Ctrl+S) writes it to Downloads through the backend
(the desktop WebView drops browser downloads) and the toast offers "Show in
folder". **Done** (Ctrl+Enter) replaces the held appshot so the next
message carries the edit (a picture already sent into a voice call stays
as it was). **Drag me** writes the finished picture to
`<temp>/jarvis-appshots/` the moment it is pressed and hands it to the native
drag bridge while the button is held, so it drops into any app as a real
file (Windows and macOS; Linux has no drag bridge yet, the handle is hidden
there and Save + "Show in folder" stand in). Escape closes a menu, cancels a
stroke, clears a selection, then closes; with unsaved edits it asks first.

### Compared with CleanShot X

Reference: [cleanshot.com/features](https://cleanshot.com/features) (checked
2026-10-03). Status here: **done** = in the editor; **elsewhere** = Personal
Jarvis covers it outside the editor; **not done** = not built, with the
reason.

| CleanShot X | Status |
|---|---|
| Arrow, line, rectangle, filled rectangle, ellipse, pencil, highlighter, text, counter, spotlight, pixelate, blur, crop with aspect ratio | Done |
| Background tool (presets, padding, aspect/position) | Done: presets, padding, corners, shadow. Custom uploads, saved presets, auto-balance and aspect ratio of the frame are not done. |
| Colour picker with saved palette | Done: presets + any colour, last one remembered (not a palette of several) |
| Arrow styles (4 curved) | Not done: one straight arrow |
| Text styles (7) | Partly: 3 styles |
| Move/select annotations | Done |
| Rotate, flip, resize image | Not done |
| Combine images, editable project file | Not done |
| Capture area / window / fullscreen, freeze, magnifier, crosshair | Elsewhere: the area picker and the window shortcut ([Selecting an area](#selecting-an-area)); no fullscreen mode |
| Quick Access Overlay (copy / save / annotate / drag after capture) | Elsewhere: the corner card (click = editor, drag = file) |
| Editor window: drag handle, zoom, Save / Done | Done: "Drag me" (Windows, macOS), zoom Fit/50/100/200 %, Save to Downloads, Done = use in the next message |
| Scrolling capture, self-timer | Not done (the page's "in 3 s" button is the only timer) |
| Screen recording, video editor, GIF | Not done |
| Cloud upload and sharing | Not done — appshots stay on this machine by design |
| Text recognition (OCR), QR codes | Not done in the editor; the model reads the picture itself when it gets it |
| Pin to screen (floating screenshot) | Not done: needs an always-on-top native window per OS |
| Capture history | Not done: only the last appshot is kept, in memory |

Every editor feature is web code in the app's own WebView, so it behaves the
same on Windows, macOS and Linux. The two native paths are the clipboard
(Windows `CF_DIB` + PNG, macOS `osascript`, Linux `wl-copy` or `xclip` —
without either, Copy falls back to the browser clipboard and says so if that
fails too) and Save (`~/Downloads` on every OS).

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
`delivery.py` (voice calls). The corner card lives in the indicator sidecar
(`jarvis/cu/indicator/renderer.py`, `_CardWindow`); the editor is
`frontend/src/views/AppshotEditor.tsx`, hosted by
`components/appshot/AppshotEditorHost.tsx` (opened through
`store/appshotEditor.ts`), with its document model in
`lib/appshotEditorModel.ts`, the copy path in `lib/appshotClipboard.ts` and
the native clipboard in `jarvis/platform/clipboard_image.py`.
The live model's `take_appshot` tool is `jarvis/plugins/tool/appshot.py`; the
REST surface is `jarvis/ui/web/appshot_routes.py`; the page is
`frontend/src/views/AppshotsView.tsx`.
