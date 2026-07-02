# Wake stt_match path: latency, reliability, false activations (2026-07-02)

Task: the custom-phrase wake ("Hey Fable", engine `stt_match`) reacted slowly,
missed normal-volume utterances inconsistently, and — user report mid-task —
also ACTIVATED without the wake phrase being spoken. All findings below are
measured with `scripts/wake_bench.py` against real captured audio
(`data/wake_debug/`, 1.8 s / 16 kHz production windows; positives = windows
whose capture transcript contained prefix+core, e.g. "Hey Nico"; negative
classes: bare core word mid-sentence, ambient speech, quiet noise, silence).

## Root causes (all live-log/bench confirmed)

- **RC1 — self-perpetuating wedge cascade.** One transcription slower than
  the 8 s poll cap counted as TWO failures (timeout + the same still-running
  call's `TranscribeBusy`) → `recover()` dropped a healthy model → the lazy
  cold rebuild raced the next 8 s timeout under load → repeat. Live log
  2026-07-02 08:21–08:26: three cycles in 2 minutes, wake deaf while the mic
  showed speech. 68 wedge lines in ~1.5 days.
- **RC2 — cold model on the poll path at boot.** Fixed by the parallel
  Boot-TTU session (single-loader warm-up + `is_warm` gate, commit 039bbddc).
- **RC3 — transcription cadence.** base/cpu/int8/threads=1: warm median
  1.36 s per 1.8 s window (p95 5.3 s under load) → word-end→trigger 1–3 s.
- **RC4 — quiet speech.** Recall falls 100 % → 92 % (−20 dBFS) → 69 %
  (−30 dBFS) → 0 % (−40 dBFS, all windows below the rms/peak gates).
- **RC5 — false activations.**
  - (a) Core-only matching (deliberate 2026-06-29 trade-off, now REVERSED by
    explicit user instruction): the bare core word mid-sentence activated —
    **71.7 % false-accept on real bare-core windows** ("1 Fable Pro",
    "Nico, mein Barsch.").
  - (b) Phrase-bias hallucination on noise: quantified below (bias on/off ×
    quiet-noise negatives).

## Fixes

1. `fix(wake) 9a4da695` — steady-state wedge accounting: `TranscribeBusy` is
   not a second failure; a >20 s continuous busy streak (true hang, BUG-036)
   or two DISTINCT timeouts still recover; after any mid-session `recover()`
   the poll loop re-warms the rebuilt model off the transcribe timeout.
   Guards: `tests/unit/speech/test_rolling_whisper_wake_steady_state.py`.
2. (pending — measured config matrix result)
3. (pending — strict full-phrase matcher)

## Measurements

### Baseline (before fixes; machine under real parallel load)

| metric | value |
|---|---|
| cold first transcribe | 2807 ms |
| warm transcribe median / p95 | 1356 ms / 5318 ms |
| stream first-try hits | 3/13 (1 wedge-recover mid-run) |
| stream latency (hits) median / max | 2694 ms / 4045 ms |
| recall orig / −20 / −30 / −40 dBFS | 100 % / 92.3 % / 69.2 % / 0 % |
| false accepts: bare core / ambient / quiet / silence | 71.7 % / 1.7 % / 0 % / 0 % |

### Config matrix (window mode)

(pending)

### After fixes

(pending)

## Cross-platform

(pending — Windows measured; Linux via WSL Ubuntu; macOS by code-path
neutrality: the touched files are pure Python + numpy + faster-whisper CPU.)
