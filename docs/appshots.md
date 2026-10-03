# Appshots

An appshot shows the assistant the window you are working in — or exactly
the part of the screen you select. It captures once — picture and on-screen
text — and hands it to the conversation as context. Settings live under
**Settings > Appshots**.

## Ways to take one

| Trigger | What happens |
|---|---|
| **Window shortcut** — both Alt keys at once by default (both Option keys on a Mac) | The front window is captured and delivered per **Appshot destination**. |
| **Area shortcut** — both Shift keys at once by default | Every screen dims; drag a rectangle, mark it up right there with the toolbar that docks under it, and Enter captures exactly that part with your markings and delivers it the same way. Esc or a right-click cancels and sends nothing. |
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

- every screen is frozen under a light dim the moment the picker opens, so
  nothing moves under the selection (where a frozen frame cannot be grabbed,
  a dim layer over the live desktop is used instead);
- the pointer is a thin crosshair with a small ring, and two small numbers
  beside it show the position — while dragging, the width and height in real
  pixels. No lens, no boxed labels, no banner;
- hovering lifts the window under the pointer out of the dim, and a click
  without a drag takes exactly that window;
- a drag clears the dim and tints the selected area a translucent grey
  with one thin border.

### Marking up before the shot

Letting go of the drag (or clicking a window) does not take the shot yet. The
area stays on screen with eight resize handles (they are the crop), its size
in pixels above its corner, and a toolbar docked under it (above it, or
inside it, when there is no room). Everything drawn here is what the
assistant sees, so it can be pointed at directly. The tools, keys and looks
are the same as in the full editor below:

| Key | Tool | |
|---|---|---|
| V | Select and move | Click a marking to pick it, drag to move it, Delete removes it, arrow keys nudge it |
| R | Rectangle | Shift = square |
| F | Filled rectangle | Shift = square |
| E | Ellipse | Shift = circle |
| L | Line | Shift snaps to 45 degrees |
| A | Arrow (default) | Tapered, classic or two heads; Shift snaps to 45 degrees |
| T | Text | Click, type; Enter finishes, Shift+Enter is a new line, Ctrl+V pastes; plain, label or outline look |
| P | Pixelate / blur | Hides what is under the box |
| H | Spotlight | Dims everything outside the box |
| C | Numbered step | Each click places the next number |
| D | Draw | Free stroke |
| M | Highlighter | Wide translucent stroke |
| B | Background | Puts the area on a gradient or solid frame |

Tools with looks (arrow, text, pixelate/blur, background) show them in a
second row under the toolbar. Colours: eight presets. Size: keys 1-5, the
size button or the mouse wheel (new text and step markers follow it). Ctrl+Z
/ Ctrl+Y undo and redo. Every marking shows its points right after it is
drawn (the previous one loses them), and any drawing tool takes a drawn
marking under the pointer again (pen and highlighter only its points; V also
takes pixelate and spotlight boxes): lines and arrows have one at each end
and one in the middle that bends them into a curve, boxes and strokes one at
each corner, a step marker one on its rim, and text one at each corner —
dragging that scales the text, font size and all, from the opposite corner. A colour, size or
look chosen then changes that marking. Double-clicking a text (with V or T)
edits it. Dragging an area handle resizes the area at any time; a press
outside an unmarked area, or a right-click on it, starts a new selection.

Finishing:

| Key / button | Result |
|---|---|
| Enter / check button | Take the appshot with the markings and deliver it |
| Ctrl+C / Copy | The same, and the finished picture goes to the clipboard |
| Ctrl+S / Save | The same, and a PNG goes to `~/Downloads` |
| Ctrl+E / Editor | The same, and the full appshot editor opens on it |
| Esc / cross | Cancel; nothing is captured |

