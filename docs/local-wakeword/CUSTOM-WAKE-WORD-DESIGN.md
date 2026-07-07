# Custom Wake-Word — Design & Implementation Report

> **Superseded in part (2026-07-07):** the pretrained bundled models and every
> "degrade to a branded fallback" behavior described below were removed — the
> wake system is fully generic now. See
> `docs/superpowers/specs/2026-07-07-one-full-install-generic-wake-design.md`.
> Kept for historical context.

**Date:** 2026-05-29
**Status:** Design accepted → implementation in progress on branch `feat/jarvis-system-cursor`
**Builds on:** [`RESEARCH-AND-DESIGN.md`](RESEARCH-AND-DESIGN.md) (the 2026-05-24 lightweight-wake research)

---

## 1. Mandate

Let the user choose their **own** wake word instead of being locked to "Hey Jarvis".
The chosen word must **actually trigger** — not silently fail. The setting must be
editable from the desktop Settings UI (and the wizard) and must survive restarts and the
config-drift-guard.

## 2. The hard problem (why a naïve "change the config string" approach fails)

The default wake engine is **openWakeWord** — a tiny neural classifier (~3.5 MB ONNX) with a
**pretrained, fixed** `hey_jarvis` model. A small classifier only recognises the *one* phrase
it was trained on. You cannot type `"Computer"` into a config field and have the
`hey_jarvis` model detect it — it never fires, and the failure is **silent** (the exact
class of bug this codebase keeps catching: BUG-009/BUG-020 "Jarvis stopped working").

There are only three honest ways to detect an arbitrary wake word on-device without
streaming continuous audio to the cloud (a hard privacy constraint from the prior research):

| Path | How | Latency | Cost | Any phrase? | Offline? |
|---|---|---|---|---|---|
| **A — Pretrained model** | Pick one of openWakeWord's bundled models | 15–30 ms | CPU only, no GPU | ❌ fixed menu | ✅ |
| **B — STT text-match** | Local Whisper transcribes a rolling buffer; regex/fuzzy-match the phrase | 300–800 ms | needs local `faster-whisper` (GPU/CPU heavy) | ✅ truly any phrase | ✅ |
| **C — Custom ONNX** | User trains/loads a `.onnx` model for their phrase | 15–30 ms | CPU only, but a one-time training step | ✅ any phrase | ✅ |

**Continuous cloud STT is deliberately rejected** as a default: it would stream the user's
room audio to Groq/OpenAI 24/7, violating privacy constraint #1 of the prior research. (It
remains a possible *explicit, warned* opt-in but is not built here.)

## 3. Design — a unified, auto-resolving wake plan

A single config block is the source of truth the user edits:

```toml
[trigger.wake_word]
phrase = "Hey Jarvis"        # the human wake word the user wants
engine = "auto"              # auto | openwakeword | stt_match | custom_onnx
custom_model_path = ""       # path to a user-supplied .onnx (engine=custom_onnx)
sensitivity = 0.5            # 0..1, mapped onto the OWW activation threshold
fuzzy_match_ratio = 0.8      # STT-match tolerance for transcription drift (engine=stt_match)
```

At boot, `resolve_wake_plan()` (in `jarvis/speech/wake_phrase.py`) turns this into a
concrete `WakeWordPlan` using the following decision lens:

```
1. engine == "custom_onnx" or custom_model_path set, and the file exists
       → Path C: load that ONNX into OpenWakeWordProvider.            (CPU, any phrase)

2. phrase normalises to a KNOWN pretrained model
   (jarvis | alexa | mycroft | rhasspy)
       → Path A: OpenWakeWordProvider with that model.               (CPU, instant)

3. arbitrary phrase, local Whisper available
       → Path B: RollingWhisperWake with a phrase-derived matcher,
         auto-enabling the local Whisper engine.                     (any phrase, needs GPU/CPU whisper)

4. arbitrary phrase, NO local Whisper (slim VPS, no [desktop] extra)
       → GRACEFUL DEGRADE: fall back to "Hey Jarvis", and surface a
         clear English message telling the user the chosen phrase
         needs the local-Whisper extra or a custom ONNX model.
         NEVER pretend it works.                                     (anti-silent-fail)
```

`WakeWordPlan` (frozen dataclass) carries everything the pipeline needs:

```python
phrase: str               # raw user phrase
engine: str               # resolved concrete engine (never "auto")
oww_model_path: str|None  # absolute ONNX path for Path A/C
oww_keyword: str          # canonical key OWW reports (e.g. "hey_jarvis")
threshold: float          # sensitivity mapped to the OWW activation threshold
matcher: WakeMatcher      # phrase matcher for the STT verifier + rolling-whisper
needs_local_whisper: bool # True only for Path B
degraded: bool            # True if we could not honour the request
message: str              # English status string for logs + UI
```

### Why the pretrained models are free wins

The installed `openwakeword` package already ships, on disk, the ONNX for:
`hey_jarvis`, `alexa`, `hey_mycroft`, `hey_rhasspy` (plus `timer`/`weather`, which are not
wake names). So Path A supports **four** instant, CPU-only, offline wake words with zero
download. `hey_jarvis` stays bundled in-repo (`jarvis/assets/wakeword/`) for first-boot
offline; the other three resolve from the package's `resources/models/` directory.

### The phrase matcher (`WakeMatcher`)

For Path B and the post-OWW prefix verifier, the chosen phrase must be matched against a
noisy STT transcript ("Athena" → Whisper may emit "Athene", "Atena", "a Tina"). `WakeMatcher`:

- **Duck-types `re.Pattern.search(text)`** returning an object with `.group(0)` — so it is a
  drop-in replacement everywhere the code currently threads a `re.Pattern`. No call-site churn.
