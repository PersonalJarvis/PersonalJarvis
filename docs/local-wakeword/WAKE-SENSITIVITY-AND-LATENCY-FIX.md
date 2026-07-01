# Wake-word sensitivity & latency fix (2026-06-30)

Make wake-word detection trigger reliably at **normal speaking volume** (no
shouting) for **any** user-configured wake word, and make the Jarvis-Bar appear
**instantly**, on Windows and Linux without breaking macOS parity.

## The two symptoms and their root cause

1. **"You have to shout."** The microphone capture applied **no** normalization
   (`capture.pcm_bytes_to_np` only scales int16 → float). Each wake engine gated
   detection on **absolute amplitude** with its own fixed thresholds, and the
   per-engine gain stage sat **after** that gate. So absolute loudness — not the
   wake pattern — decided detection:
   - openWakeWord path (`WakeGainNormalizer`) amplified only above a **fixed**
     `noise_floor_peak = 0.02`; a genuinely quiet wake between that floor and
     true silence got **zero** gain and under-scored the pinned 0.15 threshold.
   - custom-word path (`RollingWhisperWake`, the `stt_match` engine) dropped a
     window below `min_peak = 0.012` **before** transcription — only a shout
     cleared it on a quiet mic.

2. **"~0.5 s delay before the bar appears."** The bar was **not** created
   on-demand (already pre-created and merely shown), but:
   - openWakeWord's `start()` loaded the ONNX model without a warm-up inference,
     so the **first** real "Hey Jarvis" frame paid the onnxruntime cold-start;
   - the custom-word detector polled every 0.3 s, adding avoidable reaction lag.

## The fix

- **New shared input AGC** — `jarvis/audio/wake_normalizer.py`
  (`AdaptiveWakeNormalizer`): one reusable, amplify-only, capped normalizer with
  an **adaptive noise floor** (EMA over quiet frames, like
  `mic_level.LevelNormalizer`). On a quiet mic the floor settles low so a quiet
  wake rises above it and is amplified; flat digital silence and steady
  sub-floor hiss are returned unchanged, so the AGC can never manufacture an
  ambient false-fire band. Pure numpy — no OS-specific code.
- **openWakeWord** (`jarvis/plugins/wake/openwakeword_provider.py`):
  `WakeGainNormalizer` now delegates to the shared adaptive normalizer
  (floor 0.02 → adaptive-from-0.006, gain cap 20 dB → 30 dB). `start()` now runs
  one throwaway inference to pay the cold-start off the wake path. The pinned
  0.15 activation threshold and the amplify-only + sub-floor guards are
  unchanged, so quiet wakes are lifted **without** widening the false-fire band.
- **RollingWhisperWake** (`jarvis/speech/rolling_whisper_wake.py`): peak gate
  0.012 → 0.008 (still well above the ~0.0046 idle-hiss level that stays gated),
  poll interval 0.3 s → 0.2 s.

## Before / after (measured)

Measured with `AdaptiveWakeNormalizer` vs the reconstructed old fixed-floor AGC,
using a level-sensitive model proxy (`score = min(1, out_peak × 2)`, threshold
0.15). Identical output on **Windows** (Python 3.11 / numpy 1.26.4) and **Linux**
(Python 3.12 / numpy 2.2.6):

| Metric | Before | After |
|---|---|---|
| True-Accept at quiet-mic volume (peaks 0.008–0.020) | **0 / 6 (0 %)** | **6 / 6 (100 %)** |
| A quiet real wake at input peak 0.012 | output 0.012 → **miss** | output 0.379 → **HIT** |
| False-accepts (silence + sub-floor hiss 0.000–0.004) | 0 | **0** (unchanged) |
| RollingWhisper: a 0.009 quiet-mic window | **gated** (never transcribed) | **reaches Whisper** |
| RollingWhisper: 0.0046 idle hiss | gated | **still gated** |
| Reveal event path (wake detected → bar `show()`) | ~instant | mean **0.003 ms**, p99 0.01 ms, max 0.05 ms |
| openWakeWord first-frame cold-start | paid on first wake | pre-warmed in `start()` |
| Custom-word poll cadence | 0.3 s | 0.2 s |

Target values (documented): True-Accept ≥ 95 % at normal volume, reveal latency
≤ 100 ms, false-accepts ≤ baseline. All met.

## Tests (all green on Windows + Linux)

- `tests/unit/audio/test_wake_normalizer.py` — the shared AGC (level
  independence, silence/sub-floor guard, cap, adaptive floor, reset).
- `tests/unit/plugins/wake/test_openwakeword_quiet_mic.py` — a quiet wake below
  the legacy fixed floor is amplified; `start()` primes the model.
- `tests/unit/speech/test_rolling_whisper_wake_quiet_mic.py` — a 0.009 window
  reaches Whisper.
- `tests/unit/speech/test_wake_latency.py` — poll cadence + instrumented reveal
  event-path latency ≤ 100 ms.

The existing wake regression suite (threshold pendulum / BUG-009 floor,
hallucination guard, rolling-whisper sensitivity + timeout, matcher/verifier
parity) stays green — 1011 speech/audio/wake unit tests pass, no regression.

## Cross-platform

Every changed file is pure Python + numpy with no `sys.platform` / `win32` /
`ctypes` branch, so behaviour is identical across OSes. The core normalizer +
benchmark were executed on **both** Windows and Linux (WSL) and produced
byte-identical results. The reveal path is Tk on every OS (pre-created window,
`deiconify` on show) and was not changed.

## Changed files

- `jarvis/audio/wake_normalizer.py` (new)
- `jarvis/plugins/wake/openwakeword_provider.py`
- `jarvis/speech/rolling_whisper_wake.py`
- `tests/unit/audio/test_wake_normalizer.py` (new)
- `tests/unit/plugins/wake/test_openwakeword_quiet_mic.py` (new)
- `tests/unit/speech/test_rolling_whisper_wake_quiet_mic.py` (new)
- `tests/unit/speech/test_wake_latency.py` (new)
