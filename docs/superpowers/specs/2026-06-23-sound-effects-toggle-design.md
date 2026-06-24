# Sound-Effects Toggle — Design

**Date:** 2026-06-23
**Status:** Approved (design)
**Area:** Settings → Bar & Overlay → Behavior; speech pipeline earcons

## Problem

Jarvis plays four short synthesized tones ("earcons"): the wake-word "ding"
(also on push-to-talk), a descending hang-up tone, an ascending boot-ready
tone, and a "still listening" earcon that reuses the wake ding. A user who has
already disabled the spoken acknowledgment still hears the wake beep on every
"Hey Jarvis" and wants a single switch to silence all of these effect tones —
while keeping the spoken TTS voice fully audible.

## Goal

One global **Sound effects** toggle in the Settings → *Behavior* section that
mutes all four synthesized earcons together. Default **on** (no behavior change
for existing users). Applies live, no restart.

Non-goal (YAGNI): per-tone granularity; muting the spoken "Ja?" acknowledgment
(that is voice, not an effect tone, and is gated elsewhere).

## Design

Mirror the existing two Behavior toggles ("Show bar at all times",
"Mute music while dictating") layer for layer — no new architecture.

| Layer | Change | Mirrors |
|---|---|---|
| Config | new field `ui.sound_effects: bool = True` (`UIConfig`) | `startup_chime`, `bar_persistent` |
| Persistence | `config_writer.set_sound_effects()` → `[ui] sound_effects`, atomic | `set_bar_persistent` |
| REST | `GET` / `PUT /api/settings/sound-effects` (`BoolToggleBody`) | `/api/settings/mute-music` |
| Frontend hook | `useSoundEffects.ts` | `useMuteMusic.ts` |
| UI | third `ToggleRow` in `OverlayTaskbarGroup` Behavior block (icon `Volume2`) | the two existing rows |
| i18n | `taskbar_view.sound_effects.{title,description,enabled_toast,disabled_toast}` (en/de/es) | `taskbar_view.mute_music.*` |
| Backend gate | new `SpeechPipeline._play_earcon(pcm, *, sample_rate)` reads `self._config.ui.sound_effects`; the four play sites route through it | — |

### Live-apply mechanism

`DesktopApp` loads one `cfg` and passes the same object to both the FastAPI
server (`app.state.config`) and the `SpeechPipeline` (`self._config`). The PUT
route sets `cfg.ui.sound_effects` in memory **and** persists via
`config_writer`; the pipeline reads the field fresh on every earcon, so the
switch takes effect on the next tone with no restart — identical to the
"Mute music" toggle.

### Backend gate detail

The four sites:
- `_play_ack` (wake / push-to-talk `CHIME_PCM`) — gate only the chime, not the
  optional spoken ACK that follows it.
- hang-up `DISCONNECT_PCM`.
- `_play_ready_cue` (`READY_PCM`).
- completeness "still listening" earcon (`CHIME_PCM`, fire-and-forget via
  `asyncio.create_task`).

`_play_earcon` returns early (no playback) when `sound_effects` is false, read
defensively (`getattr(..., "sound_effects", True)`) so a missing field never
silences tones. The fire-and-forget site checks the same flag before scheduling
the task to avoid spawning a no-op coroutine.

## Testing

- Frontend: extend `OverlayTaskbarGroup.test.tsx` — the row renders, toggling
  issues `PUT /api/settings/sound-effects`, optimistic state flips.
- Backend: unit test that `_play_earcon` calls `play_pcm` when the flag is true
  and skips it when false (fake player).
- Config: `set_sound_effects` round-trips through `config_writer` (TOML patch).

## Rollout

Default `true` everywhere; no migration. Headless/VPS already no-ops on earcons
(no output device), so the flag is inert there.
