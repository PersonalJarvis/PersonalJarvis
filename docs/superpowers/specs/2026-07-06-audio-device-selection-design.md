# Audio Device Selection in Settings — Design

**Date:** 2026-07-06
**Status:** Approved for implementation (maintainer requested analyze + implement in one pass)

## Goal

Let the user pick, inside the Jarvis Settings view, (a) which audio OUTPUT
device Jarvis's voice plays on and (b) which MICROPHONE Jarvis listens with —
with an "Automatic" default, live application (no app restart), and graceful
cross-platform / headless degradation.

## What already exists (deep-dive result)

The heavy lifting is already in place; this feature is mostly *surfacing* it:

- `[audio].input_device` / `[audio].output_device` (str, default
  `"auto-headset"`) already exist in `AudioConfig`
  (`jarvis/core/config.py:1125`) and are threaded into the `SpeechPipeline`
  (`jarvis/ui/desktop_app.py:2404`) and the voice watchdog
  (`jarvis/speech/watchdog.py:150`).
- Output: `AudioPlayer` (`jarvis/audio/player.py`) resolves `"auto-headset"`,
  skips the localized MME/DirectSound mapper, HDMI/SPDIF sinks and the
  WDM-KS host API (BUG-014), and has a runtime `set_device()` hot-swap.
- Input: `MicrophoneCapture` (`jarvis/audio/capture.py`) has the twin resolver
  (loopback/virtual-mic aware, MME-preferred for 16 kHz) plus host-API
  fallback and a stall watchdog.
- Live re-arm: the wake session already aborts + reopens the mic when
  `SpeechPipeline._wake_reload_event` is set (`jarvis/speech/pipeline.py:4434`)
  — built for live wake-word switches, reusable for a mic switch.
- Settings pattern: the `tts-volume` GET/PUT route pair
  (`jarvis/ui/web/settings_routes.py:1735`) + `useTtsVolume` hook +
  `VolumeGroup.tsx` is the exact end-to-end template (persist via
  `config_writer`, live-apply via pipeline, i18n keys in en/de/es).
- `[audio]` is NOT pinned in `scripts/config-soll.json`, so a plain TOML patch <!-- i18n-allow: filename reference -->
  persists without drift-guard sync.

## Gap

1. No way to ENUMERATE devices for a picker (REST).
2. A concrete device NAME in `[audio].*_device` is today handed RAW to
   PortAudio, where names are ambiguous across host APIs (same endpoint
   appears under MME/DirectSound/WASAPI/WDM-KS) — unusable as a persisted
   selection.
3. No Settings UI.

## Design

### Approaches considered

- **A (chosen): persist the device NAME in the existing `[audio].*_device`
  keys + make the resolvers name-aware.** No schema change, backward
  compatible, names survive reboots/hot-plug (indices do not), and the
  existing auto-headset machinery provides the fallback when the named device
  is unplugged.
- B: persist the PortAudio device index — rejected: indices shift on every
  hot-plug/reboot (the exact BUG-014 drift class `_stabilize_audio_devices`
  exists to fight).
- C: write the picked name into `*_device_priority` — rejected: conflates the
  power-user priority list with the explicit pick and would overwrite
  hand-maintained entries.

### 1. New module `jarvis/audio/devices.py`

- `list_devices(*, output: bool) -> list[AudioDeviceInfo]` —
  `AudioDeviceInfo(name, is_default)`. Filters: direction-capable, not the
  localized MME/DirectSound virtual mapper (`is_legacy_primary_mapper`), not
  WDM-KS (blocking API unsupported, both directions). Shows everything else
  (explicit user choice is sovereign — HDMI and loopback entries included).
  Dedupes host-API twins by exact name (keeping one entry) and merges
  MME-truncated prefixes (~31 chars) into the full WASAPI name. OS-default
  endpoint sorts first and is flagged. Headless / no PortAudio → `[]`,
  never raises.
