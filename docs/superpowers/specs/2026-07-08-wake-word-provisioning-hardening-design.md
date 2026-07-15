# Wake-Word Provisioning + Reliability Hardening — Design Spec

- **Date:** 2026-07-08
- **Status:** Approved by maintainer (brainstorming session 2026-07-08, full scope)
- **Builds on / completes:**
  - `2026-07-05-universal-wake-kws-design.md` (the `vosk_kws` any-word engine — the provider is built; this spec ships its **model**).
  - `2026-07-07-one-full-install-generic-wake-design.md` (§5 already mandates
    "Vosk per-language model download failure at setup → clear English message +
    lazy retry at runtime + hotkey fallback; never fatal" — this spec implements
    that never-built download step).
- **Bug report:** Private source note (custom wake word never fires on
  a fresh v1.0.3 install; `auto` silently degrades to `stt_match`).

## 1. Problem statement

On a stock v1.0.3 install a user configures a hard proper-noun wake phrase
(e.g. `"Hey Athena"`, `engine="auto"`), completes onboarding, and the wake word
**never fires**. Verified root cause (not user misconfiguration):

1. **No Vosk model is ever provisioned.** `pyproject.toml:46-51` and
   `wake_constants.py:175-184` both document a model "fetched once at setup into
   `data/wake_models/vosk/<lang>/`", but **no download code exists anywhere in
   the repo** (verified: no `alphacephei`, no `vosk-model` URL, no zip
   machinery). The directory stays empty; `resolve_vosk_model_path()` returns
   `None`; the `vosk_kws` branch of `resolve_wake_plan` is skipped
   (`wake_phrase.py:416-443`).
2. **The openWakeWord feature backbones are not shipped.**
   `jarvis/assets/wakeword/{melspectrogram,embedding_model}.onnx` exist on the
   maintainer's disk but are **untracked** — `.gitignore:64` excludes all
   `*.onnx` and whitelists only `silero_vad.onnx`. `bundled_wakeword_models()`
   returns `None` in any clean checkout (`assets/__init__.py:51-57`), so
   `custom_onnx` can never load even if a user supplies a trained model.
3. **`auto` lands on the weakest engine silently.** With 1 and 2 dead, the only
   reachable engine is `stt_match` (base/CPU Whisper transcribe-and-match), which
   structurally cannot spell a hard custom name — the base model garbles the
   spoken proper noun into unrelated words, so `matched=0` forever
   (AP-25/AP-27/BUG-037). `resolve_wake_plan` marks this path `degraded=False`
   (`wake_phrase.py:469-483`) — no log/UI warning.
4. **Onboarding acknowledges, never verifies.** The desktop wake step calls
   `acknowledgeWakeWord()` then saves, with no live mic check and no
   "say it once" confirmation (`WakeWordStep.tsx:53-54`). Both symptoms — quiet
   mic and unrecognizable word — pass setup unnoticed.
5. **Secondary: the CPU `stt_match` path wedges.** 8 s → 20 s hangs + self-heal
   rebuild on the CUDA-less box (AP-24/BUG-036), even though `cpu_threads=2` is
   already pinned (`jarvis/plugins/stt/__init__.py:694`).

This is a textbook AP-23 failure: built and tested against the maintainer's
machine, where the assets happen to sit on disk; broken for every downloader.

## 2. Goals / non-goals

**Goals**
- A freely-chosen wake phrase works out-of-the-box on any online machine, no
  training, no GPU — by provisioning the `vosk_kws` model so `auto` resolves to
  it instead of `stt_match`.
- `custom_onnx` becomes possible on a clean checkout (backbones shipped).
- `auto` never silently lands on `stt_match` for a custom word — it warns loudly
  (log + UI) with a one-click remedy.
- Onboarding verifies the mic level and the spoken word before marking complete.
- The `stt_match` CPU path is hardened defensively and, in the normal case,
  bypassed entirely by `vosk_kws`.

**Non-goals**
- No neural custom-KWS training pipeline (separate 2026-07-05 spec).
- No change to the onboarding step *flow* (welcome → language → wake-word →
  api-keys → finish) beyond adding verification inside the wake step.
- **No promise to eliminate the ctranslate2 CPU deadlock.** It is
  constellation-specific (AP-25). We harden and bypass; we do not claim a cure.
- The torch-free base install and its CI guards stay UNCHANGED and green
  (`check_lockfile_universal.py`, `check_requirements_sync.py`). Vosk is already
  a base dep; the model is data, not a wheel.

## 3. Design — five building blocks

### B1 — Vosk model provisioning (the core fix)

