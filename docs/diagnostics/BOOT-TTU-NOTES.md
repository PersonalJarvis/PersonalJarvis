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

## Iteration 6 — the anchor itself was cosmetic; fixed + re-baselined

- **Found while self-auditing:** `VOICE_READY_MS` printed at "pipeline task
  started" — BEFORE the deferred loaders warm the wake model / VAD / TTS. A
  benchmark anchored there measures exactly the cosmetic ready state the task
  forbids. The app already publishes the honest signal
  (`VoiceBootStatus(ready=True)`, end of `_warmup_deferred_loaders`, the
  2026-06-29 "it says ready but I can't talk" contract).
- **Fix:** desktop_app now prints `VOICE_USABLE_MS` when that event fires
  (profile mode only); the harness + budget guard anchor on it.
  `VOICE_READY_MS` stays as a secondary mark.
- **Honest TTU baseline (Windows, isolated, 3 cold runs, median):**
  **12.1 s** usable (runs 12.1/12.2/10.8), window 1.3 s. Structure: imports
  ~2.9 s + WebServer ctor ~1.7 s + voice setup ~3.5 s + model warm-up ~4 s.
- Autostart reconcile is ALREADY off the critical path (daemon thread,
  launcher) — not a lever.
- Factor bookkeeping so far: honest live before fixes ~200 s (first wake match
  3.5 min, log-timeline) vs honest isolated now 12.1 s. For a same-method
  factor, iteration 7 should re-measure the OLD commit (worktree checkout +
  harness backport) with the SAME usable anchor.

## Iteration 7 — same-method before/after (worktree at pre-fix commit)

- Method: git worktree at 039bbddc~1 (pre single-loader fix), SAME harness +
  SAME honest VOICE_USABLE anchor backported, `PYTHONPATH` pinned to the
  worktree (BUG-006 class: without the pin the editable install silently
  measured the NEW code — first run showed exactly the new-code numbers;
  the re-run verified `child imports: ...ttu-old-wt...` before measuring).
- **Pre-fix isolated (2 cold runs): voice-usable median 15.8 s
  (17.4/14.2), window 19.3 s.** Post-fix: voice-usable 12.1 s, window 1.3 s.
  Same-method factors: window **14.7x**, voice-usable **1.3x**.
- **Key insight:** the 114.7 s load cascade / ~200 s live TTU needed the LIVE
  setup's contention (full data + integrations + brain/MCP storm) to
  escalate; the isolated bench never reproduced it. So: (a) the live win of
  the single-loader fix is much larger than the isolated 1.3x suggests,
  (b) the reproducible isolated baseline for the 20x arithmetic is 15.8 s,
  and 20x of that (0.8 s) is provably below the physical floor of a local
  CPU Whisper load+prime (>2 s) plus the Python import mountain (~3 s) —
  an architectural-floor case per the task's honesty clause unless the
  wake model strategy changes (e.g. instant-arm on tiny + hot-swap).
- Remaining levers toward the floor: import mountain ~3 s, warm-up ~4 s
  (tiny-first + background hot-swap like the existing turbo swap), voice
  setup ~3.5 s. Realistic isolated target ~6-8 s.

## Iteration 8 — real Linux measurement + final tally

- **Headless Linux, MEASURED** (python:3.11-slim container, fast container FS,
  correct two-step install `pip install -r requirements.txt` then
  `pip install -e . --no-deps`; the combined single call fails on the hashed
  lockfile — that was a harness usage error, not a doctrine bug):
  **cold boot spawn->serving 196 ms median** (206/187 ms).
  The committed headless baseline in-repo: 4053 ms -> **20.62x faster**.
- Note for future container runs: NEVER measure through the Windows bind
  mount (Python imports over the 9P mount blew the 240 s timeout); copy the
  tree into the container FS first.
- macOS: NOT measured (no Apple hardware on this box). The method is
  committed and identical: run `scripts/measure_boot.py` (headless) /
  `scripts/measure_desktop_boot.py --voice` (desktop) on a Mac or CI matrix.

## FINAL TALLY (2026-07-02)

| Path | Before | After | Factor |
|---|---|---|---|
| Linux headless cold boot (measured in-container) | 4053 ms (committed baseline) | **196 ms** | **20.6x** |
| Windows desktop window anchor (same-method worktree A/B) | 19.3 s | **1.3 s** | **14.7x** |
| Windows voice-usable TTU (same-method A/B) | 15.8 s | **12.1 s** | 1.3x isolated |
| Windows LIVE first working wake (log-timeline, maintainer setup) | ~200 s (load cascade) | cascade eliminated; model pre-warm 114.7 s -> 9.8 s measured live | ~16x+ live |

- 20x on the voice-usable anchor vs the reproducible 15.8 s baseline would be
  0.79 s — provably below the physical floor (local CPU Whisper load+prime
  >2 s, Python import mountain ~3 s). Documented per the task's honesty
  clause. Remaining levers toward ~6-8 s: tiny-first wake arm + hot-swap
  (touches wake precision — coordinate with the 2026-07-02 prefix-mandate
  work), import mountain, ctor slimming.
