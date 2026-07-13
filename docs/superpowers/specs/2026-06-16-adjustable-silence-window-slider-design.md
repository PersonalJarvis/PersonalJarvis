# Adjustable thinking-pause (silence window) slider

**Date:** 2026-06-16
**Status:** Approved design, pending implementation plan
**Author:** session (systematic-debugging → brainstorming)

## Problem

The voice endpoint silence window — how long Jarvis waits in silence before it
treats an utterance as finished and submits it — is fixed at 1500 ms
(`vad_silence_ms` default on `SpeechPipeline.__init__`, `jarvis/speech/pipeline.py`).
The value is not configurable: no config field reads it, and no UI exposes it.
The maintainer wants to tune this "think buffer" himself, per taste, between
0.5 s and 5 s, with 1.5 s as the default and a reset-to-default control. The
chosen value must *actually take effect* — setting 5 s must make Jarvis wait 5 s.

## Goals

- A slider in the desktop Settings view, range **0.5–5.0 s, step 0.1 s**,
  default **1.5 s**, with a live numeric readout and a reset-to-default button.
- The chosen value is **persisted** to `jarvis.toml` and **applied live** to the
  running voice pipeline — no app restart required (graceful fallback to
  "applies on next start" when the pipeline is headless/down).
- The value genuinely governs endpointing: a 5 s setting waits 5 s, including
  the safety cap (see "Growing the hard cap").
- Cross-platform by construction: the feature is web UI + backend config +
  in-process setter, with no OS-specific code.

## Non-goals

- No per-intent or adaptive window here (the delegation-composition patience in
  `_stt_probe_async` → `extend_silence_window` stays as-is and remains additive
  on top of the new base value).
- No change to the STT stability probe's force-cut floor logic (the 2026-06-16
  `_effective_silence_frames` floor fix stays; it automatically tracks the new
  base because it reads `_silence_frames`).
- No wake-word VAD change (`whisper_wake.py` has its own short 500 ms window).

## Realtime extension (2026-07-13)

The same setting also owns the turn boundary for native Realtime voice. The
provider-neutral `RealtimeSessionConfig.silence_duration_ms` receives
`SpeechConfig.vad_silence_ms` whenever a browser or desktop Realtime session is
opened, including every cross-family handshake fallback.

- OpenAI Realtime sends the value as
  `session.audio.input.turn_detection.silence_duration_ms` in `server_vad`
  mode. Jarvis retains `create_response=false`, so the provider commits the
  input only after the configured silence and Jarvis explicitly requests the
  response after the final transcript.
- Gemini Live sends the value as
  `realtime_input_config.automatic_activity_detection.silence_duration_ms`.
  Gemini therefore does not commit the automatic activity boundary or begin
  its automatic response during a shorter mid-thought pause.

The classic VAD still accepts an in-process live update. Jarvis configures the
Realtime VAD during session setup, so a newly selected value governs the next
Realtime connection; no application restart is required.

## Architecture

