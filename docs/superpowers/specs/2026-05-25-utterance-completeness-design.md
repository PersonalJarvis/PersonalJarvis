# Utterance-Completeness Pre-Processing — Design Spec

**Date:** 2026-05-25
**Branch:** `feat/semantic-hangup-detection`
**Status:** Approved (design), implementation in progress
**Author:** Jarvis / Alex (brainstorming session)

---

## 1. Problem

When the user trails off or aborts mid-dictation ("Open the… uh", "Send a mail
to…", "no, never mind"), the voice pipeline currently risks **executing half a
command**. The existing fragment guard (`_complete_or_buffer_context` +
`_looks_context_incomplete` in `jarvis/speech/pipeline.py`) buffers a dangling
fragment but then **auto-flushes it to the brain after `pending_context_flush_s`
(4 s)** — which *is* the "half command gets executed" failure the user wants to
eliminate.

We want a fast pre-processing classifier, in front of the main agent, that
decides whether a finalized transcript is a **complete actionable instruction**
or an **incomplete / abruptly-aborted utterance**, and reacts accordingly
without ever shipping a half-command to the brain.

## 2. Decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| **Behaviour on non-complete** | Do **not** execute; emit a short signal; stay in `LISTENING`. | Voice-UX best practice: the user hears that they were heard and can simply finish speaking — no silent "I said something, nothing happened". |
| **Classifier bias** | **"When in doubt, execute"** → default verdict is `COMPLETE`. Only *clear* incompletes/aborts trigger. | High precision on "incomplete"; the gray zone flows through. Mirrors the user's "precision over recall" mandate, applied to the incomplete class. |
| **Signal modality** | **Hybrid ("both, depending on context")**: earcon on a fresh/fast turn, spoken cue ("Ja?") when a conversation is already running. Telephony is spoken-only. | Earcon is latency-free and non-annoying on repeat; a spoken cue feels natural mid-conversation. |
| **Architecture** | **A + C**: deterministic classifier (regex/heuristic) + cheap acoustic/timing signals already present in the pipeline. LLM escalation (B) is wired as a config-gated option, default **off**. | A pure-LLM classifier adds latency where the user just told us the gray zone should flow through anyway → low marginal value, high cost. Stays inside the "no LLM on the voice critical path" doctrine (AP-9/AP-11) and runs on a €5 VPS. |
| **Two-turn merge** | **Kept** (core feature). | Enables seamless "finish your sentence": the buffered fragment merges with the next utterance and is re-classified. |
| **Auto-flush to brain** | **Removed.** | This is the actual bug fix. The "never stay mute" guarantee moves from *flush a half-command* to *emit a signal* — better feedback, no half-command. |

## 3. Architecture

A new **stdlib-only shared module** `jarvis/speech/completeness.py`, built exactly
like `jarvis/speech/hangup.py`: no heavy imports, so both the microphone pipeline
(`jarvis/speech/pipeline.py`) and the telephony path
(`jarvis/telephony/session.py`) can import it. It is a **pure, stateless
classifier function** — no LLM, no I/O.

The **classifier is shared**; the **reaction (signal) is surface-specific**
(mic has an earcon channel, telephony does not). This is the same split
`hangup.py` already uses.

### Data flow

```
STT final → text
  │  (existing guards, unchanged: empty · _is_wake_only · HANGUP_RE ·
  │   _STT_HALLUCINATION_RE · privacy phrases)
  ▼
classify_completeness(text, endpoint_reason=…, stt_confidence=…, duration_ms=…)
  ├─ COMPLETE       → merge pending buffer (if any) & re-check → brain / local-action  [downstream unchanged]
  ├─ INCOMPLETE     → append to pending buffer · emit signal (earcon|spoken) · stay LISTENING
  └─ ABRUPT_ABORT   → clear pending buffer · emit short "okay" signal · stay LISTENING
```

The pending buffer **expires only by silent discard** (`pending_discard_s`),
**never by flushing to the brain**. The only way a buffered fragment reaches the
brain is if a subsequent utterance completes it.

## 4. The Classifier (`jarvis/speech/completeness.py`)

```python
class Completeness(str, Enum):        # Python-internal — NOT a wire-format enum
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    ABRUPT_ABORT = "abrupt_abort"

@dataclass(frozen=True, slots=True)
class CompletenessVerdict:
    label: Completeness
    reason: str          # which rule fired — for logs / telemetry

def classify_completeness(
    text: str,
    *,
    lang: str = "de",
    endpoint_reason: str | None = None,   # C-signal: "max_utterance" == was cut at the cap
    stt_confidence: float | None = None,  # C-signal (reserved)
    duration_ms: int | None = None,       # C-signal (reserved)
) -> CompletenessVerdict
```

### Rule precedence (first match wins; default = `COMPLETE`)

1. **Empty / whitespace-only** → `INCOMPLETE` (`reason="empty"`). Defensive; the
   pipeline already guards empty before this call.
2. **Abrupt-abort phrase** → `ABRUPT_ABORT`. Narrow, explicit self-cancel
   phrases only (see list). High precision.
3. **Trailing dangling function word / open-starter** → `INCOMPLETE`
   (`reason="dangling"`). See lists below.
4. **Ends with terminal punctuation `. ! ?`** → `COMPLETE` (`reason="terminal"`).
   Overrides the C-signal: if the user clearly closed the sentence, respect it.
5. **C-signal**: `endpoint_reason == "max_utterance"` **and** no terminal
   punctuation → `INCOMPLETE` (`reason="cut_off"`). A sentence chopped at the
   max-utterance cap with no closing punctuation is a strong cut-off indicator
   that regex alone misses. Conservative: this rule can only *raise* to
   `INCOMPLETE`, never lower to `COMPLETE`.
6. **Default** → `COMPLETE` (`reason="default"`).

### Abrupt-abort phrases (rule 2) — narrow & explicit

- **DE:** `nein egal`, `nein, egal`, `ach egal`, `vergiss es`, `vergiss das`,
  `lass gut sein`, `lass mal gut sein`, `ach nichts`, `ach, nichts`,
  `schon gut`, `ne doch nicht`, `nee doch nicht`, `doch nicht`, `halt stopp` (only when not a running-task cancel — task cancel stays in `voice_command_gate`).  <!-- i18n-allow: German input vocabulary -->
- **EN:** `never mind`, `nevermind`, `forget it`, `forget that`, `nvm`,
  `scratch that`, `no wait`.

> A bare `nein` / `no` is **not** an abort — it is a valid answer. Abort phrases
> are multi-word or unambiguous.

### Trailing dangling tokens (rule 3)

Matched on the **last token** (lowercased, punctuation stripped).

**DE — high-precision set (separable-verb particles AND prepositions deliberately EXCLUDED):**
- Conjunctions: `und`, `oder`, `aber`, `weil`, `dass`, `daß`, `denn`, `sondern`, `sowie`  <!-- i18n-allow: German input vocabulary -->
- Indefinite articles (almost always pre-nominal): `eine`, `einen`, `einem`, `einer`, `eines`  <!-- i18n-allow: German input vocabulary -->
- Subordinators (open a clause that needs completion): `wenn`, `falls`, `ob`

**Explicitly NOT in the DE set** (would cause false positives):
- Separable-verb particles `zu, auf, an, ab, ein, mit, vor, nach, um, in, bei, los, hin, her, weg, da, fest, weiter` — "mach das Fenster **zu**", "mach das Licht **an**" are *complete* commands.  <!-- i18n-allow: German input vocabulary -->
- Bare definite articles / demonstratives `der, die, das, den, dem` — collide with pronouns: "mach **das**", "lass **das**", "nimm **die**" are complete.  <!-- i18n-allow: German input vocabulary -->
- All prepositions `für, von, wegen, mit, …` — collide with question tails: "was ist das **für**", "wo kommst du **her**" are *complete*. Under the "when in doubt, execute" bias they are dropped entirely.  <!-- i18n-allow: German input vocabulary -->ely.

**EN set** (no separable-particle / pronoun collision):
- Conjunctions: `and`, `or`, `but`, `because`
- Articles: `the`, `a`, `an`
- Subordinators: `if`, `when`
- `to` — kept: practically never the final token of a complete command ("remind me **to**", "send it **to**"). Other prepositions (`of`, `for`, `with`) are dropped — they collide with question tails ("what's it **for**", "afraid **of**").

> Subordinators (`wenn`/`falls`/`ob`/`if`/`when`) are matched as trailing tokens,
> so "ich rufe an **wenn**" and a bare "**wenn**" both classify INCOMPLETE.
> `kannst du` / `can you` are intentionally absent — they historically
> false-matched complete questions like "Kannst du das fixen" and froze the
> pipeline mute.

## 5. Reaction & Signal (pipeline-side, surface-specific)

`_complete_or_buffer_context()` is reworked to call `classify_completeness` and
route on the verdict:

- **COMPLETE** → combine pending buffer (if any) + text; re-classify the
  combined candidate; if still COMPLETE, clear the buffer and return the
  combined text for the brain/local-action path. If the *combination* is still
  incomplete, treat as INCOMPLETE (bounded by `max_pending_fragments`).
- **INCOMPLETE** → append fragment to `_pending_user_context`; emit signal; stay
  `LISTENING`; (re-)arm the **discard-only** timer.
- **ABRUPT_ABORT** → clear `_pending_user_context`; emit a short "okay" signal;
  stay `LISTENING`.

**Signal selection ("auto"):** earcon if it is a fresh interaction (no assistant
speech yet in this session / first fragment); spoken cue ("Ja?", `language=lang`,
through `scrub_for_voice`) if the assistant has already spoken in the session.
Telephony always uses the spoken cue (no earcon channel in the call audio).

The existing `TranscriptionUpdate(is_final=False)` event is reused to surface the
buffered partial in the UI (already wire-compatible) — no new wire-format enum.

## 6. Configuration

New block `[speech.completeness]` in `jarvis.toml`, modelled in
`jarvis/core/config.py` (mind `extra="allow"`, AP-16). All writes go through
`config_writer` (AP-7).

```toml
[speech.completeness]
enabled = true
signal_mode = "auto"          # auto | earcon | spoken
pending_discard_s = 8.0       # replaces the auto-flush — DISCARD only, never flush to brain
max_pending_fragments = 2
llm_escalation_enabled = false  # Approach B (gray-zone LLM check) — reserved, OFF
```

## 7. Error handling (fail-open)

- The classifier never raises on bad input; any internal exception is caught by
  the call site and treated as **`COMPLETE`** (fail-open = "when in doubt,
  execute"; mirrors AD-OE6 "zero silent drop / never mute").
- Signal emission (TTS or earcon) failure → log, swallow, still stay
  `LISTENING`. A signal bug must never crash the turn or mute the pipeline.
- The discard timer reuses the existing cancel/reschedule pattern
  (`_cancel_pending_flush` / `_schedule_pending_flush`).

## 8. Telemetry / enum-drift

`Completeness` stays **Python-internal** in v1. UI "incomplete" rendering reuses
the existing `TranscriptionUpdate(is_final=False)` event rather than pushing a new
enum across SQL/TS/UI — deliberately avoiding the BUG-008 multi-layer enum-drift
trap. If the verdict ever needs to reach SQL/TS, apply the five-layer pattern
(`docs/anti-drift-three-layer.md`) + a parity test at that point.

## 9. Tests (TDD)

**Unit — `tests/unit/speech/test_completeness.py`** (this milestone):

- **COMPLETE** (must pass through to the brain/fast-path):
  - "Öffne Chrome", "Öffne mir den Browser", "Wie spät ist es",  <!-- i18n-allow: German test-fixture utterances -->
    "Spiel Spotify ab", "Mach das Fenster zu", "Mach das Licht an", "Mach das",
    "Lass das", "Nimm die rote", "Gib mir den Bericht",  <!-- i18n-allow: German test-fixture utterances -->
    "Was ist das für" (preposition-tail collision — locked to COMPLETE),  <!-- i18n-allow: German test-fixture utterances -->
    "What is this for" (same collision in EN),
    "Schreib eine Mail an Tom dass ich später komme",  <!-- i18n-allow: German test-fixture utterances -->
    "Kannst du das fixen" (historic false-positive — locked to COMPLETE),
    "What time is it", "Open the terminal", "Turn it off",
    any text ending in `.` / `!` / `?`.
- **INCOMPLETE**:
  - "Öffne mal eine", "Ich brauche einen", "Kauf Milch und",  <!-- i18n-allow: German test-fixture utterances -->
    "Ich glaube dass", "Jarvis wenn", "wenn", "falls",  <!-- i18n-allow: German test-fixture utterances -->
    "Send a mail to", "I want to", "Open the".
- **ABRUPT_ABORT**:
  - "nein, egal", "vergiss es", "ach, lass gut sein", "schon gut",
    "never mind", "forget it", "scratch that".
- **C-signals**:
  - "ich möchte dass du die Datei" with `endpoint_reason="max_utterance"`  <!-- i18n-allow: German test-fixture utterances -->
    → INCOMPLETE (`reason="cut_off"`); same text with terminal punctuation
    → COMPLETE; "Öffne Chrome." with `endpoint_reason="max_utterance"`  <!-- i18n-allow: German test-fixture utterances -->
    → COMPLETE (terminal overrides).
- **Bias** (ambiguous → COMPLETE): "das Wetter heute", lone content word
  "Browser".
- **Regression guard:** every command literal used in the existing
  routing / local-action tests classifies as `COMPLETE` (no fast-path
  regression).

**Integration / pipeline (follow-up milestone, not this commit):**
- INCOMPLETE → stays `LISTENING`, no brain call, signal emitted.
- ABRUPT_ABORT → pending buffer cleared.
- Two-turn completion merges across turns.
- **Bug regression:** no timer path ever flushes a half-command to the brain.

**Must not break:** `test_thinking_pause_patience.py`, hangup parity, routing
(26-case), output_filter (40-case).

## 10. Affected files

1. `jarvis/speech/completeness.py` — **new** (classifier). *(this milestone)*
2. `tests/unit/speech/test_completeness.py` — **new** (TDD). *(this milestone)*
3. `jarvis/speech/pipeline.py` — rework `_complete_or_buffer_context`, remove
   auto-flush-to-brain, add signal emission, thread VAD signals. *(follow-up)*
4. `jarvis/telephony/session.py` — use classifier + spoken cue. *(follow-up)*
5. `jarvis/core/config.py` — `[speech.completeness]` model. *(follow-up)*

## 11. Out of scope (v1)

- LLM gray-zone escalation (Approach B) — config flag present, default off.
- Surfacing the `Completeness` verdict to SQL/TS/UI as a typed enum.
- Acoustic-only abort detection beyond `endpoint_reason` (`stt_confidence` /
  `duration_ms` are accepted as parameters but reserved for a later tuning pass).