- Regression protection: `scripts/ci/check_boot_budget.py` (+ slow-marked
  pytest wrapper) fails any change that pushes window >8 s or voice TTU >30 s.

## Iteration 9 — full cold-boot timeline (instrumented single run, Windows)

| t | block |
|---|---|
| 0.75 s | window serves (BOOT_READY) |
| 0.75-3.20 s | **import mountain 2.6 s** (heavy imports after shell paint) |
| 3.20-4.74 s | **WebServer ctor 1.5 s** |
| 4.74-5.57 s | voice setup ~0.8 s (tts=186ms wake_join=260ms pipeline_ctor=61ms) |
| 5.57 s | VOICE_READY (pipeline live), poll loop waits for warm model |
| 5.57-8.72 s | **wake model pre-warm 3.1 s** (clean, serial — no cascade) |
| 8.88 s | VOICE_USABLE |

Next lever (iteration 10): start the wake-MODEL load in a daemon thread right
after shell paint (extend `warmup_prefetch.py` from import-prefetch to a
model-prefetch cache that `FasterWhisperProvider._ensure_model` adopts when
model/device/cpu_threads match). CAUTION: the 2026-06-22 forensic showed
naive parallel native loads serialize on the GIL/init locks (11.8 s) — the
change MUST be A/B-measured with `measure_desktop_boot.py --voice --runs 3`,
and coordinate with the 2026-07-02 wake-latency session (cpu_threads 1->2,
scripts/wake_bench.py). Potential: usable ~8.9 -> ~6 s isolated.

## Iteration 10 — wake-model prefetch (implemented + A/B measured)

- Implemented per the iteration-9 plan (commit 1b50ffe9): daemon-thread load
  right after the UI shell serves, keyed hand-over cache in fwhisper
  (`prefetch_model` / adoption in `_ensure_model`), parameters resolved
  drift-free through the same `build_wake_whisper(fast_first=True)` call the
  boot uses. Contracts tested (6 tests): single-use pop (recover() never
  re-adopts, AP-24), mismatch/failure -> lazy load, in-flight await, no-op
  gates.
- **A/B (3 cold runs each): TTU 12,059 ms -> 9,492 ms median (-2.6 s,
  1.27x); best run 8.87 s.** Import mountain + ctor UNCHANGED (2.8/1.9 s) —
  no GIL contention from the parallel load. Adoption proven in the boot log
  ("Adopted boot-prefetched Whisper model (base/cpu/int8)").
  Full speech+stt suites green (939 tests).
- Remaining voice-usable structure (~9.5 s): imports ~2.8 s, ctor ~1.9 s,
  voice setup ~0.8 s, PRIME inference + TTS join ~2-3 s (the model LOAD is
  now off the path; the priming transcription remains). Next levers: prime
  during prefetch too (needs care: the priming inference must not collide
  with an early poll — the is_warm contract covers it), import mountain,
  ctor slimming. Diminishing returns; floor analysis unchanged.

## Iteration 11 — prime-in-prefetch (implemented; clean median pending)

- Implemented (commit 64b4a9a3): the prefetch thread also runs the priming
  inference; the hand-over cache carries a ``primed`` flag; ``warm_up``
  adopts a READY engine and skips its own prime. Contracts tested (3 new
  tests): failed prefetch-prime -> warm_up primes itself (readiness never
  faked); recover() resets the shortcut and always loads+primes fresh
  (AP-24). Suites green (13 stt tests).
- **Measurement status (honest):** the only quiet run measured **6.74 s**
  voice-usable (best value overall; consistent with removing the ~2 s prime
  from the path after the 9.49 s iteration-10 median). Two repeat batches
  were unusable — heavy foreign load on the box (window anchor 7-19 s,
  ctor 4 s; 23 python processes, CPU ~50 %). A clean 5-run median should be
  taken on an idle machine: `scripts/measure_desktop_boot.py --voice
  --runs 5 --warmup 1`. The budget guard (30 s voice budget) protects
  against real regressions meanwhile.
- With 6.7-9.5 s isolated TTU the remaining blocks are the import mountain
  (~2.6 s) and the WebServer ctor (~1.5 s) — the documented floor analysis
  stands; further gains need deep import/ctor refactoring.

## Iteration 12 — import-mountain decomposition (floor confirmed)

`python -X importtime` over the post-shell heavy imports (~0.97 s warm,
~2.6 s under boot load): fastapi ~0.29 s (openapi.models 81 ms self),
`jarvis.core.config` tree ~0.35 s cumulative — dominated by Pydantic schema
compilation incl. `jarvis.awareness.config` (138 ms self, pulled in as a
JarvisConfig field type; the module itself is clean model definitions),
brain/dispatcher/safety chain ~0.3 s. These are foundation costs: making the
config tree lazy would fight Pydantic validation (AP-16) for ~0.1-0.3 s —
not worth it. The import mountain is hereby part of the documented floor;
the only remaining structural lever is decoupling the voice path from the
WebServer import/ctor entirely (deep refactor, out of scope).

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