New helper module `jarvis/speech/wake_model_fetch.py` (pure download/extract; no
heavy imports, no `jarvis.*` cycle beyond config):

- **Language → model map** (Apache-2.0, verified 2026-07-08 against
  `alphacephei.com/vosk/models`):
  | lang | zip | size |
  |---|---|---|
  | `en` | `vosk-model-small-en-us-0.15.zip` | 40 MB |
  | `de` | `vosk-model-small-de-0.15.zip` | 45 MB |
  | `es` | `vosk-model-small-es-0.42.zip` | 39 MB |
  Base URL `https://alphacephei.com/vosk/models/`. Pinned by exact filename +
  a SHA-256 recorded per file (fail-closed on hash mismatch).
- **Target dir** must match `resolve_vosk_model_path` exactly: the extracted
  `vosk-model-small-<lang>-*/` folder lands under
  `<memory.data_dir>/wake_models/vosk/<lang>/` (the resolver accepts a model one
  level down via its `am/` / `conf/model.conf` probe,
  `wake_constants.py:202-208`). Data dir is `cfg.memory.data_dir` (env seam
  `JARVIS__MEMORY__DATA_DIR`), **not** `paths.user_data_dir()`.
- **HTTP:** `httpx` (repo standard), explicit timeout, streamed to a tempfile,
  atomic `os.replace` of the extracted dir; stdlib `zipfile` for extraction
  (no zip helper exists to reuse). Idempotent: a present, resolvable model is a
  no-op.
- **Language selection:** `cfg.stt.language` (`config.py:307`), first BCP-47
  segment; `"auto"`/unset → `DEFAULT_LOCALE` (`turn_language.py:46`, `"en"`).
- **Two invocation seams, mirroring the Whisper precedent:**
  1. **Setup:** extend `prefetch_all()` (`jarvis/setup/prefetch.py`) with a Vosk
     step — best-effort, honest note on failure, returns nonzero only for the
     installer summary. Reached via `python -m jarvis --prefetch`.
  2. **Runtime lazy safety net:** an off-boot task (register on
     `_heavy_backend_bg`, `desktop_app.py:1688/1748`, behind the live wake
     listener) that fetches the model once if missing. Never on the boot
     critical path (AP-26; boot-budget gate stays green).
- **In-app recovery:** the existing wake-step "enable local speech" affordance
  (`WakeWordStep.tsx` → `POST /api/settings/wake-word/enable-local-speech`,
  `useWakeWord.ts:157-213`) also triggers/awaits the Vosk fetch, so a user with
  a dead/absent model recovers entirely in-app (CLAUDE.md §3).

### B2 — Ship the openWakeWord backbones

- Whitelist the two word-agnostic feature models in `.gitignore` (mirroring the
  existing `!jarvis/assets/vad/silero_vad.onnx` line) and `git add -f` them:
  `melspectrogram.onnx` + `embedding_model.onnx` (~2.4 MB total). No branded /
  `hey_*` model is added (2026-07-07 spec §2.1 — those stay deleted).
- Package-data glob `assets/**/*` already ships them once tracked.
- Guard test: `jarvis/assets/wakeword/` contains ONLY the two word-agnostic
  models — no `hey_*`/brand ONNX — so this whitelist can never smuggle a branded
  model back in.

### B3 — Loud, honest degrade when only `stt_match` is reachable

In `resolve_wake_plan` (`wake_phrase.py:445-483`), when the resolved engine is
`stt_match` for a **custom** phrase (i.e. not served by `vosk_kws`/`custom_onnx`):
- Set `degraded=True` and emit a `log.warning` naming the weakness and the remedy
  ("install the Vosk model for `<lang>` to make `<phrase>` reliable").
- Surface the same message on the WakeWordPlan `message` so the desktop wake step
  / settings show it. The default "Hey Jarvis"-class phrase served by a real
  engine is unaffected. This does not change the honest `engine="none"` degrade
  (block 4 of the resolver) — it upgrades the *silent* `stt_match` case to a loud
  one. Guarded by a new case in `tests/unit/speech/test_wake_plan_*`.

### B4 — Onboarding verifies, not just acknowledges

- **Backend:** a small route (under `/api/onboarding/` or `/api/settings/`) that
  returns a live mic dBFS reading, reusing the logic of
  `diagnose.py:step_mic_level` (extract the pure RMS→dBFS measurement from the
  print-coupled CLI function into a reusable async helper; the CLI keeps its bars
  by calling the helper). Warn threshold −40 dBFS, matching the existing tool.
