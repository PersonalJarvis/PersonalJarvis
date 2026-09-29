# Appshots

An appshot shows the assistant the window you are working in. It captures the
front window once — picture and on-screen text — and hands it to the
conversation as context. Settings live under **Settings > Appshots**.

## Three ways to take one

| Trigger | What happens |
|---|---|
| **Shortcut** — both Alt keys at once by default (both Option keys on a Mac) | The front window is captured and delivered per **Appshot destination**. |
| **Voice or chat** — "take an appshot", "mach einen Appshot", "haz un appshot" | The turn that asked captures the front window and answers with it. |
| **Try it** button on the Appshots page | Waits three seconds so you can switch windows, then behaves like the shortcut. |

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
patterns, and no image ever written to disk. The one difference: an appshot
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
| `hotkey` | `"alt+alt"` | `alt+alt` = both Alt keys; any other combo in the shared hotkey syntax; `""` = off |
| `target` | `"auto"` | `auto` · `message` · `voice` (table above) |
| `sound` | `true` | Shutter sound; also needs `[ui].sound_effects` |
| `effect` | `true` | Flash and corner thumbnail |

## Operating systems

| | Windows | macOS | Linux/X11 | Wayland / headless |
|---|---|---|---|---|
| Both-Alt shortcut | `GetAsyncKeyState` (AltGr counts as right Alt) | `CGEventSourceKeyState`, needs the Input Monitoring grant | `XQueryKeymap` via python-xlib | Unavailable, reason shown on the page; voice and the button still work where capture works |
| Other shortcuts | Shared hotkey backends (`jarvis/trigger/backends`) | same | same | same as above |
| Flash + thumbnail | PySide6 overlay, excluded from capture | PySide6 overlay | PySide6 overlay | No overlay; the appshot is still taken where capture works |
| Capture | Screen Context engine | Needs Screen Recording | X11 | Honest refusal |

Only the instance that owns ambient duties (the default app, not the dev
instance) arms the shortcut.

## Code

`jarvis/appshot/` — `service.py` (take and deliver), `store.py` (pending and
last appshot, memory only), `gesture.py` (both-Alt watcher), `hotkey.py`
(shortcut lifecycle), `effect.py` (shutter hook), `delivery.py` (voice calls).
The live model's `take_appshot` tool is `jarvis/plugins/tool/appshot.py`; the
REST surface is `jarvis/ui/web/appshot_routes.py`; the page is
`frontend/src/views/AppshotsView.tsx`.
