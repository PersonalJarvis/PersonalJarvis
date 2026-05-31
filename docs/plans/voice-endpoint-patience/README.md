# Voice Endpoint Patience — Root-Cause Analysis & Fix

> **Status (2026-05-25):** Diagnosis complete **and the core fix (Task 1 + Task 2) is
> implemented, tested, and green.** A thinking pause (quiet tail) no longer ends the turn;
> only loud speaker-bleed forces the endpoint. Tasks 3–5 (config consolidation, probe-cadence
> tuning, semantic patience) remain optional hardening and are **not** done.
>
> **Implemented:**
> - `jarvis/audio/vad.py` — the probe callback now reports `tail_loud` (tail energy vs. the
>   relative-silence floor).
> - `jarvis/speech/pipeline.py` — `_on_vad_probe` / `_stt_probe_async` gate **both** end
>   signals on `tail_loud`: a quiet empty/stable tail defers to `silence_ms` instead of
>   forcing the endpoint.
> - `tests/unit/speech/test_thinking_pause_patience.py` (new, 4 cases) +
>   `tests/unit/audio/test_vad_turn_taking.py` (new case) pin the behavior. 200 speech/audio
>   unit tests green.

**Goal:** Stop the voice pipeline from ending the user's turn while the user is still
thinking / about to add more. Make "give the user time" a *single, tested, pinned*
behavior so it stops regressing every time an unrelated speech feature is added.

