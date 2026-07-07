# Wake-word reliability deep-dive (2026-06-30)

Goal: a custom wake word must trigger **first try, instantly**, like "Hey Google"
on a Pixel — on Windows, Linux and macOS. Reported symptom: a clear, loud
"Hey Nico" needed 2-3 repetitions before anything happened.

## Root cause — from the live logs, not a guess

`data/jarvis_desktop.log` (the running app) is unambiguous:

- **It is not volume.** During the failed attempts the mic RMS was healthy
  (-10 to -22 dBFS). The earlier "must shout" volume fix was solving the wrong
  thing for this setup.
- **The wake word IS matched when transcribed correctly** — `rolling-whisper:
  WAKE matched 'nico' in 'Hey Nico'`. The matcher is not the problem.
- **The transcription model wedges repeatedly.** Dozens of `5 consecutive
  transcribe failures -> rebuilding the wedged wake model` lines across the day.
  Each wedge leaves the wake **totally deaf for tens of seconds**. Say "Hey Nico"
  during a wedge and nothing happens; a few seconds later it is alive again —
  exactly "say it 2-3 times".
- **The wake runs a weak `base` model on the CPU** while the RTX 5070 Ti sits
  idle (the post-wake utterance STT is cloud/Groq, so the GPU is free). Under app
  CPU/GIL contention `base` both mis-transcribes ("Mach weiter" -> "Nach <!-- i18n-allow -->
  weiter"/"Wetter. Its fun.") and occasionally hangs past the 8 s timeout. <!-- i18n-allow -->

The deeper truth: **wake detection via full speech-to-text is the wrong
architecture.** "Hey Google"/Alexa/Siri never transcribe to wake — they run a
tiny, always-on neural keyword-spotting model trained for the phrase. Weeks of
tuning the base/cpu transcription path (forensic comments 2026-06-22 … 06-29) did
not make it reliable, which is the signal to change the approach, not tune again.

## What changed now (three fixes, all shipped + tested)

1. **Use the GPU for the custom-phrase wake** — `jarvis/plugins/stt/__init__.py`.
   On a CUDA box a custom phrase now transcribes on `large-v3-turbo/cuda` **with**
   the phrase bias instead of `base/cpu`. ~150 ms per window, so it **never blows
   the transcribe timeout** — this is what eliminates the wedge — and it hears the
   proper noun accurately. The bias is kept (turbo *without* bias mangles a short
   name); the old "bias hallucinates on silence" worry does not apply on the
   rolling path, which gates on RMS/peak and never feeds the model a silent
   window. Reversible via the new `[stt].wake_high_accuracy` (default `true`).
2. **Self-heal a wedge fast** — `jarvis/speech/rolling_whisper_wake.py`. Rebuild
   the wedged model after **2** consecutive failures instead of 5, so on any
   CPU-only host the deaf window is as short as possible.
3. **Sound-folding matcher** — `jarvis/speech/wake_constants.py` +
   `wake_phrase.py`. Fold sound-equivalent spellings (`c`/`k`, `ck`, `ph`/`f`,
   `y`/`i`, doubled letters) before the fuzzy compare, so `Nico`/`Niko`/`Nicko`/
   `Nikko` all match without loosening the ratio (clearly different words still
   do not match — no extra false wakes). Pure Python; **byte-identical on Windows
   and Linux** (verified).

## Effect

- On the maintainer's machine (GPU + cloud STT): the wedge disappears (turbo is
  ~150 ms, never times out) and the name is transcribed accurately -> first-try.
- Cross-platform CPU hosts: faster wedge self-heal + sound-folding recover most
  near-misses; still weaker than GPU (see endgame).

## Tests (green on Windows; pure-Python paths verified on Linux too)

- `tests/unit/plugins/stt/test_wake_whisper_build.py` — custom phrase upgrades to
  GPU turbo+bias on CUDA; reversible via `wake_high_accuracy`; fast-first + no-CUDA
  stay base/cpu.
- `tests/unit/speech/test_rolling_whisper_wake_wedge.py` — self-heal within 2
  failures.
- `tests/unit/speech/test_wake_phrase_phonetic.py` — sound-equivalent spellings
  match; clearly different words do not.

1097 speech/audio/wake/stt unit tests pass, no regression.

## Endgame — the definitive cross-platform "Hey Google" fix (next stage)