The capture itself is unchanged: after the overlay closes, Screen Context
grabs the area (denylist, redaction and all), and only that finished picture
gets the markings (`jarvis/appshot/markup.py`) — blur and pixelate on the
capture's own pixels first, then the drawn markings as a transparent layer
stretched to the capture's size, then the background frame if one was
chosen. The markings never show anything the
privacy filter removed. A marked appshot carries the same note as an editor
edit (the markings are the user's), and the corner card's thumbnail shows
them too. While the toolbar is up the global Esc stops cancelling, so Esc can
end a text box without throwing the selection away.

The picker reports the rectangle as fractions of the screen it was drawn on;
the app maps that back to capture pixels (`jarvis/appshot/region.py`), so
mixed-DPI setups capture exactly what was outlined. A selection stays on one
screen. The window list for snapping is read before the overlay appears and
skips minimized and (on Windows) cloaked windows; where no list is available
(Wayland) only dragging works. The picker gives up after fifteen minutes
without a result.

A selected area is about pixels, not about the window in front: unlike a window
appshot it is not voided when focus moves while the picker closes. Its privacy
guard is the denylist check on every visible window that overlaps the
rectangle (see below).

A spoken "what do you see?" is the same look (Screen Context); it also plays
the shutter and shows up as the last appshot.

## The card in the corner, and the editor

After the shutter the picture flies into the bottom-right corner of its
screen and rests there as a quick-access card. Its lifetime is set by
`[appshot].card_seconds` (Settings → Appshots → *Corner card*: 3, 6,
10 or 30 seconds, or until you close it); the pointer on it keeps it.

Several appshots **stack**: the newest always lands at the bottom and the
cards already there glide up to make room; when a card goes,
the ones above it glide down. Up to five cards stay (the oldest leaves first,
or earlier when the screen has no room). Every card belongs to its own
appshot, so Copy, Save, Copy text, Edit and a drag on an older card reach
that card's picture (`AppshotStore` keeps the last five, each for
`[screen_context].deck_preview_s`).

- **Hover** frosts the picture and shows **Copy** and **Save** stacked in
  the middle, plus four round chips in the corners: **Pin** (top left),
  **Close** (top right), **Edit** (bottom left) and **Copy text** (bottom
  right). The chip under the pointer names itself along the bottom edge.
  Copy puts the picture on the clipboard, Save writes it to Downloads and
  Copy text copies the on-screen text the appshot read (where the screen had
  any) — all done by the main process (`jarvis/appshot/card_actions.py`), so
  the clipboard survives the overlay quitting on Linux; the card shows the
  result for a moment. **Pin** keeps the card until you close it, whatever
  *Corner card* says; a pinned card shows the pin even without the pointer.
