# Jarvis-Bar Overlay — Design Spec

**Date:** 2026-06-01
**Status:** Approved (design), implementation pending
**Author:** Jarvis-Agents (brainstormed with the maintainer)

---

## 1. Goal

Replace the bulky mascot "ghost" orb as the **default** on-screen representation of Jarvis
with a slim, dictation-style pill bar that lives at the bottom-center of the screen and
encodes Jarvis's state purely through animation (no text). The existing mascot orb stays
fully intact and remains selectable.

The bar must visualize four states, each with a distinct motion:

| State | Visual | Drive signal |
|---|---|---|
| **Idle** | Small pill with a row of muted dots | none (static / gentle) |
| **Listening** (user speaking) | Vertical equalizer bars; height tracks live mic loudness | microphone RMS |
| **Thinking** (brain generating) | Flowing wave motion across the pill | synthetic time-driven sine |
| **Speaking** (Jarvis answering) | Vertical equalizer bars; height tracks Jarvis's voice | real TTS output RMS |

Gold (`#e7c46e`) only lights up **during activity**; idle dots are a muted grey so the bar is
calm at rest.

## 2. Confirmed product decisions

- **Default style:** `jarvis_bar`. Mascot orb (`mascot`) and `none` remain selectable.
- **Position:** bottom-center by default. **Hold-left-mouse + drag** repositions it; the position
  is persisted (same UX as the current draggable orb).
- **Single left-click:** starts a voice session — the same entry point as the wake word / the
  push-to-talk hotkey. (Click = "tap to talk"; hold+drag = move. Disambiguated by a press
  duration + movement threshold.)
- **Visibility:** configurable; **default = always visible** (persistent dots pill, expands on
  activity). When disabled, the bar behaves like today's orb: hidden until a voice session,
  grace-hide afterward.
- **Text:** none. The bar is pure animation — no transcript bubble, no status label.
- **Colour:** gold-accent-on-activity (idle muted, gold on listen/think/speak).

## 3. Architecture

**Chosen approach:** a new sibling render surface that reuses the existing event bridge.

The current orb is driven by `OrbBusBridge` (`jarvis/ui/orb/bus_bridge.py`), which subscribes to
the `EventBus` and calls a small duck-typed surface API on `OrbOverlay`
(`jarvis/ui/orb/overlay.py`): `show(mode)`, `hide()`, `set_level(float)`, `play_animation(...)`,
`stop_animation()`, `show_listening_transcript(...)`, `hide_comment()`,
`start_mouth_animation(...)`, `stop_mouth_animation(...)`.

We add a new `JarvisBarOverlay` that implements the **same surface API** (no-op for the
text/mouth methods, since the bar has no text or mouth). `desktop_app._start_speech_and_orb`
selects which surface to construct based on `[ui].orb_style`. The bridge is reused **unchanged**.

Rejected alternatives:
- **B — render mode inside `overlay.py`:** bloats the 2330-line file, mixes two visual languages.
- **C — React/pywebview transparent window:** new fragile transparent-window plumbing,
  duplicate event bridge over WebSocket, against the existing Tk seam and the cloud-first
  simplicity doctrine.

### 3.1 New files

| File | Purpose |
|---|---|
| `jarvis/ui/jarvisbar/__init__.py` | Package marker + public exports |
| `jarvis/ui/jarvisbar/renderer.py` | Pure rendering math + drawing of pill / dots / bars / wave. State-and-level → frame. No Tk, no I/O — unit-testable. |
| `jarvis/ui/jarvisbar/overlay.py` | `JarvisBarOverlay` — Tk window, daemon thread, thread-safe `_enqueue_ui` queue, ~60 FPS loop, color-key transparency, implements the surface API |
| `jarvis/ui/jarvisbar/interaction.py` | Click-vs-hold-drag discrimination (duration + movement threshold) + position persistence helpers |
| `jarvis/audio/level_tap.py` | Tiny throttled RMS helper + a process-local pub/sub for the TTS output level (out-of-band, NOT the EventBus) |

### 3.2 Touched files (additive)

| File | Change |
|---|---|
| `jarvis/core/config.py` | Extend `UIConfig.orb_style` accepted values to include `"jarvis_bar"` and `"none"`; default becomes `"jarvis_bar"`. Add `UIConfig.bar_persistent: bool = True`, `UIConfig.bar_accent: str = "active_only"`. |
| `jarvis/ui/desktop_app.py` | In `_start_speech_and_orb`, branch on `orb_style`: build `JarvisBarOverlay`, `OrbOverlay`, or nothing. Wire the same `OrbBusBridge`. |
| `jarvis/audio/player.py` | In the `_write_samples` flush (already off the hot path, inside `asyncio.to_thread`), compute a throttled RMS on the already-materialized float32 array and publish it via `level_tap` (process-local, ~25 Hz max). Guarded so it is a no-op when nobody is subscribed. |
| `jarvis/overlay/surface.py` *(optional, seam parity)* | Teach `make_overlay_surface` about `jarvis_bar` for the `OverlaySurface` protocol seam. |
| React settings *(optional, later wave)* | A "Display style: Bar / Mascot / Off" selector. Config-only is acceptable for the first cut. |

## 4. Data flow

Two clearly separated channels — this is the core of the design:

1. **State transitions (low frequency, via EventBus).**
   `SystemStateChanged(new_state ∈ {IDLE, LISTENING, THINKING, SPEAKING})`
   (`jarvis/state/supervisor.py:57`) → `OrbBusBridge` → `surface.show(mode)`.
   Plus `VoiceSessionStarted/Ended` for the suppression latch.