The feature is a single value threaded through a five-link chain. Two links
exist today (the pipeline constructor parameter and the VAD's frame loop); three
are new (config field, boot read, live setter). The REST route + frontend mirror
the existing settings pattern exactly (wake-word, overlay-style, keybinds).

### 1. Config field

Add to `SpeechConfig` (`jarvis/core/config.py`, the `[speech]` block, which
already has `ConfigDict(extra="allow")` so it is self-mod pre-validate safe,
AP-16):

```python
# Voice endpoint silence window: how long the VAD waits in silence before
# treating an utterance as finished. User-tunable "think buffer" (desktop
# Settings → Voice). Range-clamped 500–5000 ms; default 1500 ms ("1.5s rule").
vad_silence_ms: int = Field(default=1500, ge=500, le=5000)
```

`Field(ge=500, le=5000)` makes the model itself the validation authority — an
out-of-range value in a hand-edited `jarvis.toml` is rejected at load, and the
route can reuse the same bounds.

### 2. Boot read (the missing wiring)

`jarvis/ui/desktop_app.py` (~line 1569) constructs `SpeechPipeline(...)` without
passing `vad_silence_ms`, so the constructor default (1500) always wins. Pass it:

```python
pipeline = SpeechPipeline(
    ...
    vad_silence_ms=self.cfg.speech.vad_silence_ms,
    ...
)
```

The two other construction sites (`jarvis/speech/watchdog.py:125`,
`jarvis/speech/pipeline.py:6090`) are test/CLI harnesses; they keep the default
unless a follow-up wants them wired too (out of scope — desktop app is the live
runtime). Document this in the plan so the omission is deliberate, not missed.

### 3. Live setter chain (no restart)

**`SileroEndpointer.set_silence_window_ms(ms: int)`** (`jarvis/audio/vad.py`):

```python
def set_silence_window_ms(self, ms: int) -> None:
    """Live-update the base silence window AND the matching hard cap.

    The running ``utterances()`` loop reads ``_effective_silence_frames`` and
    ``_max_samples`` on every frame, so a change here takes effect on the next
    processed frame — no pipeline rebuild. ``_extra_silence_frames`` (delegation
    patience) stays additive on top of the new base. The max-utterance cap grows
    with the window so a long thinking pause is never beheaded by the safety net
    (maintainer choice 2026-06-16): cap = max(8 s, ceil(window_s) + 5 s).
    """
    ms = max(500, min(5000, int(ms)))
    self._silence_frames = max(1, ms // 32)
    cap_s = max(8, (ms + 999) // 1000 + 5)
    self._max_samples = cap_s * VAD_SAMPLE_RATE
```

Notes:
- Clamps defensively (the route already validates, but the VAD must not trust
  callers — a stray value cannot wedge endpointing).
- `cap_s` formula: `(ms + 999)//1000` is `ceil(ms/1000)`. Window 1500 →
  `max(8, 2+5)=8`; window 3000 → `max(8, 3+5)=8`; window 5000 →
  `max(8, 5+5)=10`. Small windows keep today's 8 s cap; large windows grow it,
  always leaving ≥5 s of speech budget on top of the pause.
- `_silence_frames` is the *base*; `_effective_silence_frames` =
  `_silence_frames + _extra_silence_frames` is unchanged and recomputes live.

**`SpeechPipeline.set_silence_window_ms(ms: int)`** (`jarvis/speech/pipeline.py`):
delegates to `self._vad.set_silence_window_ms(ms)`. Mirrors `set_wake_plan` /
`set_keybinds`. Best-effort/no-op-safe if `_vad` is missing (headless).

### 4. config_writer

**`set_silence_window_ms(ms: int, *, path: Path = DEFAULT_CONFIG_FILE)`**
(`jarvis/core/config_writer.py`): writes `[speech].vad_silence_ms` via the
existing tomlkit + `_WRITE_LOCK` + tempfile + BOM-safe machinery (AP-7), exactly
like `set_overlay_style`. Clamps to 500–5000 before writing.

### 5. REST API

New route file section `jarvis/ui/web/settings_routes.py` (same module, same
prefix `/api/settings`):

```
GET  /api/settings/silence-window
  → {"ms": <current>, "default": 1500, "min": 500, "max": 5000}

PUT  /api/settings/silence-window
  body: {"ms": <int>, "persist": true}
  → validate 500..5000 (400 on out-of-range),
    in-memory cfg.speech.vad_silence_ms update (best-effort),
    persist via config_writer.set_silence_window_ms (best-effort),
    live-apply via pipeline.set_silence_window_ms,
    return {ok, ms, default: 1500, persisted, applied_live, restart_required}
```

- GET reads the current value from `cfg.speech.vad_silence_ms`, falling back to
  the `SpeechConfig` default when cfg is absent.
- Live pipeline handle is `request.app.state.speech_pipeline` (the same handle
  the wake-word/keybind routes use). `restart_required = not applied_live`.
- **Reset** is `PUT {ms: 1500}` — no separate endpoint.

### 6. Frontend

A new "Voice" settings group in `SettingsView.tsx`, with a `SilenceWindowRow`
component (sibling pattern to `AppSettingsGroup`/`AutostartRow`):

- **Hook** `useSilenceWindow` (`jarvis/ui/web/frontend/src/hooks/`): GET on
  mount, `setMs(ms)` PUT, exposing `{ms, default, min, max, loading, error}`.
  Mirrors `useAutostart`.
- **Control**: a styled native `<input type="range" min={500} max={5000}
  step={100}>` — NO new dependency (there is no shadcn slider in the design
  system; a native range input is sufficient for one control, YAGNI vs. pulling
  in `@radix-ui/react-slider`). A live readout label renders `ms/1000` as e.g.
  "1.5 s" and updates immediately from local state on drag.
- **Commit timing**: the PUT fires on *commit* (pointer/key release —
  `onChange` updates local state during drag, a `onMouseUp`+`onKeyUp` or a
  ~250 ms debounce sends the PUT), so a 0.1 s-step drag does not storm the
  backend with requests.
- **Reset button**: "Reset to default (1.5 s)" → `setMs(1500)`.
- **Feedback**: success toast; if `restart_required` (headless), an honest
  caption "applies on next start".
- **i18n**: new keys under `settings_view.silence_window.*` in en/de/es
  (title, description, unit, reset, applied_toast, restart_caption). English is
  the source; de/es are translations (Output Language Policy: i18n key + English
  source, never German source).

## Data flow

```
User drags slider ──(release)──> useSilenceWindow.setMs(2500)
  └─> PUT /api/settings/silence-window {ms:2500}
        ├─ validate 500..5000
        ├─ cfg.speech.vad_silence_ms = 2500            (in-memory, best-effort)
        ├─ config_writer.set_silence_window_ms(2500)   (jarvis.toml [speech])
        └─ pipeline.set_silence_window_ms(2500)
              └─ vad.set_silence_window_ms(2500)
                    ├─ _silence_frames = 78            (2500//32)
                    └─ _max_samples = 8 * 16000        (cap stays 8s for 2.5s)
        → next utterances() frame uses the new window  (no restart)

Boot ──> load_config().speech.vad_silence_ms ──> SpeechPipeline(vad_silence_ms=…)
       └─> SileroEndpointer(silence_ms=…)            (persisted value honoured)
```

## Error handling

- Out-of-range PUT → HTTP 400 (route guard), live state untouched.
- Read-only / locked `jarvis.toml` → persist fails, logged warning, live-apply
  still succeeds (matches every other settings route; `persisted=false`).
- Headless / no live pipeline → `applied_live=false`, `restart_required=true`,
  value still persisted; takes effect next boot.
- VAD setter clamps defensively so no value can wedge endpointing.

## Testing

TDD throughout (RED → GREEN):

- **VAD** (`tests/unit/audio/test_vad_turn_taking.py`):
  `set_silence_window_ms` updates `_silence_frames` and `_max_samples` per the
  formula; a mid-stream change to a longer window defers an endpoint that the
  old window would have fired (and a shorter window fires sooner); the growing
  cap is asserted at window=5000 → 10 s.
- **Config** (`tests/unit/.../test_config*.py`): `SpeechConfig` accepts
  `vad_silence_ms`; out-of-range raises `ValidationError`; default is 1500.
- **config_writer**: round-trip `set_silence_window_ms` writes `[speech]
  .vad_silence_ms`, clamps, BOM-safe.
- **Route** (`tests/unit/ui/...` or the settings-route test module): GET returns
  current+bounds; PUT applies + reports `applied_live`; out-of-range → 400;
  reset (ms=1500) round-trips.
- **Frontend** (vitest): `SilenceWindowRow` renders the slider at the fetched
  value, fires one PUT on commit (not per tick), reset sends 1500.

## Verification (chrome-checkup-loop)

After implementation, run the `chrome-checkup-loop` skill against the real
desktop app: open Settings, drag the slider, confirm no console errors / no
failed requests / the value persists across reload / the layout is clean. Then a
real voice drive with a deliberately long pause, proving via
`data/jarvis_desktop.log` that the endpoint fires at the configured
`silence_ms` (e.g. set 3 s → `reason=silence silence_ms≈2976`).

## Files touched

- `jarvis/core/config.py` — `SpeechConfig.vad_silence_ms`
- `jarvis/core/config_writer.py` — `set_silence_window_ms`
- `jarvis/audio/vad.py` — `SileroEndpointer.set_silence_window_ms`
- `jarvis/speech/pipeline.py` — `SpeechPipeline.set_silence_window_ms`
- `jarvis/ui/desktop_app.py` — pass `vad_silence_ms` at construction
- `jarvis/ui/web/settings_routes.py` — GET/PUT `/silence-window`
- `jarvis/ui/web/frontend/src/hooks/useSilenceWindow.ts` — new hook
- `jarvis/ui/web/frontend/src/views/settings/` — `SilenceWindowRow` + wire into
  `SettingsView`
- frontend i18n locale files — `settings_view.silence_window.*` (en/de/es)
- tests as listed above