- `resolve_device_by_name(name, *, output: bool) -> int | None` — exact
  case-insensitive match first, then substring both directions (covers MME
  truncation), ranked by the direction's host-API preference (output: WASAPI
  first; input: MME first), skipping mapper + WDM-KS. `None` when absent.

### 2. Resolver upgrade (player + capture)

In `_resolve_output_device` / `_resolve_input_device`: a concrete string
(≠ `"auto-headset"`) now goes through `resolve_device_by_name`. Found → its
index. Not found (unplugged) → WARN + fall through to the existing
auto-headset heuristic — playback/wake never brick on a missing device.

### 3. Routes (existing `settings_routes.py`, already mounted + tagged → CLI
coverage gate passes automatically)

- `GET /api/settings/audio-devices` → `{available, outputs[], inputs[],
  selected_output, selected_input}` (selected = raw config strings;
  `available=false` on headless).
- `PUT /api/settings/audio-devices` body
  `{output_device?, input_device?, persist=true}` (value = device name or
  `"auto-headset"` to reset to Automatic). Persists via new
  `config_writer.set_audio_device(kind, value)` (atomic TOML patch, AP-7),
  updates the in-memory `cfg.audio.*` (alerts + watchdog restarts read it),
  live-applies via the new `SpeechPipeline.set_audio_devices(...)`. Response
  mirrors tts-volume: `{ok, persisted, applied_live, restart_required}`.

### 4. Live-apply: `SpeechPipeline.set_audio_devices(*, input_device=…, output_device=…)`

- Output: update `self._output_device` + `AudioPlayer.set_device()` — takes
  effect on the next utterance (persistent stream is torn down).
- Input: update `self._input_device` + set `_wake_reload_event` — the wake
  session reopens the mic with the new device within a second; per-turn mic
  opens (PTT, dictation) read `self._input_device` on next open anyway.
- Headless / no pipeline → persisted only, `restart_required=true` in the
  response (same contract as tts-volume).

### 5. Frontend

- `useAudioDevices` hook (mirrors `useTtsVolume`): GET on mount, `select()`
  PUT, `refetch()` for the rescan button.
- `AudioDevicesGroup.tsx` in `views/settings/`: one card, two labeled
  dropdowns (output: Volume2 icon; input: Mic icon), first option
  "Automatic (recommended)", OS default marked, save-on-change with toast,
  rescan button. Mounted in `SettingsView.tsx` after `VolumeGroup`.
  Hidden-empty state: when `available=false`, show the "no audio devices
  found" caption instead of empty dropdowns.
- i18n: `settings_view.audio_devices.*` in `en.json` (source), `de.json`,
  `es.json`.

### Error handling

- Enumeration failure → `available=false`, UI shows caption, no crash.
- Unknown/unplugged selected name → resolvers fall back to auto with WARN;
  the Settings GET still shows the persisted name so the user sees their
  choice (and can reset to Automatic).
- PUT with neither field → 422 (Pydantic model validator).

### Testing

- `tests/unit/audio/test_devices.py` — enumeration + name resolution against
  a fake `sounddevice` table (host-API twins, MME truncation, mapper, WDM-KS,
  headless `sd=None`).
- Resolver fallback tests in the existing player/capture test modules
  (named device present → index; absent → auto fallback).
- Route tests following the existing tts-volume tests (GET shape, PUT
  persist + live-apply spy, headless degradation).
- Frontend: render test for `AudioDevicesGroup` (options + save call),
  mirroring `AppSettingsGroup.test.tsx`.

### Cross-platform notes (§3 CLAUDE.md)

- Enumeration is pure `sounddevice.query_devices()` — identical API on
  Windows (WASAPI/MME/DS), macOS (CoreAudio), Linux (ALSA/Pulse). Host-API
  preference maps only affect Windows (others get rank 99 → enumeration
  order, which is correct there).
- Headless `python:3.11-slim` (no PortAudio): both modules already guard the
  import (`sd = None`); `list_devices` returns `[]`, routes answer
  `available=false`, UI degrades to a caption. Nothing on the boot path
  (AP-26): enumeration happens only when the Settings view calls the route.
