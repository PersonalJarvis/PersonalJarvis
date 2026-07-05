# Universal wake word via phoneme keyword spotting (design)

Date: 2026-07-05. Status: approved by the maintainer (brainstorming session);
spike gate below decides go/no-go for the implementation.

## Problem

The custom wake word today rides on transcription (`stt_match`): a Whisper
model transcribes a rolling window and a matcher searches the text. That is
inherently machine- and word-dependent — recall varies with CPU speed, GPU
class and the word itself (see `docs/local-wakeword/WAKE-RELIABILITY-DEEPDIVE.md`).
The 2026-07-05 GPU-probe work fixed the NVIDIA-host tier, but hosts without a
usable GPU (macOS, most laptops, small Linux boxes) stay on the weak path.
The maintainer's goal: **one identical wake system on every machine
(Windows/macOS/Linux), any freely chosen word, no per-user training, no
cloud, no GPU requirement.**

## Approach (chosen in brainstorming: "Richtung 1")

A single tiny pretrained **open-vocabulary keyword-spotting model** (k2/sherpa-onnx
zipformer KWS class: ~3.3 M params, ~14 MB, streaming, real-time on one CPU
core, official wheels for Windows/macOS/Linux x86+ARM) runs as the wake
listener. The user's phrase is converted to its token/phoneme sequence at
CONFIGURATION time — a millisecond text operation, no training, fully local.
The model is identical for every user and every word.

Rejected alternatives: per-word trained openWakeWord models (needs
training compute + synthetic TTS clips per word — two classes of machines),
pretrained model catalog (kills free word choice; kept as FALLBACK if the
spike fails), commercial Porcupine (closed source, per-user account).

## Hard design criteria

1. **No transcript ever leaves the wake detector.** It emits exactly one
   signal ("keyword fired"); the post-wake utterance STT (cloud) is a separate
   provider, unchanged. The wake path must never double-feed user speech into
   the pipeline (maintainer's "Afrika" requirement).
2. **Bundled, not downloaded at runtime.** The model ships in
   `jarvis/assets/` like the existing `hey_jarvis` ONNX — present right after
   install, offline-capable. Repo grows ~14 MB once.
3. **One system everywhere.** The KWS engine is the default for every
   arbitrary phrase on every OS. `stt_match` (incl. the verified-GPU turbo)
   remains as a silent fallback tier only (e.g. sherpa-onnx import failure).
4. **Base install stays torch-free and universal** (CLAUDE.md §3):
   `sherpa-onnx` is onnxruntime-class, no CUDA/torch; it must pass
   `check_requirements_sync.py` + `check_lockfile_universal.py`.
5. **Boot budget untouched (AP-26):** model load happens where the wake
   detectors load today (deferred/background), never on the hear-ready path.
6. **Headless hosts:** voice is off there anyway; the engine must degrade to
   the existing honest no-op if audio/deps are absent.

## Spike gate (before any pipeline change)

Measure the candidate model CPU-only against the real captured fixtures in
`data/wake_debug/` (positives: Hey Nova / Hey Nico / Hey Ruben / Hey Luca;
negatives: bare-core, ambient speech, quiet noise, silence — the wake_bench
fixture classes):

- Recall on real positives (target: >= the turbo/cuda stream result, 11/13
  class; hard floor: clearly above base/cpu's 8/13).
- False accepts on negatives (target: 0 on silence/quiet, <= stt_match on
  ambient/bare).
- Non-English risk: German-sounding words / umlauts approximated through the
  available (en or zh-en) token set — measured, not assumed.
- Per-frame CPU cost + end-to-end latency (target: trigger < 500 ms after
  word end on CPU).

If the spike fails on German/free-word recall, the fallback plan is the
pretrained-catalog approach — decided WITH the maintainer, not silently.

## Architecture sketch

- New engine value `phoneme_kws` in `WAKE_ENGINES` + a provider under
  `jarvis/plugins/wake/` (sibling of `openwakeword_provider.py`), streaming
  chunks in, yielding the keyword on a hit; reuses the existing cooldown and
  energy-gate patterns.
- `resolve_wake_plan` chain becomes: custom_onnx (user-supplied file) →
  pretrained OWW model (exact known phrases) → **phoneme_kws (any phrase,
  default)** → stt_match (fallback) → none/hotkey.
- Phrase → token sequence resolution at plan time (pure function, unit-testable).
- Tests: contract tests with a fake sherpa runtime (CI has no audio), parity
  with the OWW provider's stats()/gating conventions, cross-OS unit runs
  (Windows + WSL; macOS via identical pure-Python tests + official wheels).

## Rollout

1. Spike + numbers (this session, wake_bench-style harness).
2. Engine + integration behind the plan resolver; stt_match untouched as
   fallback.
3. Bench before/after on Windows + WSL; live smoke on the maintainer's box.
4. Docs: deep-dive addendum + CUSTOM-WAKE-WORD-DESIGN update.