2. **Amplitude (high frequency, OUT-OF-BAND — never the EventBus).**
   Publishing ~25 Hz level samples on the bus would spam the flight-recorder wildcard
   subscriber (5 s cap). Instead:
   - **Mic level** during LISTENING: reuse the existing `jarvis/ui/orb/mic_listener.py`,
     which already taps sounddevice and calls `surface.set_level()`.
   - **TTS level** during SPEAKING: the new `level_tap` carries the player's output RMS
     directly to `surface.set_level()`.
   - **Thinking wave:** no external signal — the renderer generates a time-driven sine.

The renderer applies easing/smoothing to the incoming level so the bars glide instead of
jittering, and decays to the idle pill when no level arrives.

## 5. Render mechanics

- Own Tk `Toplevel`/root, `overrideredirect(True)`, `-topmost`, color-key transparency
  (magenta `#FF00FF`, same technique as the orb).
- **BUG-030 guard:** re-apply `wm_attributes("-transparentcolor", ...)` after any Win32 style
  mutation; never drop `WS_EX_LAYERED`. A regression here turns the screen black.
- **Thread safety:** all Tk mutations from the asyncio bridge thread go through `_enqueue_ui()`
  → `queue.Queue` drained by `root.after(20, ...)` on the Tk thread. Never call Tk directly
  from the bridge thread.
- ~60 FPS via `root.after(16, ...)`.
- Pill geometry: collapsed ≈ 150×36 px (idle), expanded ≈ 280×52 px (active). Window is sized
  to the expanded bounds; the pill is drawn smaller when collapsed.
- Bar count ≈ 7–9 vertical bars; thinking renders a connected sine polyline.

## 6. Interaction

- **Press classification** (`interaction.py`): on mouse-down start a timer and record the
  origin. If the pointer moves beyond a 16 px manhattan threshold → **drag** (reposition,
  persist). If released under the threshold and under a short duration → **click** → start a
  voice session via the same entry the wake word uses. (Hold without moving past the
  threshold and past the duration still resolves to drag-arming, matching the user's "halten =
  verschieben" intent.)
- Position persistence reuses the orb's `drag_persistence` pattern (clamp to work area, save to
  `jarvis.toml` via the atomic config writer — never a raw write, AP-7).

## 7. Visibility lifecycle

- `bar_persistent = true` (default): the dots pill is always shown; `show(mode)` expands it,
  IDLE collapses it back to dots (does **not** hide it).
- `bar_persistent = false`: IDLE hides the window after the existing grace delay; the bar appears
  only on a voice session (current orb behavior). The suppression latch in `OrbBusBridge`
  (anti-resurrection) is honored in both modes.

## 8. Cross-platform & doctrine

- Gated behind `probes.has_overlay()` (`display_present() and tkinter`). Headless €5-VPS →
  `has_overlay = False` → no bar, no crash (TrayOnly floor). The base `python:3.11-slim` boot
  imports nothing new.
- **No new base dependency.** numpy/PIL/tkinter are already desktop-only and lazily imported.
- macOS uses the same `-transparentcolor`; Linux is best-effort (existing seam behavior).

## 9. Invariants respected

- **AP-9 / AP-11:** the TTS RMS is computed on an array that already exists, inside the existing
  `to_thread` flush — off the voice critical path, no LLM, no added latency.
- **BUG-030:** color-key re-applied after style mutations.
- **AP-7:** position writes go through the atomic config writer.
- **`[overlay].enabled` is NOT reused** — it already belongs to the Phase-9 Computer-Use overlay
  subprocess. Selection lives in `[ui].orb_style`.
- **Suppression latch** (anti-orb-resurrection) reused via `OrbBusBridge`.
- **EventBus contract:** the bar's bus handlers are synchronous and fast (well under the 5 s
  wildcard cap); amplitude never touches the bus.

## 10. Testing strategy

CI-provable (no GUI required):
- `renderer.py` unit tests: state → expected geometry/element set; level → bar heights monotonic;
  thinking wave is bounded; smoothing/decay behavior.
- `interaction.py` unit tests: click vs drag classification across duration/movement matrices.
- `level_tap.py` unit tests: throttling cap, no-subscriber no-op, RMS correctness on known PCM.
- `config.py`: `orb_style` accepts the three values + coerces legacy; defaults are correct.
- Surface API contract: `JarvisBarOverlay` exposes the same methods `OrbBusBridge` calls
  (duck-typed parity test against the bridge's call sites).
- Headless import/boot: importing the package and constructing the bridge with a fake surface on
  a no-display environment does not raise.

Live sign-off (maintainer, on the real desktop — cannot be CI-verified):
- Bar visible bottom-center, gold-on-activity, four states animate correctly.
- Click starts a session; hold+drag repositions and persists across restart.
- Switching `orb_style` back to `mascot` restores the ghost unchanged.

## 11. Open implementation details (resolve during build)

- Exact wake/voice-session entry point the click should call (the same path the hotkey/wake uses
  in `pipeline`/`desktop_app`).
- Whether `mic_listener` is currently coupled to `OrbOverlay` specifically or already surface-
  agnostic (it calls `set_level` — confirm it can target the new surface).
- Throttle rate for the TTS level (start at ~25 Hz; tune for smoothness vs cost).
- Whether to expose the React selector in this wave or ship config-only first.