- **Click** (or Edit) opens the **appshot editor in a window of its own** in
  front of you; the app behind keeps its size and what it shows. Without a
  desktop shell (a browser, a headless host) the editor opens over the app's
  page instead. See [The editor](#the-editor).
- **Save** or **Done** in the editor flies the edited picture from the
  editor back to the bottom of the stack (the shutter's flight, without the
  flash or a sound); **Close** slides the unchanged picture in from the edge.
  Either way it stays at hand.
- **Drag** the card into any app that accepts files or images (chat, mail,
  Explorer/Finder) to drop the picture there. A smaller
  rounded copy lifts off and stays where you grabbed it, and a green "+"
  beside the pointer shows that the text field or window under it takes the
  picture (macOS shows its own green badge). Dropped nowhere, or cancelled
  with Esc, the card stays; after a drop it slides away.
- **Right-click** dismisses it.

Only the finished, privacy-filtered appshot can leave by drag — the card's
own thumbnail is cut from the raw frame and never does. A drag is the one
moment an appshot touches disk: the picture is written to
`<temp>/jarvis-appshots/` just then, and files older than an hour are removed
on the next drag. With `[screen_context].deck_preview_s = 0` (keep nothing)
the card only opens the editor and shares nothing.

## The editor

The editor provides annotation tools, one-letter shortcuts and a compact
window for editing a capture. It opens from the corner card or from **Edit**
on the last appshot (Settings → Appshots) — in
its own desktop window (`?view=appshot-editor`, `views/AppshotEditorWindow.tsx`),
or, without a desktop shell, as a floating rounded window over the dimmed
app. Nothing navigates away.

- **Top bar:** close, crop and background, the tools as pills, the colour
  menu (presets + any colour) and the size slider, then **Save** and
  **Done**.
- **Options capsule:** under the top bar, only for tools that have options
  (text style, pixelate or blur, crop ratio with size, Reset and Crop,
  background) or a hint. **Add picture** opens a side panel instead.
- **Bottom bar:** zoom (Fit, 50 %, 100 %, 200 %), undo and redo; a **Drag me**
  handle in the middle; delete (with a selection), save and copy on the right.

| Key | Tool | What it does |
|---|---|---|
| V | Select and move | Click an annotation to select it; drag moves it, arrow keys nudge it (Shift = 10 px), Delete removes it. Double-click a text to change it. The only tool that also picks spotlights and redactions. |
| A / L | Arrow / Line | Shift snaps to 45°. |
| R / F / E | Rectangle / Filled rectangle / Ellipse | Shift makes a square or circle. |
| D | Draw | Freehand, smoothed. |
| M | Highlighter | Translucent marker that keeps text underneath readable. |
| T | Text | Plain, label (filled box) or outline style; Enter commits, Shift+Enter breaks the line. |
| C | Counter | Numbered badges; each click adds the next number. |
| H | Spotlight | Dims everything outside the dragged areas. |
| P | Pixelate or blur | Hides a region; the options bar switches between pixelate and blur. |
| K | Crop | A frame over the whole document with corner grips (and edge grips for a free crop), a thirds grid and its size in pixels. Drag a grip or the frame itself, or drag outside it for a new one; Free, 1:1, 4:3 or 16:9 (a ratio reshapes the frame at once). Enter or **Crop** finishes, **Reset** shows everything again, undo brings the rest back. |
| B | Background | Frames the picture on a backdrop (eight presets, padding, corner radius, shadow); applied on copy, save and use. |
| I | Add picture | Puts another picture into the document: an earlier appshot from the gallery, a file, a pasted picture (Ctrl+V) or one dropped onto the editor. It goes **beside** (same height), **below** (same width) or **on top** (small, in the middle) of what is visible; the choice is remembered. |

**Several pictures in one.** An added picture is a layer of the document,
painted under every annotation, so an arrow or a highlight can run from one
picture into the next. The select tool (V) moves it and its corner grips
resize it without distorting it; Delete removes it. The document grows with
its pictures, a crop that is in place grows to take a new picture in, and
Copy, Save, Drag me and Done hand on the combined picture. Pixelate and blur
hide whatever picture lies under them.

Taking hold of annotations: the **select tool** picks up any annotation —
the pointer turns into a move cursor over it, a drag moves it, a click
selects it. A **shape tool** (arrow, line, boxes, text, spotlight, redact)
selects the shape it just drew and can reshape or move only that one; a
press anywhere else, also on an older annotation, draws a new shape.
**Draw, Highlighter and Counter never take hold of anything**: their strokes
and badges are not selected, so writing over earlier ink or placing the next
number right beside the last one always draws. A selected annotation shows
round grips — at both ends of an arrow or line (drag the tip somewhere
else), at the corners of a box, a stroke or a redaction (resize from the
opposite corner), at a text's corner (its size) and on a counter's rim (the
badge's size). Every move and reshape is one undo step.

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

### Feature coverage

Status here: **done** = in the editor; **elsewhere** = Personal Jarvis covers
it outside the editor; **not done** = not built, with the reason.

| Feature | Status |
|---|---|
| Arrow, line, rectangle, filled rectangle, ellipse, pencil, highlighter, text, counter, spotlight, pixelate, blur, crop with aspect ratio | Done |
| Background tool (presets, padding, aspect/position) | Done: presets, padding, corners, shadow. Custom uploads, saved presets, auto-balance and aspect ratio of the frame are not done. |
| Colour picker with saved palette | Done: presets + any colour, last one remembered (not a palette of several) |
| Arrow styles (4 curved) | Not done: one straight arrow |
| Text styles (7) | Partly: 3 styles |
| Move/select annotations | Done |
| Rotate, flip, resize image | Not done |
| Combine images | Done: add pictures beside, below or on top (gallery, file, paste, drop) |
| Editable project file | Not done |
| Capture area / window / fullscreen, freeze, crosshair with coordinates | Elsewhere: the area picker and the window shortcut ([Selecting an area](#selecting-an-area)); no fullscreen mode |
| Corner card (copy / save / annotate / drag after capture) | Elsewhere: the corner card (click = editor, drag = file) |
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

## What the assistant gets after an edit

An appshot is two things at once: a screenshot the user keeps, and context
for the assistant. **Done** decides the second: the edited picture becomes
the appshot (`PUT /api/appshot/latest/image`), and `deliver_edit` in
`jarvis/appshot/service.py` hands it over with a note that the markings are
the user's — arrows, boxes, numbers and text point at what they mean, hidden
parts were hidden on purpose:

| Situation | Where the edit goes |
|---|---|
| A voice call runs and `target` is `auto` or `voice` | Into the call at once — even when the call already saw the original. A waiting original is withdrawn, so it does not follow later. |
| No call (or `target = message`) | Onto the next message — even when the original was sent already. |
| `target = voice` and no call | Nowhere; the edit is kept as the last appshot. |

The editor says which one happened. Copy, Save and Drag never send anything
to the assistant; they are the screenshot-tool half.

## The gallery

Below the settings, **Your appshots** shows every appshot taken with the
shortcuts, the page's buttons or the assistant's `take_appshot` tool, and every
edit saved in the editor, newest first. An edit sits right before its original
and wears an **Edited** badge; the **Edited** filter shows only those. A look
a conversation turn took on its own is never kept.

- **Click** a picture to open it in the editor again. Saving there keeps the
  new edit beside the original and hands it to the assistant like any edit.
- **Drag** a picture into a chat composer or a terminal pane to attach it.
  The drag carries the picture's real path the way a row from the workspace
  explorer does, so both drop targets take it unchanged. In a browser the
  drag also carries a `DownloadURL`, so it can be dropped onto the desktop.
  Inside the desktop shell the grip on a tile starts a native file drag that
  reaches any other app (mail, Explorer/Finder, a browser upload).
- **Delete** on an edited tile removes only that edit; on an original it
  removes the appshot and its edit. **Delete all** asks once more first.

The pictures live in `<data dir>/appshots/<id>/` (`original.<ext>`,
`edited.png`, `meta.json`, small thumbnails made on first view). Only the
finished, privacy-filtered appshot is written — never the raw frame or the
on-screen text. The library keeps the newest 500 appshots and removes the
oldest first. **Keep appshot history** (`[appshot].library`) turns it off;
what is already kept stays until it is deleted.

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
unchanged: the app denylist and redaction of password fields and sensitive
patterns. The capture itself writes nothing to disk; the one deliberate copy
is the gallery's history (see [The gallery](#the-gallery)), which can be
switched off and deleted. For an area, a denylisted window
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
| `card_seconds` | `6` | How long the corner card rests, 0–600 seconds; `0` = until closed |
| `library` | `true` | Keep every appshot and saved edit in the gallery (`<data dir>/appshots/`, newest 500) |

## Operating systems

| | Windows | macOS | Linux/X11 | Wayland / headless |
|---|---|---|---|---|
| Both-Alt shortcut | `GetAsyncKeyState` (AltGr counts as right Alt) | `CGEventSourceKeyState`, needs the Input Monitoring grant | `XQueryKeymap` via python-xlib | Unavailable, reason shown on the page; voice and the button still work where capture works |
| Other shortcuts (incl. the area shortcut) | Shared hotkey backends (`jarvis/trigger/backends`) | same | same | same as above |
| Area picker and its marking toolbar | PySide6 overlay; a global Esc also cancels until an area is chosen, because Windows may not hand it keyboard focus until the first click | PySide6 overlay | PySide6 overlay | Unavailable, reason shown on the page; the window appshot still works |
| Burning the markings into the appshot | Pillow, in the main process (`jarvis/appshot/markup.py`) | same | same | same (no display needed) |
| Flash + thumbnail | PySide6 overlay, excluded from capture | PySide6 overlay | PySide6 overlay | No overlay; the appshot is still taken where capture works |
| Capture | Screen Context engine | Needs Screen Recording | X11 | Honest refusal |

Only the instance that owns ambient duties (the default app, not the dev
instance) arms the shortcuts.

## Code

`jarvis/appshot/` — `service.py` (take and deliver), `store.py` (pending and
last appshot, memory only), `library.py` (the gallery's history on disk), `gesture.py` (both-Alt watcher), `hotkey.py`
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
`frontend/src/views/AppshotsView.tsx`, its gallery
`frontend/src/views/AppshotLibrary.tsx`.
