# One Official Full Install + Fully Generic Wake Word — Design Spec

- **Date:** 2026-07-07
- **Status:** Approved by maintainer (brainstorming session 2026-07-07)
- **Relates to:** `2026-07-05-universal-wake-kws-design.md` (vosk_kws any-word
  engine — this spec builds on it), `2026-07-06-onboarding-install-redesign-design.md`
  (installer flow — this spec **amends one non-goal**, see §4),
  `2026-06-20-assistant-name-coupled-to-wake-word-design.md`.

## 1. Problem statement

Two maintainer directives (2026-07-07):

1. **Zero hardcoded wake words.** No branded or pre-named wake-word artifact
   may ship with the product, for trademark reasons. The wake system must be
   fully generic: any user-chosen phrase, on every machine, with neutral
   examples only. Removing the remaining branded artifacts must not break any
   feature — a user who *chooses* "Hey Jarvis" as their phrase must still get
   a working wake word (via the generic engines).
2. **One official full installation.** Users see exactly ONE advertised
   install path, and it installs everything the repository offers (desktop,
   telephony, channels, local voice models). Whatever an OS cannot run is
   skipped automatically.

### Current state (verified against code 2026-07-07)

Most of directive 1 already landed in earlier waves:

- `DEFAULT_WAKE_PHRASE = ""` — no default wake word; the user opts in
  (`jarvis/speech/wake_constants.py:44`).
- `INSTANT_WAKE_PHRASES = ()` — no advertised phrase list
  (`wake_constants.py:111`).
- vosk (any-word KWS), openwakeword runtime, and CPU onnxruntime are **base**
  dependencies; any phrase already works in the base install
  (`pyproject.toml:27-47`).
- Honest degrade: an unservable phrase arms NO detector and points to
  hotkey / push-to-talk — no silent branded substitute
  (`jarvis/speech/wake_phrase.py:515-540`, product rule 2026-07-04).

Remaining hardcoded/branded surface:

- Bundled pretrained models `jarvis/assets/wakeword/hey_jarvis_v0.1.onnx`
  and `hey_rhasspy_v0.1.onnx` (~3.9 MB with the feature models).
- `KNOWN_OWW_MODELS = {"jarvis": "hey_jarvis", "rhasspy": "hey_rhasspy"}`
  (`wake_constants.py:99-102`) plus a runtime probe that resolves ANY typed
  phrase against the pretrained models the installed `openwakeword` package
  exposes (`match_known_oww_model`, `wake_constants.py:190-210`) — typing
  "Alexa" would load the third-party brand model from the upstream package.
- The jarvis-family special case: `JARVIS_WAKE_PATTERN`
  (`wake_constants.py:50-53`), `matcher.is_jarvis_default`, and the
  `verify_prefix` special-handling in `resolve_wake_plan`.
- Stale docs/prose that still advertise brand names or retired behavior:
  `docs/local-wakeword/USER-GUIDE-WAKE-WORD.md` (four "instant phrases" incl.
  Alexa; "falls back to Hey Jarvis" — both no longer true),
  `wake_phrase.py` module docstring (retired Hey-Rhasspy degrade),
  `core/config.py:137` comment ("jarvis/alexa/mycroft/rhasspy").
- Packaging: `[full]` deliberately EXCLUDES `[local-voice]`
  (`pyproject.toml:133-142`), and docs lead with the slim base install.

## 2. Decision D1 — fully generic wake system

1. **Delete the branded models.** Remove `hey_jarvis_v0.1.onnx` and
   `hey_rhasspy_v0.1.onnx` from `jarvis/assets/wakeword/`. Keep
   `melspectrogram.onnx` and `embedding_model.onnx` — they are word-agnostic
   feature extractors required by user-trained custom openWakeWord models
   (and by the future generic KWS per the 2026-07-05 spec).
2. **Empty the brand map, remove the upstream probe.**
   `KNOWN_OWW_MODELS := {}`; `match_known_oww_model` no longer probes the
   installed openwakeword package for pretrained brand models. A typed brand
   word ("Alexa", "Hey Jarvis", …) is treated exactly like any other phrase.
3. **Engine chain becomes purely generic:**
   user-trained `custom_onnx` (if configured and matching the phrase) →
   `vosk_kws` (any word, base install) → `stt_match` (local-Whisper accuracy
   path where installed — everywhere on the full install) → `engine="none"`
   with the existing honest hotkey message.
4. **Retire the jarvis-family special case.** `JARVIS_WAKE_PATTERN`, the
   `is_jarvis_default` matcher flag, and their `verify_prefix` special-handling
   collapse into the generic sound-folded fuzzy matcher + the generic STT
   prefix verification already used for custom models. The BUG-009 guarantee
   (bare "Jarvis" must NOT fire when the phrase is "Hey Jarvis") must be
   preserved by the generic strict-adjacency matcher; its guard test stays.
