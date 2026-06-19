# Voice continuation recombine while thinking — design

**Date:** 2026-06-16
**Status:** Approved (design); spec pending user review
**Area:** `jarvis/speech/pipeline.py`, `jarvis/audio/vad.py`, `jarvis/brain/manager.py`
**Related:** `jarvis/speech/continuation_buffer.py` (pre-dispatch sibling), AD-OE1/OE5/OE6,
the voice auto-submit memory line (delegation-composition patience),
`docs/BUGS.md` (BUG-032 watchdog class, the auto-submit class).

---

## 1. Problem

When the user keeps talking *after* an utterance has already been dispatched to the
brain — i.e. the brain is already in its "thinking" phase — the continuation is
treated as a brand-new, independent turn. The earlier (truncated) part is lost as
context.

Concrete repro the maintainer reported (2026-06-16):

1. User says *"Hallo hallo hallo, ich möchte nach"* — VAD endpoints, it is dispatched.
2. The brain enters its thinking phase (`PROCESSING`).
3. User continues *"…Griechenland"*.
4. The brain answers as if the message were only *"Griechenland"* — the first half is gone.

The user's words: *"dann fängt's wieder an … eine neue Nachricht an ihn schicken"* —
the continuation becomes a fresh message that drops the earlier context, and "it
feels dumber".

## 2. Why it happens (current behavior, verified in code)

- `SpeechPipeline._active_session` (`pipeline.py:3258`) runs one main microphone and
  pulls utterances from the VAD generator. It `await`s `_handle_utterance` **fully**
  (`pipeline.py:3337`) — STT → brain → TTS — before pulling the next utterance.
- There is already a `ContinuationBuffer` (`continuation_buffer.py`) that coalesces a
  *syntactically* open-ended fragment (trailing conjunction / preposition / comma)
  **before** dispatch. It does NOT help here, because by the time the user continues,
  the fragment was already classified as complete-enough and dispatched.
- During the pure thinking phase there is **no microphone listener**. The
  `_barge_monitor` (`pipeline.py:5857`) only runs *inside* `_speak` (during TTS
  playback) and only **detects** speech energy on a second mic — it does not capture
  or transcribe. So the user cannot interrupt the *thinking* today; their continuation
  is buffered by the OS mic stream and surfaces as the **next** utterance once the
  whole turn (including any spoken answer) is over.
- That next utterance runs a fresh `_handle_utterance_turn` → fresh brain turn. The
  earlier text is not re-attached.

**Key enabling fact for the fix:** the brain commits the conversation history
**atomically at the end of a turn** — `generate()` appends the `user` and `assistant`
messages together as its last step (`manager.py:4116-4117`; the non-streaming twin at
`3048-3049`). The streaming voice path (`generate_stream`, `manager.py:4253`) delegates
to `generate()` via a `_producer` task that is cancelled in `finally`
(`manager.py:4345-4351`) when the consumer is cancelled. Therefore: **cancelling the
brain turn before it finishes reliably skips the history commit** — the truncated first
half never pollutes history.

## 3. Goals / Non-goals

**Goals**
- When the user speaks again while the brain is still thinking or speaking, **abort**
  the half-formed answer (never speak it) and re-think the **combined** sentence as one
  turn. (Maintainer choice Q1: "Denken abbrechen & neu".)
- The continuation window also covers a **short grace period** after the answer finished
  (~2.5 s). (Maintainer choice Q2: "Denken/Sprechen + kurze Nachfrist".)
- No truncated/duplicate user message survives in the brain history.
- Bounded: a capped continuation chain and a wall-clock window — never an infinite buffer
  or a live-lock (same discipline as `ContinuationBuffer`).
- Entirely opt-out-able via config; degrades to today's behavior when disabled.
- Zero added latency on the happy path (no continuation) — AP-9/AP-11 discipline.

**Non-goals**
- Not changing the pre-dispatch `ContinuationBuffer` behavior (it stays as the first
  line of defense for syntactically open fragments).
- Not rewriting the main `_active_session` loop to consume the VAD concurrently with the
  brain turn. That is the most regression-prone path (the auto-submit bug class lives
  there); we deliberately reuse the proven second-mic monitor pattern instead.
- Not solving multi-speaker / cross-talk disambiguation. Within thinking/speaking we
  treat the user's own continued speech as a continuation.

## 4. Design

Three cooperating units. The first is the actual context repair; the second makes the
"abort the thinking" UX real; the third keeps the history clean.