- For the **default `hey_jarvis`** family it wraps the *existing* `rolling_whisper_wake.DEFAULT_PATTERN`
  **byte-for-byte**, so all ~40 existing wake tests stay green and the BUG-009 bare-"Jarvis"
  protection is preserved.
- For an **arbitrary phrase** it normalises (lower-case, strip punctuation, collapse spaces),
  strips common wake prefixes (`hey/hi/ok/okay/hallo/hallo`) from both phrase and transcript,
  then fuzzy-matches the core phrase against a sliding token window using
  `difflib.SequenceMatcher` with a configurable ratio (`fuzzy_match_ratio`, default 0.8). This
  tolerates transcription drift without the brittleness of a generated regex.

## 4. Anti-drift: `engine` is a five-layer enum

`engine` is a string that crosses Python ↔ TOML ↔ Pydantic ↔ TypeScript ↔ UI label. Per
[`docs/anti-drift-three-layer.md`](../anti-drift-three-layer.md) this gets the five-layer
treatment:

- **Single source of truth:** `jarvis/speech/wake_constants.WAKE_ENGINES`.
- **Pydantic:** `WakeWordConfig` validates `engine` against the SoT and coerces an unknown
  value to `"auto"` with a warning (boot-resilient — AP-16: a stale/garbage value must not
  brick the boot).
- **TypeScript mirror + parity test:** `tests/unit/speech/test_wake_engine_parity.py` asserts
  the frontend constant lists exactly the same engines, so the UI dropdown can never drift
  from the backend.

## 5. Persistence — 3 layers (BUG-010 defence)

A wake word is a **user-switchable** setting, so per the project mandate it is written to all
three layers at once (mirroring `config_writer.set_brain_primary`):

1. `jarvis.toml` `[trigger.wake_word]` (via `config_writer`, lock + tempfile + BOM-safe).
2. `scripts/config-soll.json` `trigger.wake_word.*` (so the drift-guard *protects* the user's <!-- i18n-allow -->
   choice against rogue parallel sessions instead of reverting it).  <!-- i18n-allow -->
3. `JARVIS__TRIGGER__WAKE_WORD__*` User-scope ENV (boot-override consistency).

All three are **best-effort / graceful**: a missing `config-soll.json` or a non-Windows host  <!-- i18n-allow -->
(no registry) is a no-op, never an exception — the live switch that already succeeded must
not be undone by a persistence hiccup.

## 6. Cloud-first compliance

- **Default unchanged:** `phrase = "Hey Jarvis"`, `engine = "auto"` → bundled `hey_jarvis`
  ONNX, CPU-only, offline. A fresh `python:3.11-slim` box behaves exactly as today.
- **Path A** (alexa/mycroft/rhasspy) is CPU-only and offline — VPS-friendly.
- **Path B** (arbitrary phrase) requires the `[desktop]`-tier local Whisper. On a slim box it
  degrades gracefully with a clear message instead of silently failing.
- No new **base** dependency: `faster-whisper` stays an opt-in extra; the custom-phrase path
  simply turns it on when present.

## 7. The `SpeechPipeline` seam (low-risk)

`SpeechPipeline.__init__` gains one optional `wake_plan: WakeWordPlan | None = None`:

- `wake_plan is None` → **byte-identical** to today (every existing test constructs the
  pipeline this way).
- `wake_plan` provided → overrides the OWW model path, the verifier matcher, the
  rolling-whisper pattern, and the canonical keyword from the plan.

`jarvis/ui/desktop_app.py` resolves the plan once, decides whether to build a local
`FasterWhisperProvider` (`plan.needs_local_whisper or cfg.trigger.heavy_local_whisper`), and
threads the plan in. If `plan.degraded`, it logs the English message and (later) the UI
surfaces it.

## 8. Files touched

**New:** `jarvis/speech/wake_constants.py`, `jarvis/speech/wake_phrase.py`,
`tests/unit/speech/test_wake_phrase.py`, `tests/unit/speech/test_wake_engine_parity.py`,
`tests/unit/core/test_wake_word_config.py`,
`jarvis/ui/web/frontend/src/constants/wakeEngines.ts`.

**Modified:** `jarvis/core/config.py` (`WakeWordConfig`), `jarvis.toml`,
`jarvis/plugins/wake/openwakeword_provider.py`, `jarvis/speech/wake_verifier.py`,
`jarvis/speech/rolling_whisper_wake.py`, `jarvis/speech/pipeline.py`,
`jarvis/ui/desktop_app.py`, `jarvis/core/config_writer.py`,
`jarvis/ui/web/settings_routes.py`, `jarvis/ui/web/frontend/src/views/SettingsView.tsx`,
`jarvis/ui/web/frontend/src/hooks/*`, `jarvis/ui/web/frontend/src/i18n/locales/{de,en,es}.json`,
`jarvis/setup/wizard.py`.

## 9. Acceptance criteria

1. Default boot (`phrase="Hey Jarvis"`, `engine="auto"`) behaves exactly as today; all
   existing wake tests stay green.
2. Setting `phrase="Alexa"` (or mycroft/rhasspy) makes Jarvis wake on that word with no
   download, no GPU.
3. Setting an arbitrary `phrase="Computer"` on a machine with local Whisper makes Jarvis wake
   on "Computer" (Path B), and auto-enables the local Whisper engine.
4. Setting an arbitrary phrase on a box *without* local Whisper degrades to "Hey Jarvis" and
   surfaces a clear English message — never a silent dead listener.
5. The choice is editable in the desktop Settings UI, persists across restart, and is not
   reverted by the drift-guard.
6. `engine` cannot drift between backend and UI (parity test).
