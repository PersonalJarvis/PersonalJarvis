# Incomplete-Prompt Completion Buffer — Design Spec

**Date:** 2026-05-25
**Branch:** `feat/semantic-hangup-detection` (sibling feature to the `[[END_CALL]]` work)
**Status:** Approved design — ready for implementation plan
**Scope:** Voice path only (microphone pipeline). Telephony and typed chat are out of scope for v1.

---

## 1. Problem

The voice pipeline finalizes a turn the moment the turn-taking layer decides the
user stopped speaking (VAD `silence_ms` / thinking-pause-patience / endpoint
probe). That decision is **acoustic**: it answers *"did the user stop making
sound?"*, not *"did the user finish the thought?"*.

A user dictating freely produces utterances that are acoustically complete but
**semantically dangling**:

> "Erinnere mich morgen daran, dass" ("Remind me tomorrow that") … *[silence, turn ends]*

Today this fragment goes straight to the brain, which either guesses or asks a
clarifying question, breaking the flow of a sentence the user was mid-way through
speaking. We want Jarvis to recognize the obviously-unfinished case, hold the
fragment, stay silent, and stitch it together with the next utterance into one
complete prompt.

### What this is NOT (boundary)

Three mechanisms already handle *acoustic* incompleteness — a user pausing
**within** a single breath/turn — and stay untouched:

- VAD `silence_ms` turn endpoint (`jarvis/audio/vad.py`)
- thinking-pause-patience (defer endpoint on a quiet tail — `tail_loud`)
- VAD cancel-hysteresis (absorb short ambient/bleed spikes)

This feature is a **new, higher layer**: it runs *after* the turn has already
ended and the transcript is final, classifying the *content* as syntactically
open-ended.

---

## 2. Architecture Decision: deterministic heuristic classifier

| Approach | Latency | Precision | Verdict |
|---|---|---|---|
| **A. Deterministic heuristic** — regex on dangling sentence endings, mirroring `HANGUP_RE` | ~0 ms | High — fires only on unambiguous open markers | **Chosen for v1** |
| B. Brain appends `[[INCOMPLETE]]` sentinel (mirroring `[[END_CALL]]`) | +1 full deep-brain round-trip | Probabilistic | Documented backstop / future |
| C. Dedicated flash-brain classifier (Ack-Brain tier) | +sub-second on **every** turn | Medium | Too costly for a rare case |

**Decision: A.** The user mandate is **"answer when in doubt" (precision over
recall)**. A narrow rule list fires *only* on syntactically unambiguous open
endings, so any prompt that is even slightly complete goes straight to the brain.
This is precise by construction and costs microseconds, preserving the latency
SLO (`intent→ACK p95 < 3.0 s`).

This is the same design principle `hangup.py` already follows: match only
unambiguous commands deterministically, delegate everything ambiguous to the
brain. The completion classifier is its structural twin.

`[[INCOMPLETE]]` (approach B) is recorded as a documented extension point should
recall prove too low in practice. It is explicitly **not** in v1.

---

## 3. Components (each independently testable)

### 3.1 `jarvis/speech/completion.py` (new, stdlib-only)

Single source of truth for dangling detection. Imports nothing heavy
(no `sounddevice`), so the path stays cheap and the module is unit-testable in
isolation — mirroring the `hangup.py` constraints.

Public API:

```
is_incomplete(text: str, language: str = "") -> IncompleteVerdict | None
is_cancel(text: str) -> bool
```

`is_incomplete` returns a small frozen `IncompleteVerdict` (carrying a `reason`
string for telemetry) when the transcript ends on an unambiguous open marker,
else `None`. It fires when the **trailing token(s)** are:

- **Subordinating / coordinating conjunctions:** `dass, weil, damit, sodass,  <!-- i18n-allow: German input vocabulary -->
  sobald, wenn, falls, ob, und, oder, aber, denn, sondern, dann`  <!-- i18n-allow: German input vocabulary -->
  (EN mirror: `that, because, so that, and, or, but, then`)
- **Prepositions expecting an object:** `mit, für, an, auf, in, zu, von, über,  <!-- i18n-allow: German input vocabulary -->
  bei, durch, gegen, ohne, …` (EN: `with, for, to, about, at, …`)