### 4.1 Unit A — Continuation window (the coalescer)

New per-turn state on `SpeechPipeline`, guarded by `getattr` defaults so the test
fixtures that build the pipeline via `__new__` keep working (existing convention):

- `_continuation_text: str` — the last dispatched user text eligible to be extended.
- `_continuation_deadline_ns: int | None` — when the window closes.
- `_continuation_chain: int` — how many fragments have been coalesced so far.
- `_continuation_committed: bool` — whether the turn that armed the window already
  reached the brain history commit (drives the `drop_last_turn` fallback in Unit C).

Lifecycle:

- **Arm** at the single dispatch commit point in `_handle_utterance_turn` — right where
  the turn transitions to `PROCESSING` (`pipeline.py:4100`), after `text` is final and
  after the skill-direct / privacy / wake-only / hangup guards have passed. Store the
  dispatched `text`, set `deadline = now + (turn lifetime)`, `chain = 1`,
  `committed = False`.
- **Keep armed** through `PROCESSING` and `JARVIS_SPEAKING`. When the turn finishes
  (answer fully spoken, or aborted), set `deadline = now + continuation_grace_ms`.
- **Mark committed** when the brain turn actually committed history (turn completed
  normally without being aborted).
- **Disarm** when: the deadline passes (checked lazily on the next utterance, like
  `ContinuationBuffer`), on hangup / `is_cancel(text)` ("vergiss das"), on a wake-only
  turn, or once `chain >= continuation_max_chain` (then the next utterance is a fresh
  turn). Re-armed (with the combined text, `chain += 1`) after a successful recombine.

### 4.2 Unit B — Thinking-phase interrupt monitor

Mirror of `_barge_monitor`, but active during the **thinking** phase, not only during
playback.

- Started as a task when the brain turn begins (in the streaming brain path,
  `_brain_streaming` / `_run_brain_with_stall_guard`, alongside the existing produce/
  play tasks), covering the pre-first-TTS-frame window. During playback the existing
  per-`_speak` `_barge_monitor` already covers interruption — Unit B does not duplicate
  that; it fills the gap *before* the first frame.
- On detecting clear, sustained user speech it **cancels the in-flight brain consumer**.
  Cancellation propagates to `generate_stream`'s `finally` → the `_producer` task →
  `generate()`, which is aborted before its history append. The turn ends with an
  "interrupted" signal (reuse the `barged` return channel so `_finish_after_response`
  stays `LISTENING`).
- Echo robustness: reuse `_barge_monitor`'s heuristics (Silero threshold, consecutive-
  frame run) and honor the existing TTS echo-suppression window
  (`_input_suppressed_until_ns`) — critical because the **ack-brain preamble**
  ("Researching…") can be playing during the thinking phase via `_on_announcement`.
  When the preamble is playing, the same conservative thresholds the barge monitor uses
  apply; when nothing is playing, a shorter grace is acceptable (no speaker→mic echo).
- Fail-safe: any exception in the monitor is swallowed and treated as "no interrupt"
  (AP-18 spirit) — it must never crash or wedge the turn.
- Bounded by the same brain stall/hard-timeout ceiling already in place; the monitor is
  cancelled as soon as the turn produces its answer / first frame.

### 4.3 Unit C — Recombine on the next utterance

In `_handle_utterance_turn`, after STT finalize and after the existing
`ContinuationBuffer.process`, before the privacy/skill/brain dispatch:

- If the continuation window is armed and not expired, and the new utterance is not
  itself a hangup / cancel:
  - **Prepend**: `combined = f"{_continuation_text} {text}".strip()`.
  - If `_continuation_committed` is `True` (the grace-after-answer case, where Turn 1's
    user+assistant pair is already in history), call a new
    `BrainManager.drop_last_turn()` so the combined message **replaces** the previous
    pair instead of duplicating it. (For the abort-during-thinking case nothing was
    committed, so this is a no-op.)
  - Continue the turn with `text = combined`; re-arm the window with `combined` and
    `chain += 1`.
- Emit a `TranscriptionUpdate(is_final=True)` with the combined text so the UI bubble
  shows the whole sentence (consistent with the existing completion-buffer bubble path,
  `pipeline.py:4079`).

### 4.4 `BrainManager.drop_last_turn()`

New small method:

```python
def drop_last_turn(self) -> None:
    """Remove the most recent (user, assistant) pair from the conversation
    buffer. Used only by the voice continuation-recombine path when a new
    combined turn supersedes the immediately-preceding committed turn, so the
    truncated half is not duplicated in history. No-op when fewer than two
    messages are buffered or the tail is not a user/assistant pair."""
```

