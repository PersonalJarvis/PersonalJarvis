# Boot TTU (Time-to-Usable) — working notes

Loop task 2026-07-02: cut boot TTU by >= 20x, measured, on Windows/macOS/Linux,
with a regression guard. TTU = process start until the user can REALLY interact
(wake word armed AND firing, speech -> STT -> brain -> answer, typed turn works,
TTS ready) — all end-to-end over the same EventBus. "Window visible" does not count.

## Baseline measurement #1 — Windows, live log, boot of 2026-07-02 08:20 (commit 157b9f57)

Wake engine: stt_match (phrase "Hey Fable", rolling local Whisper base/cpu).

| t (mm:ss from first log line 08:20:18) | milestone |
|---|---|
| 00:00 | first bootstrap log lines (registries created, scans deferred) |
| 00:06 | speech pipeline built ("Pipeline bereit", voice build timings: tts=25ms wake_join=459ms) |
| 00:06 | wake loop started (whisper wake) — but local whisper NOT loaded yet |
| 00:58 | "Heavy backend: wake-model gate timed out (12 s) — starting the backend anyway" |
| 00:58 | persistent overlay (JarvisBar) revealed — ~1 min after start |
| 01:03 | rolling-whisper wedged during warm-up -> self-heal rebuild |
| 02:00 | "Wake-model pre-warm done in 114718 ms" (!!) |
| 03:23 | first real wake match ("Hey Fable" recognised) |

=> TTU(wake path) ~ 200+ s this boot. Even overlay-visible took ~60 s.

## Regression context (why "5x slower since today")

Until 2026-07-02 the wake engine was custom_onnx (small ONNX model, hear-ready in
~3 s). The stale-model fix (157b9f57, correct) resolved the changed phrase
"Hey Fable" to engine=stt_match — which puts the local faster-whisper base/cpu
load + pre-warm on the boot path: 114.7 s pre-warm + a wedge/self-heal cycle.
So the regression is NOT the fix itself but the stt_match boot cost that the
custom_onnx engine had hidden.

## Suspected critical-path blockers (to verify with code + measurements)

1. Wake-Whisper (faster-whisper base/cpu) load + pre-warm: 114.7 s logged.
   Why so long? Model load alone should be ~2-5 s; suspects: CPU contention
   with the parallel boot (uvicorn, wiki, registries), ctranslate2 thread pool
   vs the rest (AP-25), warm-up inference serialised behind the GIL, or an
   in-flight lock collision (the 08:21:21 wedge suggests the poll loop already
   ran while pre-warm was still using the model — AP-24 territory).
2. "Heavy backend: wake-model gate" — backend start WAITS up to 12 s on the
   wake model; overlay reveal is tied to boot completion (timeout-fallback).
3. Unknown import/spawn cost before the first log line (process start ->
   08:20:18) — must instrument.

## Findings log (append per loop iteration)

- **F1 (verified in code, iteration 1):** the wake POLL loop starts at
  "Wake-Loop gestartet" (t+6s) while `_warmup_deferred_loaders` is still
  pre-warming the SAME stt model object (`pipeline.py::_warmup_deferred_loaders`,
  "PRIORITY: pre-warm the WAKE model FIRST"). The poll's own transcribe attempts
  collide with the warm-up ("a transcription is already in flight on this
  model", 08:21:21), the 8 s transcribe cap + 2-fail self-heal then REBUILDS the
  model mid-warm-up — throwing away the half-loaded state. AP-24 territory, on
  the boot path.
- **F2 (verified in code, iteration 1):** `_heavy_backend_bg` waits on
  `_wake_model_loaded` (12 s cap); the signal task `_signal_wake_model_loaded`
  polls `base_stt._model is not None` for max 20 s (desktop_app.py:2444). In the
  08:20 boot the gate TIMED OUT -> the heavy backend storm (server.start init
  chain + brain build + MCP) started mid-model-load, adding CPU contention on
  top of F1. Combined effect: 114.7 s pre-warm for a model measured at ~3-4 s
  isolated (per existing comments), TTU ~200 s.
- **F3:** overlay (JarvisBar) reveal waited for the boot-complete signal and
  fired via "timeout-fallback" at ~60 s.

## Fixes applied (append per loop iteration, with measured effect)

- **Fix for F1+F2 (iteration 2, commit pending):** single-loader wake warm-up.
  (a) `FasterWhisperProvider.is_warm` — True once `warm_up` completed (model
  constructed + primed); reset by `recover()`. (b) The rolling-whisper poll
  loop skips the transcribe phase (and self-heal fail counting) until
  `is_warm`; if nobody warms the model within `warm_wait_fallback_s` (20 s),
  the poll loop warms it once itself — exactly one loader either way.
  (c) `_wake_model_is_loaded` (heavy-backend gate) now prefers `is_warm`, so
  the backend CPU storm starts only after the priming inference.
  Expected effect: wake-model pre-warm back to ~4 s isolated (from 114.7 s),
  gate opens before its 12 s timeout, no self-heal rebuild during boot.
  MEASUREMENT PENDING (next boot).

## Lessons (do not repeat)

- The unit-test suite fast-forwards SMALL asyncio sleeps (a sleep accelerator;
  600 poll iterations in 0.01 s real). Wall-clock-threshold logic cannot be
  tested with real waits — make thresholds injectable and use 0.0 in tests
  (see test_rolling_whisper_wake_boot_warm.py). Two hours of "phantom hangs"
  were exactly this.