- **Articles / determiners expecting a noun:** `der, die, das, ein, eine, einen,  <!-- i18n-allow: German input vocabulary -->
  dem, den, mein, meine, meinen, …` (EN: `the, a, an, my, …`) <!-- i18n-allow: German input vocabulary -->
- Trailing `dass ich` / `weil ich` style subject-without-predicate tails.  <!-- i18n-allow: German input vocabulary -->

`is_cancel` matches abort phrases that discard a pending fragment:
`vergiss das, ach nein, lass stecken, never mind, schon gut`.  <!-- i18n-allow: German input vocabulary -->

Because STT frequently drops terminal punctuation, the classifier keys on
trailing-token morphology, not on the presence/absence of a period. A short
minimum-length guard avoids treating a one-word utterance as a dangling fragment.

The marker lists live as module-level constants so they are tunable in one place.
(This is an internal heuristic, not a cross-layer wire enum, so the five-layer
anti-drift pattern does not apply — but the single-source-of-truth discipline
does.)

### 3.2 `PendingPromptBuffer` (new, single-slot)

A dedicated, minimal buffer — **not** an extension of `TurnBuffer`
(`jarvis/speech/turn_buffer.py`), which owns a separate job (the rolling window
for the "nein, ich meinte X" ("no, I meant X") correction command). Single responsibility each.

State held: `fragment_text`, `language`, `started_ns`, `chain_count`.
Methods: `store`, `append`, `flush`, `clear`, `is_pending`.

Lives on the `SpeechPipeline` instance, session-scoped (cleared on session
end / hangup, like the other per-session voice state).

### 3.3 Orchestration in `_handle_utterance` (`jarvis/speech/pipeline.py`)

The most delicate part, because this is the path with many historically-silent
return branches (BUG-007 / BUG-020 / BUG-028). A new enum value is added to
`TurnTakingState` (pipeline.py:173):

```
WAITING_FOR_COMPLETION = "WAITING_FOR_COMPLETION"
```

It sits conceptually between `PROCESSING` and `JARVIS_SPEAKING` — the point where
the prompt is finalized but content-incomplete. The state is driven through the
existing `_set_turn_state` / `_schedule_turn_state` machinery (pipeline.py:825 /
:848) so the Orb/UI receives the hint via the existing state-change emit.

---

## 4. Flow

```
Final transcript ready
   │
   ├─ Already in WAITING_FOR_COMPLETION? ───────────────► [CONTINUATION]
   │
   ├─ Hangup command?         ──► flush buffer→brain (if any), then hang up
   ├─ Wake-only / hallucination? ──► ignore (existing filters, unchanged)
   │
   ▼
 [CLASSIFIER]  is_incomplete(text)?
   │
   ├─ NO / unsure ──► normal brain call            ◄── "answer when in doubt"
   │
   └─ YES (clearly open) ──► buffer.store(text)
                             state = WAITING_FOR_COMPLETION
                             start completion timeout (~8 s)
                             STAY SILENT, mic open, Orb shows "…waiting"  ◄── no TTS

[CONTINUATION]  (next utterance while WAITING_FOR_COMPLETION)
   ├─ Hangup?        ──► flush buffer→brain, hang up
   ├─ Cancel phrase? ──► discard buffer, back to LISTENING
   ▼
   joined = buffer.fragment + " " + new_text
   reclassify(joined)
   ├─ now complete           ──► brain.call(joined); buffer.clear()
   ├─ still open & chain < MAX(3) ──► buffer.append(joined); keep waiting (reset timer)
   └─ chain >= MAX           ──► flush joined→brain (never infinite)

[TIMEOUT]  (no continuation within the window)
   └─ flush buffer→brain anyway — NEVER silently discard
                                        ◄── AD-OE6 / BUG-020: zero silent drops
```

---

## 5. Non-negotiable safety bindings

1. **Timeout-flush, never a silent drop (AD-OE6).** If the user never completes,
   the buffered fragment is sent to the brain after the timeout (the brain then
   asks for the rest). Silent discard *is* the BUG-020 class and is forbidden.