- **Frontend:** `WakeWordStep.tsx` gains, before `acknowledgeWakeWord()`:
  (a) a mic-level meter that warns when too quiet, and (b) an optional
  "say your wake word once" confirmation. Acknowledgment proceeds regardless
  (never block finishing setup — the wizard's headless/no-mic contract,
  `wizard.py:560-568`), but a failed check is shown clearly.
- Headless / no-mic hosts: the mic route degrades to a clear "no input device"
  payload; onboarding still completes.

### B5 — Harden the `stt_match` CPU path (AP-24)

- **Primary mitigation is B1:** once `vosk_kws` serves the wake word, the
  ctranslate2 wake path is not used at all in the normal case.
- **Defensive hardening for the residual `stt_match` users:** bound the
  ctranslate2 / OpenMP thread pool via environment (`OMP_NUM_THREADS`,
  `CT2_*` as appropriate) set **before** ctranslate2 is imported — today there
  is zero env-level bounding anywhere near it (verified), only the constructor
  `cpu_threads=2`. Keep the existing per-instance non-blocking `_infer_lock`
  (`fwhisper.py:322/547`) and the 8 s/20 s wedge backstop + `recover()`
  unchanged (`rolling_whisper_wake.py:82/96`, `fwhisper.py:397`).
- **No claim of a full cure** — this reduces the deadlock surface and is backed
  by the bypass. Explicitly documented as such (AP-25 discipline).
- Quiet-mic secondary symptom: leave the RMS/peak gates as-is
  (`min_rms=0.003`/`min_peak=0.008`, `rolling_whisper_wake.py:201/212`); B4's mic
  check is the real fix (tell the user their mic is too quiet at setup).

## 4. Error handling (never fatal — cross-platform contract)

- **Offline / flaky mirror:** download prints an honest English note; runtime
  lazy retry remains; wake degrades to the loud `stt_match`/hotkey path. Never
  fatal (matches prefetch doctrine, `prefetch.py:9-13`).
- **Headless Linux `python:3.11-slim`:** no audio, no GPU, no keyring touched.
  The Vosk fetch is disk-only and works; the mic check reports "no device";
  wake is a quiet no-op if voice is disabled. Base `pip install` + boot unchanged.
- **Hash mismatch / corrupt zip:** discard, note, lazy-retry; never install a
  partial model.
- **Cross-family:** unaffected — wake is local; this touches no cloud provider.

## 5. Testing

- `wake_model_fetch`: unit tests with a fake HTTP client + a tiny fake zip —
  target-dir layout resolves via `resolve_vosk_model_path`; idempotent no-op when
  present; hash-mismatch rejects; offline failure is non-fatal.
- `resolve_wake_plan`: new case — custom phrase + only `stt_match` reachable →
  `degraded=True` + warning message; `vosk_kws` present → selected over
  `stt_match`; default phrase unaffected. AP-27 recall guard
  (`test_rolling_whisper_wake_silence_ghost.py`) stays green.
- Backbones guard test (B2) as above.
- Prefetch: Vosk step best-effort, honest note on failure, non-fatal.
- Onboarding mic route: returns a dBFS float; headless → "no device" payload.
- §3 non-maintainer paths: fresh install with one arbitrary key, headless slim
  boot (base floor), cross-family fallback — all still reach a working path.
- Base/lockfile universality guards stay green (no new wheel).

## 6. Proof on the maintainer test box (the reproduction machine)

This machine is the exact bug case (CUDA-less Windows, empty Vosk dir). After
implementation, verify **behavior, not just green tests**:
1. Trigger the Vosk fetch (prefetch or the in-app affordance); confirm
   `data/wake_models/vosk/de/` now resolves via `resolve_vosk_model_path`.
2. Re-resolve the wake plan; confirm `engine=vosk_kws` (not `stt_match`).
3. Speak the configured phrase; confirm it fires (log shows a wake, not
   `matched=0`), via `python -m jarvis.speech.diagnose` + the live desktop app.

## 7. Risks / rollout

- **Feature preservation:** every previously working wake word keeps working;
  only the serving engine improves. No config migration.
- **Repo size:** +2.4 MB tracked (backbones). Models are per-install data, never
  committed.
- **Boot path untouched** (AP-26): fetch is setup-time or off-boot only.
- **Download dependency on a third-party mirror:** mitigated by best-effort +
  lazy-retry + honest degrade; SHA-pinned to prevent a poisoned mirror.
- **The CPU wedge is only mitigated, not eliminated** — accepted, because
  `vosk_kws` bypasses it in the normal case.