5. **Onboarding.** Default phrase stays empty; onboarding/Settings require an
   explicit phrase. Placeholder examples are neutral, non-branded words
   (e.g. "Computer", "Athena"). No pre-selected suggestion.
6. **Back-compat.** Existing configs keep working unchanged: a configured
   phrase resolves via the generic chain; a configured `custom_model_path`
   is untouched. No config migration needed.
7. **Drop `pvporcupine`.** The Porcupine wake engine is referenced only in
   comments/legacy wizard text (no plugin, not in `WAKE_ENGINES`), is
   proprietary-keyed, and ships branded built-in keywords — remove it from
   `[local-voice]` and clean the dangling mentions
   (`core/config.py`, `setup/wizard.py`, `hardware/detection.py`,
   `core/registry.py` docstring). Verify zero imports before removal.

## 3. Decision D2 — one official full installation

1. **`[full]` now includes `[local-voice]`:**
   `full = personal-jarvis[desktop,telephony,channels,local-voice]`. Platform
   markers keep skipping what an OS cannot use. GPU *acceleration* remains a
   runtime capability probe (AP-25) — the full install must not hard-require
   CUDA/nvidia wheels beyond what the existing `[local-voice]` pins pull.
2. **Every user-facing install path advertises ONLY the full profile:**
   root `README.md` quickstart, `install/README.md`, the install one-liner /
   `installer.py` pip step, and in-app hints. The 2026-07-06 installer spec's
   prefetch step (`--prefetch`) now legitimately covers the wake/STT Whisper
   models because the runtime for them is installed.
3. **The slim base survives as an internal technical floor** (CI, tiny VPS,
   experts) — documented in a short "minimal install (advanced)" appendix, no
   longer the headline path. CLAUDE.md §3's "base install stays torch-free"
   rule and the guards (`check_lockfile_universal.py`,
   `check_requirements_sync.py`) stay UNCHANGED and green.
4. **CLAUDE.md/docs note:** one line documenting that the official install
   path is the full profile; base = internal floor.

## 4. Amendment to the 2026-07-06 install spec

The non-goal "No GPU/`[local-voice]` extras in the base install (stays opt-in,
AP-25)" is **amended**: the *base* install indeed stays torch-free (unchanged),
but the *official installer/one-liner* now installs the full profile including
`[local-voice]`. AP-25 is unaffected — it governs runtime GPU *usage*, not
packaging. The rest of that spec (prefetch and onboarding-once) stands. Its
fully non-interactive Stage-1 rule was superseded on 2026-07-11: only a missing
Python/Git prerequisite may trigger one explicit install-consent prompt.

## 5. Error handling

- Unservable phrase → existing honest degrade (wake off + hotkey message),
  unchanged (`wake_phrase.py` step 5).
- Vosk per-language model download failure at setup → clear English message +
  lazy retry at runtime + hotkey fallback; never fatal (matches the
  2026-07-06 installer failure policy).
- Headless / no-mic hosts → existing quiet no-op behavior, unchanged.

## 6. Testing

- Update wake-plan/engine tests that assert `KNOWN_OWW_MODELS` or pretrained
  resolution (`tests/unit/speech/` incl. `test_wake_engine_parity.py`).
- New guard test: `jarvis/assets/wakeword/` contains ONLY the word-agnostic
  feature models (no `hey_*`/brand ONNX), and no branded wake-word names in
  source outside i18n-allow forensic quotes / migration notes.
- BUG-009 guard (bare "Jarvis" must not fire) re-targeted at the generic
  matcher; AP-27 recall guard
  (`test_rolling_whisper_wake_silence_ghost.py`) stays green unchanged.
- Packaging assertion: `[full]` includes `local-voice` (pyproject parse);
  lockfile/base guards stay green.
- §3 non-maintainer paths verified: fresh install with one arbitrary key,
  headless `python:3.11-slim` boot (base floor), cross-family fallback.

## 7. Out of scope

- Renaming the product itself ("Personal Jarvis" as a name is a separate
  trademark topic, explicitly not part of this change).
- The neural generic-KWS training pipeline (2026-07-05 spec) — unchanged.
- The onboarding step flow itself (2026-07-06 spec) — unchanged apart from
  the §4 amendment.
- "Jarvis Hub": maintainer mentioned the term; no component under that name
  exists in-repo. The full install covers all repo components; to be
  clarified if it refers to something specific.

## 8. Risks / rollout

- **Feature preservation is the hard constraint:** every previously working
  wake word keeps working; only the serving engine changes (bundled branded
  model → generic chain). Existing user configs need no migration.
- Wheel shrinks by the two removed ONNX models.
- No boot-path changes (AP-26 untouched); vosk is already a base dep.
- Full-install size grows (torch et al.) — accepted trade-off per maintainer
  decision; tiny-VPS users keep the documented minimal appendix path.