2. **Hangup always wins.** "Auflegen" ("hang up") during the wait hangs up immediately
   (after flushing any pending fragment). The hard kill-switch contract is
   unchanged (see the `auflegen` hard-kill-switch memory).
3. **Bounded chain + precision gate.** Max 3 concatenations, then forced flush.
   The classifier fires only on unambiguous markers, so a complete prompt never
   enters the waiting state.

---

## 6. Timeout rationale (`completion_wait_ms`, default 8000)

The timeout is the **per-gap** budget, not a total. Every continuation resets the
timer, and the Max-Chain of 3 absorbs piecewise dictation such as:
"…dass ich… [pause] …wenn ich im Büro bin… [pause] …die Müllers anrufe"  <!-- i18n-allow: quoted German voice-input example -->
("…that I… [pause] …when I'm in the office… [pause] …call the Müllers").  <!-- i18n-allow: quoted German voice-input example -->
Only the *first* formulation pause (typically the longest) actually races the
clock.

Default **8 s** (a comfortable first-pause budget; raise to 10 s if the user
self-identifies as a slow formulator). Even a too-short value loses nothing: the
timeout-flush degrades to a natural brain follow-up question, and the session
stays open (conversation mode, 30 s idle). The value is a one-line config change.

---

## 7. Configuration (`[voice]` section, `jarvis.toml`)

| Key | Default | Meaning |
|---|---|---|
| `completion_detection_enabled` | `true` | Master switch for the feature |
| `completion_wait_ms` | `8000` | Per-gap wait before timeout-flush |
| `completion_max_chain` | `3` | Max concatenations before forced flush |

Added under `ConfigDict(extra="allow")`-covered config so a self-mod / drift-guard
write cannot reject boot (AP-16). Reads go through the existing config object;
no hardcoded constants in the pipeline.

---

## 8. Edge cases

- **Continuation is itself a hangup** → flush + hang up (hangup precedence).
- **Continuation is a new complete thought / topic change** → the joined text
  re-classifies as complete and goes to the brain as a whole; acceptable because
  the precision gate makes a false "incomplete" on the *first* fragment rare.
- **Continuation is a cancel phrase** → discard the buffer, return to LISTENING.
- **Chained dangling** → bounded by `completion_max_chain`, then forced flush.
- **Feature disabled** → classifier is skipped entirely; behaviour identical to
  today (regression-guarded).
- **Wake-only / STT-hallucination follow-ups** while waiting → existing filters
  run first; a hallucination does not count as a continuation.

---

## 9. Telemetry & logging

Log (and emit a lightweight event where the bus already carries turn events):
- entering `WAITING_FOR_COMPLETION` (with `reason` from the verdict),
- each concatenation (`chain_count`),
- timeout-flush vs. completed-by-continuation outcome.

This lets us measure the **false-positive rate** in practice — precision is the
stated goal, so we must be able to observe when a complete prompt was wrongly
held (it should be near-zero).

---

## 10. Testing strategy

Pinned regression suite, in the spirit of the prior voice-turn fixes:

`tests/unit/speech/test_completion_detection.py` (classifier in isolation):
- clear dangling DE/EN markers → verdict returned
- complete sentences (incl. borderline polite/short) → `None` (precision guard)
- cancel phrases → `is_cancel` true

Pipeline integration tests:
- dangling → enters WAITING, **no TTS emitted**
- complete → answers immediately (**latency-regression guard**: no extra hop)
- continuation → correctly concatenated and sent once
- **timeout → flush to brain (no silent drop)** ← the critical safety test
- continuation == hangup → hangs up
- chain > MAX → forced flush
- feature disabled → identical to baseline

---

## 11. Blast radius

One new stdlib module (`completion.py`), one single-slot buffer
(`PendingPromptBuffer`), one new `TurnTakingState` value, one new branch in
`_handle_utterance`, three `[voice]` config keys. The existing VAD /
thinking-pause / hangup paths are untouched.

---

## 12. Out of scope (v1)

- Typed-chat surface (voice only).
- Telephony surface (`jarvis/telephony/`).
- The `[[INCOMPLETE]]` brain-sentinel backstop (documented extension point in §2).
- Cross-session persistence of a pending fragment.
