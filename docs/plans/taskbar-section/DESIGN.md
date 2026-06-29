# Taskbar Section + Dictation Features — Design Spec

**Date:** 2026-06-02
**Status:** Approved (design), implementation pending
**Author:** Jarvis-Agents (brainstormed with the maintainer)

---

## 1. Goal

Add a dedicated **"Taskbar"** sidebar section to the desktop app (sibling to Socials/Chats)
that owns the on-screen overlay settings, plus two dictation-style features:

1. **Show bar at all times** — toggle the jarvis-bar's persistent/standby behaviour
   (`[ui].bar_persistent`). OFF = no standby; the bar only pops up on "Hey Jarvis".
2. **Mute music while dictating** — while Jarvis is in a voice session, mute every OTHER
   app's audio (Spotify, browser music…) and restore it when the session ends.

The existing Bar/Mascot/None style selector **moves** from Settings into this new section.

## 2. Confirmed decisions

- The overlay-style selector is **moved** into the Taskbar section and **removed** from Settings
  (single source).
- "Mute music while dictating" mutes for the **whole voice session** (start→end), not per
  sub-state.
- It **fully mutes** other apps (not duck-to-20%).
- Design: brand guidelines (Charcoal card + Gold accents), grouped-card layout (grouped cards, label +
  toggle rows). No black/white, no AI slop.

## 3. Architecture

### A) New "Taskbar" sidebar section (frontend)

- `components/layout/Sidebar.tsx` — add a `NAV_ITEMS` entry
  `{ id: "taskbar", labelKey: "nav.taskbar", icon: <LucideIcon> }`.
- `store/events.ts` — add `"taskbar"` in **three** places: the `SectionId` union, the
  `SECTION_IDS` array, and the `SECTION_LABELS` record (AP-4 multi-layer drift — all three or a
  TS error / silent ChatsView fallback).
- `components/layout/MainView.tsx` — import `TaskbarView` + add `case "taskbar": return <TaskbarView />`.
- **New `views/taskbar/TaskbarView.tsx`** — `ViewHeader` + brand cards
  (`rounded-lg border border-border bg-card/60 p-4`):
  - Card **"Appearance"**: the moved `OverlayStylePanel` (Bar/Mascot/None `<select>` + Save).
  - Card **"Behavior"**: two toggle rows (Radix `Switch`, label + description left, switch right):
    - **"Show bar at all times"** → `bar_persistent`.
    - **"Mute music while dictating"** → `ducking.enabled`.
- `views/SettingsView.tsx` — remove `<OverlayStylePanel />` (line ~89) AND the
  `useOverlayStyle` import (line ~22). Move the `OverlayStylePanel` component into `TaskbarView`.
- New hooks `hooks/useBarPersistent.ts` + `hooks/useMuteMusic.ts` — GET/PUT a boolean, mirroring
  `useAutostart.ts`.
- i18n: `nav.taskbar` + a `taskbar_view.*` namespace in en/de/es.

### B) "Show bar at all times" — live (`bar_persistent`)

- `config.py` — `[ui].bar_persistent` already exists (default `True`). No model change.
- `config_writer.set_bar_persistent(enabled)` — `_patch_table(path, "ui", "bar_persistent", bool)`.
- `settings_routes.py` — `GET /api/settings/bar-persistent` (current value) + `PUT` (persist +
  live-apply via `request.app.state.desktop_app.set_bar_persistent(enabled)`).
- **`DesktopApp.set_bar_persistent(enabled)`** (new, live): flip `self._orb._persistent`, flip
  `self._bridge._hide_on_idle = not enabled`, then if now persistent `show("idle")` else, when
  currently idle, `hide()`. No new Tk root → safe, no restart. Returns `applied_live`.

### C) "Mute music while dictating" — audio ducking (new subsystem)

New package `jarvis/audio/ducking/`, mirroring the `jarvis/platform/` seam:

- `protocol.py` — `AudioDucker` Protocol: `mute_others() -> list[int]`, `restore(pids) -> None`.
- `windows.py` — `WindowsPycawDucker`: enumerate sessions via pycaw, mute every session whose
  `ProcessId` is not our own PID (and not in a name allowlist), return the PIDs it muted; restore
  unmutes exactly those. Lazy `import pycaw` inside the methods. Runs the blocking COM work inside
  the controller's `asyncio.to_thread` with `comtypes.CoInitialize()`.
- `null.py` — `NullDucker`: logged no-op (non-Windows / pycaw missing).
- `factory.py` — `make_audio_ducker()` → `WindowsPycawDucker` if `sys.platform=="win32"` and pycaw
  importable, else `NullDucker`.
