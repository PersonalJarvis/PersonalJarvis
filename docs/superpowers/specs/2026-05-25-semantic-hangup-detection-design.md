# Semantic Hang-Up Detection — Design

**Date:** 2026-05-25
**Status:** Draft (awaiting maintainer review of committed spec)
**Author:** Claude (brainstorming session)
**Branch:** `feat/semantic-hangup-detection`

---

## 1. Problem

Today Jarvis only ends a voice session reliably when the user says one of a fixed
list of literal phrases ("auflegen", "leg auf", "tschüss", "bye jarvis", …). The  <!-- i18n-allow: German input vocabulary -->
user wants Jarvis to **understand the intent to end the conversation**, not just
match a keyword — e.g. "Kannst du jetzt bitte gehen", "that's all for today,
thanks", "ich glaube wir sind durch". This must work in both configured
languages (German **and** English) and on **both** voice surfaces (the desktop
microphone pipeline and Twilio telephony).

## 2. Current state (deep-dive findings)

There are **two** voice surfaces, each with its own hang-up logic, and a
semantic path already exists but is fragile.

### 2.1 Microphone pipeline — `jarvis/speech/pipeline.py`

Two layers:

1. **Regex, pre-brain** — `HANGUP_PATTERNS` / `HANGUP_RE` (`pipeline.py:125-180`).
   ~45 literal patterns matched against the transcript *before* the brain is
   called (`pipeline.py:2224`). Fast and deterministic, but purely literal and
   German-heavy; English entries exist mostly as Whisper mis-transcription
   catches. Includes ambiguous-polite entries (`\bvielen\s+dank\b`,
   `\bdanke jarvis\b`, `\bexit\b`, `\bquit\b`, `\bbeenden\b`) that can
   false-fire.
2. **Brain signal, post-brain** — the persona prompt
   (`jarvis/brain/JARVIS_PERSONA.md:95-98`) instructs the brain: *"When the
   conversation ends or Alex dismisses you, reply with EXACTLY one of: EN:
   Goodbye, Alex. / DE: Auf Wiedersehen, Alex."* The pipeline then does an
   **exact normalized string compare** of the brain's response
   (`pipeline.py:2351` streamed, `pipeline.py:2432` non-streamed) and triggers
   hang-up on a match.

### 2.2 Telephony — `jarvis/telephony/session.py`

A self-contained, smaller regex copy (`session.py:69-88`, deliberately *not*
importing `pipeline.HANGUP_RE` to avoid the forbidden `sounddevice` import).
Matched pre-brain at `session.py:231`. **No brain-signal path.**

### 2.3 Key insight

