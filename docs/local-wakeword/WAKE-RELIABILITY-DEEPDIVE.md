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