- `controller.py` — **`AudioDuckController(bus, cfg, ducker)`**:
  - `attach()` subscribes `VoiceSessionStarted` (→ if `cfg.ducking.enabled`: mute, store PIDs) and
    `VoiceSessionEnded` (→ sleep `restore_delay_ms`, restore, clear PIDs). COM work via
    `asyncio.to_thread`.
  - `async restore()` — force-restore (called on shutdown so a crash mid-session never leaves the
    user's music muted).
  - `set_enabled(bool)` — live flip (the next session honours it; if turning OFF mid-session,
    restore now).
- Config `[ducking]`: `enabled: bool = False` (opt-in), `restore_delay_ms: int = 400`.
- `config_writer.set_mute_music(enabled)` — `_patch_table(path, "ducking", "enabled", bool)`.
- `settings_routes.py` — `GET/PUT /api/settings/mute-music` (persist + live `set_enabled`).
- Wiring: `desktop_app` near the bridge bootstrap — `self._ducker = make_audio_duck_controller(bus,
  cfg); self._ducker.attach()`. `await self._ducker.restore()` in shutdown teardown.
- `pyproject.toml` `[desktop]` extra: `pycaw; sys_platform == 'win32'`, `comtypes; sys_platform ==
  'win32'` (psutil already present). Base `python:3.11-slim` install unaffected (lazy import,
  NullDucker fallback).

## 4. Data flow

- **Session boundary mute:** `VoiceSessionStarted` → controller (if enabled) → `to_thread` →
  pycaw mutes other-PID sessions → store muted PIDs. `VoiceSessionEnded` → sleep
  `restore_delay_ms` (TTS tail grace) → `to_thread` → unmute exactly those PIDs → clear.
- **Own-PID exclusion is the TTS protection:** Jarvis's TTS plays via sounddevice in the same
  `pythonw.exe`; its session's `ProcessId == os.getpid()`, excluded from the mute sweep → never
  muted. No "is this the TTS stream" detection needed.
- **bar_persistent live:** PUT → `desktop_app.set_bar_persistent` flips two flags + show/hide.

## 5. Cross-platform & doctrine

- pycaw/comtypes are Windows-only, in `[desktop]` extra with `sys_platform=='win32'` markers.
- `make_audio_ducker()` returns `NullDucker` on non-Windows / when pycaw is absent → base headless
  install boots unaffected; the feature degrades to a logged no-op.
- Linux (`pactl set-sink-input-mute`) and macOS (Audio Tap, 14.2+) are noted as follow-ups; v1 is
  Windows + Null.

## 6. Invariants respected

- **AP-4 (multi-layer enum drift):** `"taskbar"` added to all three `store/events.ts` sites.
- **AP-7:** all config writes via `config_writer._patch_table` (lock + tempfile + BOM-safe).
- **AP-18:** the controller's bus handlers try/except; a failure never propagates.
- **AP-9/AP-11 (voice hot path):** ducking runs on session boundaries via `asyncio.to_thread`,
  never on the voice critical path; the brain/STT/TTS are untouched.
- **Cloud-first:** no new base dependency; Windows-only deps extras-gated; NullDucker no-op.
- **Crash-safety:** track-and-restore only the PIDs we muted; force-restore on shutdown.

## 7. Testing

CI-provable:
- `AudioDuckController` with a **fake ducker**: mute-on-`VoiceSessionStarted` only when enabled;
  restore-on-`VoiceSessionEnded`; PID tracking; `set_enabled(False)` mid-session restores;
  `restore()` idempotent; disabled = no calls.
- `make_audio_ducker()` returns NullDucker off-Windows / without pycaw.
- `config_writer.set_bar_persistent` / `set_mute_music` round-trips.
- Routes (`bar-persistent`, `mute-music`) via FastAPI TestClient: persist + live-apply calls.
- `DesktopApp.set_bar_persistent` logic with fake bar+bridge (flags flipped, show/hide called).
- Frontend: `npm run build` (tsc) + vitest; the new view renders.

Live sign-off (maintainer):
- pycaw mute/restore on the real desktop (Spotify mutes during a session, restores after).
- The Taskbar section renders on-brand; toggles work; selector moved.

## 8. Open items (resolve during build)

- The exact `restore_delay_ms` (start 400 ms; tune for TTS tail).
- Name allowlist for never-mute apps (Discord/Zoom/Teams) — config `[ducking].never_mute = []`,
  empty default; wire but not surfaced in UI for v1.
- Whether "Show bar at all times" should be disabled in the UI when style != jarvis_bar (it only
  affects the bar). v1: always shown, applies to the bar.