The semantic mechanism (layer 2) is the right idea — an LLM is the correct tool
to understand "Kannst du jetzt bitte gehen" — but it is coupled to an **exact
magic string**. If the brain paraphrases ("Auf Wiedersehen, Alex! War mir ein  <!-- i18n-allow: quoted German voice-output example -->
Vergnügen."), the exact match fails and no hang-up occurs. The fix is to make  <!-- i18n-allow: quoted German voice-output example -->
that path **robust**, not to add a new classifier.

Both surfaces share the same brain: telephony uses
`build_default_brain(tier="router")` (`telephony_routes.py:346,714`), the same
factory the mic path uses, and `BrainManager` loads `JARVIS_PERSONA.md`
(including the hang-up contract) into the system prompt
(`manager.py:813-817`). **One persona change therefore reaches both languages
and both surfaces.**

### 2.4 Doctrine constraints

- **No extra LLM call on the voice path** (AP-9, AP-11; latency mandate). The
  solution must ride the brain call that already happens — no separate
  classifier round-trip.
- **No new heavy dependency** (cloud-first €5-VPS doctrine) — rules out a local
  embedding/intent model.
- **Five-layer enum discipline** for any new wire-format value (BUG-008). We
  avoid this risk by reusing the existing `HANGUP_VOICE_PATTERN` reason.

## 3. Product decisions (confirmed with maintainer)

- **Bias under uncertainty: stay on.** Precision over recall. A false hang-up
  mid-conversation is worse than a missed one (the user can always say
  "auflegen"). The brain ends the call only on a *clear* dismissal; ambiguous
  input keeps the session open.
- **Scope: both surfaces** — microphone pipeline and Twilio telephony.
- **Languages: German + English.**

## 4. Chosen approach: robust brain-emitted control signal

Decouple *deciding to end* (semantic — the LLM's job) from *what is said* (the
farewell wording). The brain appends a robust control sentinel to its reply when
it judges the user wants to end. The pipeline detects the sentinel anywhere in
the response and strips it before TTS, so the brain may say anything natural.

Rejected alternatives:

- **Dedicated `end_conversation` tool call** — requires amending `ROUTER_TOOLS`
  + ADR-0011, adds a tool round-trip (latency), complicates the streaming path,
  and the telephony brain consumes a plain text stream. Overkill.
- **Local embedding/intent classifier pre-brain** — new heavy dependency
  (violates cloud-first), threshold-tuning pendulum risk (BUG-009 class), and
  the brain is already a superior, free semantic classifier.

## 5. Architecture

### 5.1 New module — `jarvis/speech/hangup.py`

Standard-library only (`re`), no `sounddevice` import, importable by both the mic
pipeline and telephony. `jarvis/speech/__init__.py` is empty, so importing
`jarvis.speech.hangup` does **not** pull in the heavy pipeline module. This is
the single source of truth, replacing the two divergent regex copies.

Exports:

- `HANGUP_RE` — unified bilingual **explicit-command** regex (DE + EN). Cleaned
  up: ambiguous-polite phrases (bare "vielen dank", "danke jarvis", "exit",
  "quit", "beenden") are **removed** from the instant regex and delegated to the
  brain, which has conversational context and can honor "stay on" when unsure.
  Unambiguous commands stay ("auflegen", "leg auf", "hang up", "goodbye jarvis",
  "good night", …).
- `END_CALL_SIGNAL = "[[END_CALL]]"` — the control sentinel.
- `contains_end_signal(text: str) -> bool` — substring detection.
- `strip_end_signal(text: str) -> str` — removes the sentinel and trailing
  whitespace, safe to call on partial stream chunks and the full response.
- `LEGACY_FAREWELL_PHRASES` + `is_legacy_farewell(text: str) -> bool` — backward
  compatibility for the old exact phrases ("auf wiedersehen, alex", "goodbye,
  alex", …) so older brain behavior still hangs up during rollout.
- `BRAIN_HANGUP_INSTRUCTION` — the canonical instruction text (DE + EN) that the
  persona references, keeping the contract in one place.

### 5.2 Edits

- **`jarvis/speech/pipeline.py`** — import `HANGUP_RE`, `contains_end_signal`,
  `strip_end_signal`, `is_legacy_farewell` from `hangup.py`; delete the inline
  `HANGUP_PATTERNS`/`HANGUP_RE`. Replace the two exact-match blocks
  (`:2351`, `:2432`) with `contains_end_signal(response) or
  is_legacy_farewell(normalized)`. Strip the sentinel before any TTS enqueue
  (both the streaming sentence-splitter path and the non-streaming `_speak`
  path).
- **`jarvis/telephony/session.py`** — import `HANGUP_RE` from `hangup.py`
  (drops the local copy); add sentinel detection inside the
  `brain.generate_stream` loop (`:268`) — accumulate, strip the sentinel from
  chunks before TTS, and on `contains_end_signal` call
  `end(reason=..., status=CALL_COMPLETED)` after the farewell is spoken.
- **`jarvis/brain/JARVIS_PERSONA.md`** — replace the exact-farewell contract
  (`:18-25`, `:95-98`, `:133`) with the sentinel contract: the brain says a
  natural, short farewell and appends `[[END_CALL]]`. Add the conservative-bias
  rule: emit the sentinel **only** on a clear intent to end (explicit goodbye,
  dismissal, "you can go", "that's all for today"); when unsure, do not emit —
  keep talking or briefly ask. Bilingual examples (DE + EN).
- **`jarvis/brain/output_filter.py`** — `scrub_for_voice` strips
  `END_CALL_SIGNAL` as defense-in-depth, so the token can never reach TTS even
  if a detection site is missed.

### 5.3 Hang-up reason

Reuse `HANGUP_VOICE_PATTERN` (`jarvis/sessions/constants.py:33`) for the mic
path — its docstring already covers "or it was inferred from a closing intent".
For telephony, reuse the existing reason constant used at `session.py:232`
(`"hangup_phrase"`). **No new enum value** → no five-layer-drift work, no BUG-008
risk. (Optional, non-blocking: log the trigger source — `regex` vs `brain` — as a
trace field for telemetry, without introducing a new wire enum.)

## 6. Data flow (identical shape on both surfaces)

```
transcript
  │
  ├─ HANGUP_RE matches (explicit command)? ── yes ──► instant hard hang-up
  │                                                    (player.stop(); "auflegen"
  │                                                    stays an absolute kill switch)
  │
  └─ no → brain response
            │
            ├─ contains_end_signal()  or  is_legacy_farewell()? ── yes ──►
            │        strip sentinel → speak farewell → graceful hang-up
            │        (stop_player=False, the goodbye is heard)
            │
            └─ no → normal reply, keep listening   ← conservative "stay on"
```

## 7. Latency

Zero added latency. The regex layer already exists; sentinel detection rides the
brain response that is generated regardless. No new LLM or network call.
Satisfies AP-9 / AP-11.

## 8. Error handling / edge cases

- **Sentinel mid-stream:** the streaming path strips it from each chunk before
  TTS; full-response detection still fires the hang-up. The farewell text is
  spoken normally.
- **Brain emits sentinel but no farewell text:** speak a minimal localized
  farewell fallback, then hang up (never silent — AD-OE6).
- **STT mis-transcription of "auflegen" as polite phrases:** previously caught
  by the over-broad regex; now the brain handles these with context. Because the
  brain sees the full turn, "vielen Dank, und mach mir noch X" correctly does  <!-- i18n-allow: quoted German voice-input example -->
  *not* end the call.
- **Negative cases that must NOT hang up:** "kannst du das nochmal machen", "geh
  mal auf die Seite", "danke, und jetzt …" — covered by tests.  <!-- i18n-allow: quoted German voice-input example -->

## 9. Testing

- **`tests/unit/speech/test_hangup.py`** (new) — DE + EN explicit commands match;
  ambiguous-polite phrases do **not** match `HANGUP_RE`; `contains_end_signal` /
  `strip_end_signal` round-trip (incl. partial chunks); `is_legacy_farewell`
  backward compat; negative cases.
- **`tests/unit/speech/`** — pipeline integration: response with `[[END_CALL]]`
  triggers `_trigger_voice_hangup(stop_player=False)`; sentinel never reaches
  `_speak`.
- **`tests/unit/telephony/test_session.py`** — extend: brain stream containing
  the sentinel ends the call after the farewell; sentinel stripped from outbound
  TTS.
- **`tests/unit/brain/test_output_filter.py`** — `scrub_for_voice` removes
  `END_CALL_SIGNAL`.

## 10. Out of scope

- New voice surfaces beyond mic + telephony.
- Languages beyond DE + EN (the design is language-agnostic at the regex/brain
  level; adding a language is a persona + regex addition later).
- A dedicated telemetry enum for "semantic vs explicit" hang-up (optional trace
  field only).

## 11. Open questions

None blocking. The sentinel string `[[END_CALL]]` is an implementation detail;
if a collision with real user text is ever observed, swap to a rarer token in
`hangup.py` (single source of truth).