The GPU fix makes this excellent on a CUDA box, but a laptop/VPS on CPU still
leans on transcription. The real answer for **any word, any OS, instant, on CPU**
is a trained neural keyword-spotting model — the `custom_onnx` engine already
exists in this repo; the gap is generating the `.onnx`. Scope:

1. When the user sets a custom wake word, generate synthetic TTS clips of the
   phrase (many voices/accents), mix with noise/negatives.
2. Train a small openWakeWord-style model (minutes on GPU; runs on CPU at
   inference, ~few ms/frame — never wedges, fires instantly).
3. Load it via the existing `custom_onnx` path; fall back to the transcription
   path while training runs.

This removes speech-to-text from the wake path entirely — the architecture every
production wake word uses.

## How to make it live

The code is committed but the running app must **restart** to pick it up
(Settings → restart, or `POST /api/settings/restart-app`). After restart, the
GPU turbo model hot-swaps in a few seconds after boot; then say "Hey Nico" once.
If a custom voice ever over-triggers, set `[stt].wake_high_accuracy = false`.

## The transcript-content trap (2026-07-02, BUG-037 / AP-27)

The `stt_match` wake primes Whisper with `initial_prompt=<phrase>` for recall.
Consequence: the primed model **invents** the phrase on silence (ghost) and the
unprimed model **garbles** it on speech (`Mythos`→`Mütos`, `Fable`→`Farbe`). So <!-- i18n-allow: forensic quotes of the German STT-garble tokens under test -->
any ghost fix that gates on *transcript content* — most tempting, "make the
unbiased confirm pass also say the word" — rejects every genuine wake. "Fires
on silence" and "never fires" are the same bug from opposite ends.

**Rule:** gate silence on raw audio **energy** (word-agnostic match-site RMS
gate, `RollingWhisperWake._match_min_rms`), never on transcript content. Keep
the bias-echo confirm permissive (fail-open) and skip it on a clearly-loud
window for latency (`_ECHO_CONFIRM_SKIP_RMS`; a loud wake fires ~0.6 s vs
~1.1 s). The recall guard `test_loud_wake_fires_even_when_unbiased_pass_garbles_the_hard_word`
must stay green. The clean endgame remains a trained neural KWS model
(`custom_onnx`) that does not transcribe at all.

## The GPU turbo returns, probe-gated (2026-07-05, AP-25 revisited)

The 2026-06-30 GPU fix above was later disabled globally
(`wake_high_accuracy=False`) after the AP-25 Blackwell hang — which silently
put EVERY host back on the weak `base/cpu` model and re-created the
machine-/word-dependent wake quality this document opened with (live log
2026-07-05: heartbeat with 288 transcriptions and 0 matches in 26 min for
"Hey Nova"; accepted wakes read `'Hey Hey Nova'` — the user literally
repeating the phrase).

Re-measured on the SAME Blackwell GPU (RTX 5070 Ti / sm_120, ctranslate2
4.7.1 + torch 2.11-cu128), all in one session:

- Standalone turbo/cuda: load 8.4 s, inference 0.58 s cold / 0.11–0.12 s warm.
- Torch-coexistence (3 in-process torch-OpenMP burner threads — the live app
  constellation): **40/40 inferences, zero hangs**, p50 117 ms, max 336 ms.
- `wake_bench --mode window`: warm median 121 ms / p95 860 ms (turbo/cuda)
  vs 767 ms / p95 2853 ms (base/cpu t2).
- `wake_bench --mode stream` (13 real captured wake streams, end-to-end
  through `RollingWhisperWake.detect()`): **11/13 first-try hits,
  word-end→trigger median 225 ms / p95 462 ms** (turbo/cuda) vs 8/13 and
  1097 ms / p95 4021 ms (base/cpu t2). Zero wedge-recovers on both.

So the hang was **constellation-specific** (the then-current runtime combo),
not a property of the architecture. The blanket default-off is replaced by a
**capability probe** (AP-21): `build_wake_whisper`'s CUDA branches now also
require `_wake_gpu_inference_verified()` — one real turbo/cuda transcribe in
a killable subprocess (BUG-036: an in-process hang could never be cancelled),
verdict = the `WAKE_GPU_PROBE_OK` stdout marker (never the exit code — the
CUDA teardown can abort AFTER correct work, observed exit 127/0xC0000409),
cached in `data/wake_gpu_probe.json` keyed by the ctranslate2 version so a
runtime upgrade re-probes exactly once. The probe runs only on
non-`fast_first` builds — i.e. inside the background hot-swap, never on the
boot/hear-ready path (AP-26) and never on the live settings switch
(`set_wake_plan` builds `fast_first`). Two hard-won details:

1. **Import-order trap:** on hosts without a system CUDA toolkit,
   `cublas64_12.dll` ships only inside `torch\lib` and becomes loadable when
   torch's import registers its DLL directory. The live app always has torch
   (Silero VAD) loaded long before the hot-swap; the probe mirrors that by
   importing torch first (best effort). Probing without torch would fail with
   "Library cublas64_12.dll is not found" on a host where the live upgrade
   works fine — and would test the wrong coexistence constellation anyway.
2. **Live backstop:** the hot-swap keeps the proven base/cpu provider
   attached to the turbo instance (`_wake_gpu_fallback`). If the GPU model
   ever wedges live, `_recover_wedged` swaps straight back (the fallback kept
   its loaded model — instant) and persists the bad verdict via
   `mark_wake_gpu_bad()`, so no later build re-runs the hanging inference.
   Rebuilding the same CUDA model — the old behaviour — was the AP-25 deaf
   cycle.

`wake_high_accuracy` defaults to True again; False is the hard opt-out.
CPU-only hosts, headless Linux and macOS are byte-identical to before (the
probe is short-circuited behind `cuda_available`). Guards:
`tests/unit/plugins/stt/test_wake_gpu_probe.py`,
`tests/unit/plugins/stt/test_wake_whisper_build.py`,
`tests/unit/speech/test_rolling_whisper_wake_gpu_backstop.py`.
Bench: `scripts/wake_bench.py --device cuda`.

## The any-word engine lands: vosk_kws (2026-07-06)

The GPU-probe work above fixes the NVIDIA-host tier; the `vosk_kws` engine
(design + spike: `docs/superpowers/specs/2026-07-05-universal-wake-kws-design.md`)
is the **one-identical-system-everywhere** answer: a per-language Vosk model
(Apache-2.0, torch-free, official CPU wheels win/mac/linux x86+ARM) streams
audio through a grammar-constrained recognizer that only knows the configured
phrase + `[unk]`. Any freely chosen word is pure configuration — no per-user
training, no cloud, no GPU. Detection is two-stage and AP-27-safe: the
grammar PARTIAL fires during the phrase; a candidate then waits 0.6 s so the
phrase tail lands in the ring (E2E-measured: confirming mid-word truncates
the utterance and halves recall), passes a word-agnostic RMS gate, and ONE
free-decode pass must merely be SOUND-CLOSE to the phrase (never spell it —
the free ear hears "hey room"/"herum" for a genuine German "Hey Alex";
ambient "vielen dank" is nowhere near). <!-- i18n-allow: forensic quotes of German utterances under test -->

End-to-end through the real `VoskKwsProvider.detect()` on real captured
streams (neutral judge-approved positives): **Hey Alex 21/24 (88 %),
Hey Luca 8/8 (100 %), false accepts 0/120** on judged ambient-speech streams
— and **bit-identical numbers on Linux (WSL Ubuntu, py3.12)**; vosk also
installs + imports clean on headless `python:3.11-slim`. Live on the
maintainer's box the ready log arms `WAKE=['nova']` and the confirm visibly
suppresses continuous room speech with zero false fires. Boot: model loads in
0.7-6 s inside `_start_wake`; boot-budget gate green (voice TTU 8.9 s ≤ 20 s).

Engine chain: custom_onnx (matching file) → pretrained OWW → **vosk_kws
(any phrase, default)** → stt_match (fallback) → none/hotkey. Honest limits:
(a) the ~45 MB per-language model is fetched once at setup (not bundled;
missing model falls through to stt_match with a clear message), (b) fantasy
words outside the model lexicon ride the free-decode fuzzy path — best
effort, (c) the definitive sub-200 ms class remains a trained neural KWS.
Guards: `tests/unit/plugins/wake/test_vosk_kws_provider.py`,
`tests/unit/speech/test_wake_plan_vosk.py` (chain + live-arming regression).