Guarded and idempotent; touches only `self._history`.

## 5. Configuration

New keys under `[voice]` in `jarvis.toml` (all via `config_writer`, BOM-safe; the
Pydantic model gets the fields with the documented defaults — `ConfigDict(extra="allow")`
already protects boot if a key is missing, AP-16):

| Key | Default | Meaning |
|---|---|---|
| `continuation_interrupt_enabled` | `true` | Master switch for Units A–C. `false` → today's behavior. |
| `continuation_grace_ms` | `2500` | How long after the answer finishes a new utterance still counts as a continuation. |
| `continuation_max_chain` | `3` | Max number of coalesced fragments before the next utterance is a fresh turn. |

## 6. Edge cases

- **Genuinely new command during thinking/speaking** → treated as a continuation (the
  user is deliberately talking over the brain — a strong continuation/correction
  signal). Accepted by design.
- **Genuinely new command in the grace window** → the main false-merge risk. Mitigated
  by a deliberately short grace (2.5 s default). Documented limitation; the maintainer
  chose the wider window (Q2) with eyes open.
- **Hangup / "auflegen" / "vergiss das"** during the window → discard the window, run
  the hangup/cancel path unchanged (these guards already run before the recombine point;
  the recombine must explicitly skip when the new utterance matches them).
- **Wake-only continuation** ("Jarvis") → no recombine; window untouched (handled by the
  existing wake-only guard before dispatch).
- **Chain cap reached** → next utterance is a fresh turn; window disarmed.
- **Brain already finished but TTS not started** (tiny race) → cancel cannot undo the
  commit; the `drop_last_turn` fallback in Unit C cleans it on recombine.
- **Stale window** → dropped lazily on the next utterance (same pattern as
  `ContinuationBuffer`), never via a background timer that could fire across turns
  (BUG-032 watchdog-class avoidance).

## 7. Testing strategy (TDD RED→GREEN)

Unit tests (asyncio_mode=auto; fakes from `tests/fakes/`, no `unittest.mock`):

- **Coalescer (Unit A/C)** — arm on dispatch; prepend on next utterance within window;
  combined text dispatched as one turn; window resets/extends correctly; chain cap
  enforced; stale window dropped; hangup/cancel/wake-only discard the window; expired
  window → fresh turn (no merge).
- **History hygiene** — interrupted (cancelled) turn never appends to history; grace
  recombine calls `drop_last_turn` exactly once; `drop_last_turn` is a no-op on short /
  non-pair history.
- **`drop_last_turn`** — removes exactly the last user+assistant pair; idempotent.
- **Thinking monitor (Unit B)** — with a fake monitor, a detected-speech signal cancels
  the brain consumer and the turn ends `barged`/interrupted without speaking; no
  detection → no effect, no added latency; monitor exception → no interrupt, turn
  proceeds.
- **Regression guard** — `continuation_interrupt_enabled = false` reproduces today's
  behavior exactly (a continuation is a fresh independent turn).

Live verification: the coalescer logic is fully unit-testable and can be exercised via
the WS text-drive path. The thinking-phase interrupt needs a real microphone (Silero)
and is verified on the maintainer's machine after `POST /api/settings/restart-app`.

## 8. Risks

- **Hot-path fragility.** This touches the voice critical path that owns the auto-submit
  bug class. Mitigation: reuse the proven second-mic monitor pattern; no main-loop
  rewrite; everything behind a default-on but switchable flag; comprehensive unit
  coverage before any live drive.
- **Two concurrent mics during thinking + preamble playback.** The barge monitor already
  opens a second mic on demand; Unit B must not open a third simultaneously with an
  active `_speak` barge monitor. Implementation detail: Unit B runs only in the
  pre-first-frame window and is torn down before `_speak`'s own barge monitor starts.
- **False merges in the grace window.** Accepted, bounded by the short default and the
  config switch.
- **Latency.** The monitor is a parallel task with no effect on the happy path; it is
  cancelled the moment the answer streams.

## 9. Decisions captured

- Q1 → abort the in-flight answer and re-think the combined sentence ("Denken abbrechen
  & neu").
- Q2 → continuation window = thinking + speaking + short grace ("Denken/Sprechen +
  kurze Nachfrist").
- Detection mechanism → reuse the second-mic monitor (low risk), not a main-loop
  restructure.