**The bug (user's words):** "When it's hard to finish the sentence, Jarvis just stops
listening and submits. If you pause briefly to think or want to add something, it has
already submitted. You get no time to think." The user states this was fixed thoroughly
before but **keeps breaking whenever new features are added.**

**Mandate (from memory + this session):** When in doubt, keep listening. Precision over recall on the endpoint decision (prefer ending too late over too
early).

---

## 1. How the turn actually ends today (verified end-to-end)

The turn-ending decision is **not** one place. It is a layered chain across two modules:

```
mic 100ms blocks
   └─> SileroEndpointer.utterances()                      jarvis/audio/vad.py:109
        ├─ per 512-sample (32ms) frame: Silero prob + RMS  vad.py:146-160
        ├─ silent_run accumulator (+ cancel-hysteresis)    vad.py:182-219
        ├─ probe_callback(tail) every ~650ms               vad.py:228-236  ─┐
        └─ THREE endpoint triggers (OR-ed):                vad.py:244-248   │
             (a) silent_run >= silence_frames  → "silence"                  │
             (b) total >= max_samples          → "max_utterance"           │
             (c) _endpoint_requested == True   → "stt_stable"  <───────────┘
                                                          set by request_endpoint()
   └─> _on_vad_endpoint(reason)                            pipeline.py:811
   └─> _handle_utterance(pcm)                              pipeline.py:2150
        └─ _complete_or_buffer_context(text)               pipeline.py:2271 / 2430
```

The STT probe (`_on_vad_probe` → `_stt_probe_async`, `pipeline.py:842-962`) transcribes only
the **last 1800ms** of audio (`probe_tail_ms=1800`, `pipeline.py:589`) every ~650ms and,
when it thinks the tail is "empty" or "stable", calls `self._vad.request_endpoint()`
(`pipeline.py:917` and `:947`).

### The single most important fact

`request_endpoint()` sets `_endpoint_requested = True` (`vad.py:94`). Trigger **(c)** in
`vad.py:240-248` fires on the **very next frame, completely independent of `silent_run`**.

> **The 1200ms silence guard does not protect the probe path.** The probe can end the turn
> at any moment, after only ~650ms of "active speech", the instant its tail transcription
> comes back short/empty.

This is by design — see the constructor comment at `pipeline.py:577`:
*"the STT probe path normally fires within ~2.5 s, so this cap is the last safety net, **not
the primary path**."* The probe **is** the primary turn-ender. Silence (1200ms) and
max-utterance (8s) are backstops.

---

## 2. Root cause: a thinking pause is indistinguishable from "done"

The STT probe was built to defeat **speaker bleed** (music/podcast keeps Silero reporting
"speech" while Whisper sees only the user; fix `a7adecf78`, BUG `BG-VAD-2026-05-05`). It
ends the turn when the tail transcribes to nothing.

The problem: **a thinking pause produces exactly the same empty tail as "user is done".**
The probe has no concept of "the user might continue." Two code paths cut the user off:

### Suspect #1 — `tail_is_empty` → immediate endpoint (HIGH, verified)
`pipeline.py:906-919`. If the tail transcript is empty, `< 4` chars (`_probe_min_text_len`,
`pipeline.py:622`), or matches `_STT_HALLUCINATION_RE`, then `request_endpoint()` fires
**immediately**. When the user pauses ≥ ~650ms after ≥ ~650ms of speech, the next probe sees
a near-silent tail → empty → turn ends. No silence floor is required for this path.

### Suspect #2 — `tail_is_stable` → endpoint after 2 identical probes (MEDIUM, verified)
`pipeline.py:939-948` with `_probe_required_stable = 1` (`pipeline.py:606`). If two probes
~650ms apart return the same short text, the turn ends. A user who says "…und dann…" and
pauses gets the same tail twice → endpoint after ~1.3s of pause. No silence floor required.

### Suspect #3 — `cancel_hysteresis_ms = 160` eats the resume (MEDIUM, verified, NEW today)
`vad.py:45,65,186-219` (commit `87b27e0b5`, **today**). While a silence timer runs, resumed
speech must be **5 consecutive `is_speech` frames (160ms)** to reset `silent_run`
(`vad.py:195`). But the relative-silence RMS gate (`vad.py:150-160`) classifies *quieter*
resumed speech (below 22% of the earlier peak) as silence, so `resume_run` resets to 0
(`vad.py:208`) and never reaches 5. Net: after a pause, a softly-spoken continuation may fail
to cancel the silence timer, and the `silence` endpoint fires mid-resumption. This commit
was made to fix the **opposite** bug ("Jarvis never endpoints in a noisy room") — it is
directly antagonistic to pause-tolerance.

### The only existing patience layer — and why it's too narrow (verified)
`_complete_or_buffer_context` (`pipeline.py:2430`) + `_looks_context_incomplete`
(`pipeline.py:293`) **do** try to keep listening: if the transcribed text "looks
incomplete", the fragment is buffered and the pipeline keeps listening, with a 4s auto-flush
safety (`pending_context_flush_s=4.0`, `pipeline.py:462`).

But this runs **after** the VAD endpoint + STT, on **text**, and `_looks_context_incomplete`
only catches:
- empty text / `< 2` words (`pipeline.py:301-307`)
- text ending in sentence punctuation = treated complete (`pipeline.py:303-304`)
- `_INCOMPLETE_TAIL_RE` matches (`pipeline.py:310`)
- **exactly five** dangling starters: `"wenn"`, `"falls"`, `"if"`, `"when"`, `"ob du"`,
  `"jarvis wenn"` (`pipeline.py:318-326`).

So "Erstelle mir eine Liste mit den wichtigsten …" (pause) ends on "wichtigsten" → not a
dangling conjunction, > 2 words, no punctuation → **judged complete → submitted instantly.**
The vast majority of natural mid-thought pauses fall straight through this filter.

**Crucially**, this layer is the *dangerous* lever: commit `c122429a1` ("remove
false-positive incomplete-starters") and the comment at `pipeline.py:313-317` document that
broadening it (e.g. adding `"kannst du"`) trapped the pipeline in **silent LISTENING** on
complete questions. So the obvious "just add more patterns" fix is exactly what caused a
*different* regression before.

---

## 3. Why it keeps breaking — the regression mechanism

> **Endpoint patience is spread across three orthogonal layers — VAD `silence_ms`, the STT
> probe's empty/stable signals, and the post-submit `_looks_context_incomplete` buffer — with
> NO shared parameter and NO test that pins "a thinking pause must not end the turn." Every
> unrelated speech feature touches one of these layers and silently shifts the balance.**

### Mutation timeline (verified commit hashes — "the old mutations for reference")

| Date | Commit | What it changed about endpointing |
|---|---|---|
| ~04-22 | (fossil `pipeline.py:447`) | `vad_silence_ms` raised 350 → 1200ms — the original "patience" decision, baked into a code default. |
| 04-27 | `797c0d17f` | Added `relative_silence_rms_ratio=0.22` RMS-drop endpoint + first `_looks_context_incomplete` starters. |
| 05-09 | `a7adecf78` | Added the STT stability probe + `request_endpoint()`. **This made the probe the primary turn-ender** — the bypass of `silence_ms` was born here. |
| 05-11 | `ce202c6c2` | **BUG-018**: probe ended turns on low Whisper confidence (`< 0.55`) → cut users off mid-sentence. Fix: confidence removed as a standalone trigger. *This is the direct prior instance of the exact bug being reported now.* |
| 05-?? | `c122429a1` | `max_utterance` 12 → 8s; removed `"kannst du"/"can you"` from incomplete-starters (they trapped LISTENING); probe timings tightened to `650/650/1800`. |
| 05-13 | `68cac8905` | Ack-brain suppress-if-fast — runs a parallel task on the hot path; changes event-loop scheduling around `_handle_utterance` (indirect). |
| 05-24 | `5a9afb22a` | `single_turn_mode` → `false` (conversation mode back on). |
| 05-25 | `87b27e0b5` | **(today)** Added `cancel_hysteresis_ms=160` — fixes "never endpoints in noise" but is antagonistic to pause-tolerance (Suspect #3). |
| 05-25 | `939eeb932` | **(today)** Probe refactor: `_probe_stt` can be cloud Groq; `_probe_generation` cross-turn-leak guard; dropped polite-thanks hangup regex. |

### Corrected agent finding (verified false)
A sub-agent flagged commit `17296d5e3` ("accumulate long dictation across VAD max-utterance
cuts") with a `FORCED_CUT_REASONS` enum in `jarvis/audio/vad_reasons.py`. **The commit
exists, but that file and those symbols do NOT exist in the current tree** (`grep` finds
zero `FORCED_CUT_REASONS` / `_last_endpoint_reason`; `jarvis/audio/vad_reasons.py` is
absent). The PCM-carry-across-cuts mechanism it introduced was removed by a later refactor.
This is itself a data point: **a patience feature was added and then silently deleted** —
the exact pattern the user describes.

### Config truth table (verified — note: NOT a config-drift bug this time)

| Parameter | Code default | `jarvis.toml` | `config-soll.json` | ENV | Notes |
|---|---|---|---|---|---|
| `vad_silence_ms` | `1200` `pipeline.py:447` | — | — | — | Hardcoded; drift-guard does not touch it. |
| `max_utterance_s` | `8` `pipeline.py:581` | — | — | — | Hardcoded. |
| `cancel_hysteresis_ms` | `160` `vad.py:45` | — | — | — | Not configurable. |
| `probe_interval_ms` | `650` `pipeline.py:587` | — | — | — | Tuned for **local** Whisper (~300ms). |
| `probe_min_active_ms` | `650` `pipeline.py:588` | — | — | — | |
| `probe_tail_ms` | `1800` `pipeline.py:589` | — | — | — | |
| `_probe_required_stable` | `1` `pipeline.py:606` | — | — | — | One repeat ends the turn. |
| `pending_context_flush_s` | `4.0` `pipeline.py:462` | — | — | — | |
| `single_turn_mode` | `True` `config.py:95` | `false` :25 | `false` :6 | — | Resolved in `desktop_app.py:1209`. |
| `heavy_local_whisper` | `False` `config.py:102` | — | — | — | **In production `self._stt = None`** → probe runs against cloud Groq, not local Whisper. |

**Two real smells from the table:**
1. **Probe runs against cloud STT in production** (`heavy_local_whisper=False` →
   `_probe_stt = self._utterance_stt`, `pipeline.py:544`), but the probe interval (650ms) was
   sized for local Whisper (~300ms). Cloud round-trips (~680ms) can exceed the interval, so
   the `_probe_in_flight` latch (`pipeline.py:845`) throttles probes and their timing drifts.
2. **All endpoint timings are hardcoded defaults** — nothing pins them, nothing tests them.
   The next feature edit can move any of them with no guard rail.

---

## 4. The fix philosophy

Per the "when in doubt, keep listening" mandate, the endpoint decision should bias toward keeping
the mic open. The probe's "empty tail" must distinguish **two genuinely different
situations**, which currently collapse into one:

| Tail state | RMS of tail | Meaning | Correct action |
|---|---|---|---|
| empty transcript, **loud** audio | high | speaker bleed (music/TV) | end turn (probe's real job) |
| empty transcript, **quiet** audio | low | user paused to think | **grace period, keep listening** |

The discriminator is **tail energy (RMS)**, which the VAD already computes per frame
(`vad.py:148`) but the probe never sees. Re-introducing that signal lets us be patient with
real pauses without re-breaking the speaker-bleed cure.

---

## 5. Implementation plan (proposed — TDD, not yet applied)

> Order matters. **Task 1 is the anti-regression centerpiece** and must land first so the
> behavior is pinned before any tuning. Every subsequent task keeps Task 1 green.

### Task 1 — Pin the behavior with a regression test (do this FIRST) — ✅ DONE

> Implemented as `tests/unit/speech/test_thinking_pause_patience.py` (quiet/loud × empty/stable
> = 4 cases) plus a VAD-level case in `test_vad_turn_taking.py`. Watched them fail RED first,
> then go green.

**Files:**
- Create: `tests/unit/speech/test_thinking_pause_patience.py`
- Reference: `jarvis/audio/vad.py:109` (`SileroEndpointer.utterances`), existing
  `tests/unit/audio/test_vad_turn_taking.py` for the synthetic-frame fixture pattern.

- [ ] **Step 1: Write the failing test.** Drive `SileroEndpointer` with a synthetic frame
  sequence: ~800ms speech → ~1500ms genuine silence (low RMS, below `min_speech_rms`) →
  ~800ms speech again. Assert the endpointer yields **one** utterance containing *both*
  speech bursts, i.e. the mid pause did **not** end the turn. Use a fake probe callback that
  mimics the empty-tail case to prove the probe-path is also covered.

```python
# Skeleton — fill RMS/prob values from test_vad_turn_taking.py conventions.
import numpy as np, pytest
from jarvis.audio.vad import SileroEndpointer

@pytest.mark.asyncio
async def test_thinking_pause_does_not_end_turn(monkeypatch):
    ep = SileroEndpointer(silence_ms=1200, max_utterance_s=8)
    # monkeypatch ep._prob to return a scripted prob sequence:
    #   speech(0.9) x25 | silence(0.1) x47 | speech(0.9) x25
    # feed via a fake AudioChunk async-iterator (see existing VAD test helper)
    utterances = [u async for u in ep.utterances(fake_chunks)]
    assert len(utterances) == 1, "thinking pause split the turn — premature endpoint"
```

- [ ] **Step 2: Run it, confirm it FAILS** (current code splits the turn at ~1200ms silence):
  `pytest tests/unit/speech/test_thinking_pause_patience.py -v` → expect FAIL.
- [ ] **Step 3: Commit the red test** (`test(speech): pin thinking-pause patience (currently failing)`).

This test is the scaffolding `CLAUDE.md` mandates for recurring bug classes (cf. the
`hangup_reason` parity test). It is the thing that has been missing — it makes the next
unrelated feature edit fail loudly instead of silently regressing the user.

### Task 2 — Give the probe the tail energy, so it can tell pause from bleed — ✅ DONE

> Implemented with the simpler **defer-to-silence** variant (not the separate grace timer):
> `vad.py` computes `tail_loud` from the tail RMS vs. the existing `relative_silence` floor and
> passes it to the probe; `pipeline.py` gates **both** the empty-tail and stable-tail signals
> on it. Because `tail_loud=False` uses the same threshold as the per-frame silence gate, a
> quiet tail is *guaranteed* to let the natural `silence_ms` endpoint fire — no grace timer
> needed, no hang risk. Original step-by-step proposal kept below for reference.

**Files:**
- Modify: `jarvis/audio/vad.py:228-236` (probe-callback emit — also pass tail RMS)
- Modify: `jarvis/speech/pipeline.py:842-919` (`_on_vad_probe` / `_stt_probe_async` signature
  + `tail_is_empty` branch)

- [ ] **Step 1:** Extend `probe_callback` to receive the tail's mean RMS alongside the PCM
  (compute from `tail` at `vad.py:233` — the frames are already in hand).
- [ ] **Step 2:** In `_stt_probe_async`, when `tail_is_empty` is true, branch on RMS:
  - tail RMS **above** a `speaker_bleed_rms_floor` → `request_endpoint()` immediately (bleed).
  - tail RMS **below** it → do **not** end yet; record a "quiet-empty" timestamp and only
    `request_endpoint()` after `probe_pause_grace_ms` of *continued* quiet-empty tails.
- [ ] **Step 3:** Update the Task-1 test's fake probe to assert the grace path keeps the turn
  open. Run → green.
- [ ] **Step 4:** Commit (`fix(speech): probe distinguishes thinking-pause from speaker-bleed`).

### Task 3 — Consolidate endpoint timings into one configurable block

**Files:**
- Modify: `jarvis/core/config.py` (add a `TurnEndpointConfig` with the params below)
- Modify: `jarvis.toml` (`[turn.endpoint]` section with comments)
- Modify: `jarvis/speech/pipeline.py:572-589` (read from config instead of hardcoded literals)
- Modify: `scripts/config-soll.json` (so the drift-guard pins the patience values too)

- [ ] **Step 1:** Define one source of truth: `silence_ms`, `max_utterance_s`,
  `cancel_hysteresis_ms`, `probe_interval_ms`, `probe_min_active_ms`, `probe_tail_ms`,
  `probe_required_stable`, `probe_pause_grace_ms`, `speaker_bleed_rms_floor`.
- [ ] **Step 2:** Wire `desktop_app.py:1169` and `watchdog.py:124` to pass these through
  (today they pass none → defaults win silently).
- [ ] **Step 3:** Add a parity test asserting the `jarvis.toml` keys, `config.py` fields, and
  `config-soll.json` keys match (the five-layer anti-drift pattern from
  `docs/anti-drift-three-layer.md`).
- [ ] **Step 4:** Commit (`refactor(speech): single source of truth for turn-endpoint timing`).

### Task 4 — Align probe cadence to the real (cloud) STT latency

**Files:**
- Modify: `jarvis/speech/pipeline.py:587` (`probe_interval_ms`)

- [ ] **Step 1:** Since production runs the probe against cloud Groq (`heavy_local_whisper=False`,
  `config.py:102`; `_probe_stt`, `pipeline.py:544`), set `probe_interval_ms` from the active
  STT provider's measured latency (or bump default to ≥ 900ms when `self._stt is None`).
- [ ] **Step 2:** Keep Task-1 test green. Commit.

### Task 5 (OPTIONAL, HANDLE WITH CARE) — broaden semantic patience

**Files:** `jarvis/speech/pipeline.py:293-326` (`_looks_context_incomplete`)

- [ ] Only after Tasks 1-2 land. The history (`c122429a1`, comment `pipeline.py:313-317`)
  proves broadening this traps the pipeline in silent LISTENING. If touched at all, every new
  marker needs a paired test proving a *complete* phrase with the same opener is NOT trapped,
  and the 4s auto-flush (`pipeline.py:2498`) must remain the backstop. **Recommend deferring.**

---

## 6. Verification (Definition of Done)

- [ ] `pytest tests/unit/speech/test_thinking_pause_patience.py -v` — green.
- [ ] `pytest tests/unit/audio/test_vad_turn_taking.py -v` — still green (no bleed regression).
- [ ] `pytest tests/unit/speech/test_probe_cross_turn_leak.py -v` — still green.
- [ ] Live: speak a sentence, pause ~1.5s mid-thought, continue — turn does **not** submit
  during the pause. (`scripts/voice_e2e_probe.py`.)
- [ ] Live: play music, stay silent — turn still ends promptly (speaker-bleed cure intact).

---

## 7. Files involved (quick map)

| File | Role | Hot lines |
|---|---|---|
| `jarvis/audio/vad.py` | Endpoint engine; 3 triggers; cancel-hysteresis | `94`, `146-160`, `182-219`, `228-236`, `240-284` |
| `jarvis/speech/pipeline.py` | Probe logic; config defaults; post-submit buffer | `293-326`, `447-466`, `544`, `572-622`, `842-962`, `2150`, `2430-2535` |
| `jarvis/core/config.py` | `heavy_local_whisper`, `single_turn_mode` defaults | `95`, `102` |
| `jarvis.toml` / `scripts/config-soll.json` | Config layers | `[turn]` §, `single_turn_mode` |
| `tests/unit/audio/test_vad_turn_taking.py` | Existing VAD test (fixture pattern) | — |

---

## 8. One-paragraph summary

The voice pipeline ends turns primarily through an **STT stability probe** that fires the
moment its 1800ms tail transcribes to empty/short — **bypassing the 1200ms silence guard
entirely** (`vad.py:240-248`, `pipeline.py:906-919`). A thinking pause produces the same
empty tail as "user is done", and nothing in the code distinguishes the two. The only
patience layer (`_looks_context_incomplete`, `pipeline.py:293`) runs after the fact on text
and catches just five German conjunctions. The behavior regresses repeatedly because
endpoint patience is scattered across three layers with no shared parameter and **no
regression test** — so every speech feature (most recently today's `cancel_hysteresis_ms`,
`87b27e0b5`) silently shifts the balance. The fix: give the probe the tail's energy so it
can tell a quiet thinking-pause from loud speaker-bleed (Task 2), consolidate the timings
into one tested config block (Task 3), and — first of all — **pin "a thinking pause must not
end the turn" with a failing test** (Task 1) so it can never silently regress again.
