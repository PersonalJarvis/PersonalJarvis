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
  **MEASURED (boot 2026-07-02 08:59, Windows):** pre-warm 114,718 ms ->
  **9,828 ms** (11.7x); zero transcribe timeouts / TranscribeBusy / self-heal
  rebuilds during boot; poll starts cleanly after 10.1 s; overlay revealed via
  "voice-ready" (was 60 s timeout-fallback). Rough wake-ready TTU ~50 s
  (was ~200 s).

## Next bottlenecks (iteration 3+)

1. ~35-40 s elapse between process start and "Pipeline bereit" (quoted log marker) (08:58:5x -> <!-- i18n-allow: quoted log line -->
   08:59:33) — profile the bootstrap chain BEFORE the speech pipeline
   (imports, registries, TTS init, uvicorn bootstrap...). This is now the
   dominant TTU block.
2. Pre-warm 9.8 s vs ~4 s isolated — check what still contends (gate opens
   correctly now, but something still races the load).
3. Build the reproducible TTU benchmark (cold start, several runs, median) +
   commit; then per-OS baselines (Linux via headless python:3.11-slim, macOS
   via CI or documented method).
4. Startup-budget regression guard test + "how to add a feature
   boot-neutrally" doc note.

## Iteration 3 findings (bench infra + pre-pipeline timeline)

- **Benchmark infra ALREADY EXISTS:** `scripts/measure_boot.py` (headless) and
  `scripts/measure_desktop_boot.py` (desktop backend path, no GUI/mic) with
  full isolation (.boot-bench/, per-run data wipe, seeded vault, free port via
  `_bench_env`/`_free_port`), warm-up + median over N runs, JSON baseline
  files. Anchors printed under `JARVIS_BOOT_PROFILE=1`: `BOOT_READY_MS`
  (window can appear), `VOICE_READY_MS` (wake loop armed, same clock), plus
  `[BOOT_PROFILE] db_*` marks (pre_webserver, webserver_ctor, server_start).
  The TTU benchmark should EXTEND this (add the VOICE_READY_MS anchor + an
  end-to-end typed-turn round-trip), not reinvent it.
- **Pre-pipeline timeline of the 08:59 boot (no profile flag, from logs):**
  process spawn ~08:58:47-50 -> first log 08:59:05 (import/interpreter lead,
  ~15 s incl. relauncher wait) -> autostart reconcile 08:59:11-14 (~4-6 s,
  network + scheduled-task work, questionable on the critical path) ->
  WebServer ctor ~08:59:15-28 (~13 s: skills bootstrap ~3 s, DocRegistry
  ~3 s, Board ready ~6.5 s) -> pipeline built 08:59:33. The voice path waits
  for the FULL WebServer ctor because `_start_speech_and_orb` needs
  `server.bus` — candidates: slim the ctor (defer Board/registry setup into
  `server.start()`), move autostart reconcile behind voice-ready, examine the
  15 s import lead with `db_` marks.
- Next iteration: run `measure_desktop_boot.py --runs 3` for a REAL baseline
  with db_ marks, then attack the largest measured block.

## Iteration 4 — TTU benchmark mode + first honest baselines (Windows)

- `measure_desktop_boot.py --voice` (commit ce8a2bd9): boots WITH the voice
  stack, anchors on VOICE_READY_MS, writes desktop-ttu-{baseline,latest}.json.
- **Isolated window anchor (voice off): 1.18 s median** (3 runs) — serve-first
  works; "window appears" was never the problem.
- **Isolated TTU (voice ready, 3 cold runs): 8.0 s median**
  (runs 8.8/7.7/8.0 s; phases: pre_webserver 3.6 s = import lead,
  webserver_ctor 1.9 s). Baseline frozen in desktop-ttu-baseline.json.
- **Gap analysis:** live maintainer boot after the single-loader fix is ~50 s
  wake-ready vs 8.0 s isolated. The isolated bench has fresh data dirs and no
  integrations; the live delta (~42 s) therefore lives in DATA + INTEGRATIONS
  (wiki 1005 rows, mission/board DBs, Telegram poller, MCPs, autostart
  reconcile ~5 s, log sink history) — NOT in the voice code path anymore.
- Honest factor bookkeeping: live-before-fixes ~200 s (log-timeline) ->
  live-after ~50 s (4x live); isolated code path now 8.0 s. The 20x claim must
  come from the SAME measurement method — next iterations must shrink the
  live delta and re-measure live.

## Next (iteration 5+)

1. ~~Regression guard~~ DONE (iteration 5): `scripts/ci/check_boot_budget.py`
   (one isolated cold boot; window budget 8 s, voice-TTU budget 30 s, env-
   overridable; ~4x the measured medians so variance never flakes but every
   seen regression class blows through) + pytest wrapper
   `tests/integration/test_boot_budget.py` (slow-marked, self-skips when the
   host cannot measure). Headless boxes check the window anchor and skip the
   voice anchor honestly.
2. Attack the live delta: profile a LIVE boot (env flag on the relauncher or
   log-timeline) and defer the data/integration blocks (autostart reconcile,
   board init, telegram/MCP starts) behind voice-ready.
3. Cross-OS: headless python:3.11-slim (Docker/WSL) for Linux, document a
   macOS method (CI matrix) — measure, not assume.

## How to add a feature WITHOUT slowing boot (doctrine)

- Nothing new runs before VOICE_READY. New subsystems hook into
  `_heavy_backend_bg` (desktop_app), a deferred registry scan, or a
  fire-and-forget task created AFTER voice-ready — never into the module
  import path, the WebServer ctor, or `_start_speech_and_orb`.
- Heavy imports: lazy-import inside the function (the repo pattern), or add a
  prefetch thread like `warmup_prefetch.py` if the import must be warm early.
- Verify before merging: `scripts/measure_desktop_boot.py --voice --runs 3`
  before/after; the budget guard (`scripts/ci/check_boot_budget.py`) is the
  enforcement backstop.

## Lessons (do not repeat)

- The unit-test suite fast-forwards SMALL asyncio sleeps (a sleep accelerator;
  600 poll iterations in 0.01 s real). Wall-clock-threshold logic cannot be
  tested with real waits — make thresholds injectable and use 0.0 in tests
  (see test_rolling_whisper_wake_boot_warm.py). Two hours of "phantom hangs"
  were exactly this.
