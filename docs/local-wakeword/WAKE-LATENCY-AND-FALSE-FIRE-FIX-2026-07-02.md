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
2. **Wake Whisper `cpu_threads` 1 → 2** (`build_wake_whisper`), matrix- and
   probe-backed: 1.7–2.8× faster per window (median 706 ms vs 2003 ms in the
   same-load matrix; 1718 ms vs 2960 ms under a deliberate 3-thread
   torch-OpenMP burn) with ZERO hangs in the 80-round torch-coexistence probe
   and identical recall. The AP-24/25 economics changed with fix 1: a rare
   wild hang now self-heals in bounded time instead of cascading. The thread
   count stays FIXED (never auto/all-cores), `num_workers=1`.
   Rejected by measurement: `tiny` (same recall, but 13.3 % bias
   hallucinations on real quiet-noise windows — fires "out of nowhere");
   bias OFF (recall collapses 100 % → 62.5 %, quiet recall 87.5 % → 37-50 %);
   `language=None` untested-by-default (de pin kept — live transcripts of
   "Hey Fable" are correct, and auto-detect is the documented EN-flip
   hallucination source on short windows).
3. **Strict full-phrase matcher** (`wake_phrase.py`, in WIP commit 8705f911):
   a phrase configured with a wake prefix ("Hey Fable") fires ONLY when a
   prefix token immediately precedes the core; any known prefix counts
   ("Hallo Fable" still wakes). Every core token must individually clear its
   fuzzy bar. REVERSES the 2026-06-29 "prefix optional" trade-off on explicit
   user instruction (2026-07-02). Single-word phrases are unchanged.

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

### Config matrix (window mode; 8 positives ×2 volumes, 85 negatives; same
machine load per batch — absolute times vary with the day's load, the
relative comparison inside a batch is the signal)

| config | cold | warm median | p95 | recall orig/−30 | FA bare* | FA quiet |
|---|---|---|---|---|---|---|
| base t1 de bias | 4463 | 2003 | 7454 | 100 % / 87.5 % | 70 % | 0 % |
| tiny t1 de bias | 2073 | 613 | 2516 | 100 % / 87.5 % | 93 % | **13.3 %** |
| tiny t1 de no-bias | 2315 | 1173 | 1987 | 62.5 % / 37.5 % | 20 % | 0 % |
| base t1 de no-bias | 2765 | 1307 | 4024 | 62.5 % / 50 % | 30 % | 0 % |
| **base t2 de bias** | **1701** | **706** | 2659 | **100 % / 87.5 %** | 70 % | **0 %** |
| base t4 de bias | 1585 | 616 | 5772 | 100 % / 87.5 % | 70 % | 0 % |

\* FA bare = false accepts on real bare-core-word windows, measured with the
OLD loose matcher — the strict matcher (fix 3) addresses this class; see the
after-run below.

Torch-coexistence probe (3 torch-OpenMP burner threads in-process, 80 rounds
t2 / 40 rounds t1): **0 hangs both**; t2 median 1718 ms vs t1 2960 ms.

### After all fixes (production build: base/cpu/int8/threads=2/bias/de +
strict matcher + cross-snapshot join)

| metric | baseline | after |
|---|---|---|
| stream first-try hits | 3/13 (+1 wedge-recover) | 7–8/13, **0 wedge-recovers** (2 runs) |
| stream word-end→trigger median / p95 | 2694 / 3910 ms | 1138–1389 / 2399–3047 ms |
| warm transcribe median (same-day load) | 1356–2003 ms | 706–949 ms |
| cold first transcribe | 2807–4463 ms | 1701–2446 ms |
| false accepts: bare core word | **71.7 %** | **6.7 %** |
| false accepts: ambient / quiet / silence | 1.7 / 0 / 0 % | 0 / 0 / 0 % |
| recall orig / −30 dBFS | 100 % / 69–87.5 % | 100 % / 87.5 % |
| stress (8 CPU burners): model teardowns | cascade (3 recovers/2 min live) | **0** across four 8 s timeouts |

Remaining honest gaps: (a) stream misses concentrate at rms ≈ 0.003 windows —
whisper-quiet speech at the silence-gate boundary, outside "normal speaking
volume"; run-to-run stream variance under fluctuating machine load is ±1–2
hits. (b) residual 6.7 % bare-core false accepts happen when the bias prompt
hallucinates the prefix onto a bare-name window; eliminating the bias costs
far more recall than it saves (matrix). (c) The KWS endgame (trained neural
model per phrase) remains the path to sub-200 ms wakes.

## Cross-platform

- **Windows**: all numbers above; 1106 speech/stt/audio unit tests green.
- **Linux (WSL Ubuntu, py3.12, faster-whisper 1.2.1)**: 64 wake unit tests
  green; window bench (reduced set): recall 100 % orig / 75 % @−30 (n=4,
  1 gated), false accepts bare 8.3 % (1/12), ambient/quiet/silence 0 %, warm
  median 1730 ms (CPU shared with the loaded Windows host — behaviour
  identical, absolute times not comparable).
- **macOS**: no hardware available. The touched files are pure Python +
  numpy + faster-whisper CPU wheels with no OS-specific branch; verified by
  the same tests running unmodified on both other OSes.
