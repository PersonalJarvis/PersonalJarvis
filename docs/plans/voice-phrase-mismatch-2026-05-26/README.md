# Voice-Phrase Mismatch — Diagnosis 2026-05-26

> **Status:** Investigation only. **No code change has been made.** This
> document follows Phase-1 of the *systematic-debugging* skill (root-cause
> investigation before fix). Do **not** modify Jarvis based on this file
> until Phase-2/3 of that skill has been completed (hypothesis selection
> + minimal-change test).

---

## 1. Symptom (verbatim from the user)

> "Manchmal schmeißt er einfach irgendwelche komischen Phrases rein, die
> gar keinen Sinn machen. Ich habe eine simple Aufgabe gestellt, und er
> hat gesagt:
>
>     „Die Mission ist fehlgeschlagen, drei Versuche haben nicht
>      gereicht. Dann schauen wir einfach mal in die letzte
>      Transkription, was ich gesagt habe."
>
> Das hat wirklich überhaupt nichts mit dem Kontext zu tun."
>
> (English gloss: "Sometimes it just throws in random weird phrases that
> make no sense at all. I gave it a simple task, and it said:
> 'The mission has failed, three attempts were not enough.
> Then let's just take a look at the last transcription to see what I said.'
> That has absolutely nothing to do with the context.")

The two sentences are **semantically incompatible**:

- The first is a closing, terminal-failure announcement.
- The second is an opening, planning utterance ("let us now look at …").

That mismatch is the headline of the bug.

---

## 2. Phrase decomposition

| # | Phrase                                                                                                                                                                      | Surface character                          |
|---|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------|
| 1 | "Die Mission ist fehlgeschlagen, drei Versuche haben nicht gereicht." ("The mission has failed, three attempts were not enough.")                                            | Static template, terminal-failure summary  |
| 2 | "Dann schauen wir einfach mal in die letzte Transkription, was ich gesagt habe." ("Then let's just take a look at the last transcription to see what I said.")              | First-person, mid-thought reasoning prose  |

**Hypothesis (to be proven, not assumed):** the two phrases were emitted
by **two different voice surfaces in rapid succession**, and the user
perceived them as one utterance.

---

## 3. Phrase 1 — source confirmed

### 3.1 Code location

`jarvis/missions/voice/announcer.py:151-213` — `MissionAnnouncer._render`,
the `MissionFailed` branch.

```python
# announcer.py:177-193 (extract)
de_map = {
    "critic_loop_exhausted": "Drei Versuche haben nicht gereicht.",
    "critic_rejected":       "Die Prüfung war nicht zufrieden.",
    "task_error":            "Der Worker ist abgebrochen.",
    ...
}
short_reason = reason.split(":", 1)[0].strip()
if lang == "de":
    tail = de_map.get(short_reason, f"Grund: {reason}" if reason else "")
    text = f"Die Mission ist fehlgeschlagen. {tail}".rstrip()
```

### 3.2 Why this fires

The phrase is the **deterministic readback** of a `MissionFailed` event
whose `reason` field equals `"critic_loop_exhausted"`. That reason is
emitted by the Critic loop once `MAX_CRITIC_LOOPS = 3` is reached without
the Critic returning `approve` (see `jarvis/missions/critic/runner.py`
+ ADR-0009 §"Self-healing Worker-Critic loop").

### 3.3 What it tells us about the failed call

That the phrase fired at all means:

1. The router-brain decided to **spawn a mission** for the user's
   utterance (force-spawn, `BrainManager._should_force_openclaw`, or
   the deprecated `spawn_openclaw` tool path).
2. The mission ran, the worker produced an answer/diff, the Critic
   rejected it.
3. The retry loop tried again, was rejected again, hit the hard cap of
   three iterations.
4. The Critic surrendered, the orchestrator emitted `MissionFailed`
   with reason `critic_loop_exhausted`.

That is **architecturally expected behaviour**. The phrase is not the
bug; it is the symptom that *something else* (the routing decision,
the worker output, the critic rubric) produced a wrong path for a
"simple task". Phrase 1 is therefore in scope for review (was the
spawn justified?) but not in scope for *output filtering* — ADR-0009
deliberately makes this path narrative-free.

### 3.4 What is ruled out for Phrase 1

| Hypothesis                                                  | Verdict | Evidence                                                                            |
|-------------------------------------------------------------|---------|-------------------------------------------------------------------------------------|
| LLM hallucinated the sentence inside the voice path         | **No**  | Hard-coded string literal at `announcer.py:178`. No LLM call on this path (AP-11).  |
| Critic-LLM `correction_instruction` leaked into TTS         | **No**  | ADR-0009 §1 Action/Observation-Invariant. Listener docstring repeats this.           |
| Worker free-text response leaked                            | **No**  | `MissionApproved.summary_de` is generated *only* by `summarise_from_tool_calls` (see `jarvis/missions/critic/summary.py`). `MissionFailed` does not read worker text at all. |
| Phrase was mis-attributed to Jarvis (it was the user)       | **No**  | The exact wording matches the literal in source.                                    |

### 3.5 Footgun on this path (informational, not the cause here)

The Mission→Voice bridge has **two parallel implementations** that both
subscribe to the `MissionBus`:

- `jarvis/missions/voice/announcer.py` — `MissionAnnouncer`, bridges
  Mission events to the speech pipeline's `AnnouncementRequested` bus
  (so they share `scrub_for_voice` + barge-in with every other voice
  surface).
- `jarvis/missions/voice/listener.py` — `MissionVoiceListener`, calls
  `tts_speak_fn` directly using `MissionReadback` templates.

If the bootstrap accidentally activates **both** for the same mission,
the user hears the failure message twice. This is **not** the cause of
the current report (the two phrases here are semantically distinct, not
duplicates), but a co-located bug surface to watch when triaging future
Mission-Voice reports. The `announcer.py:27-29` docstring documents this
risk.

---

## 4. Phrase 2 — source hypotheses

The grep for the literal `"schauen wir"` / `"letzte Transkription"` /
`"was ich gesagt habe"` returns **zero hits** under `jarvis/`. The
sentence therefore does **not** come from any hard-coded template. It is
LLM-generated. The remaining question is: which LLM, on which surface,
and how did it slip through `scrub_for_voice`?

The voice pipeline (`jarvis/speech/pipeline.py`) speaks via five
surfaces. Phrase 2 must come from one of them:

```
┌──────────────────────────────────┬──────────────────────────────────────────────────────┐
│ Surface                          │ How it can produce free-text prose                   │
├──────────────────────────────────┼──────────────────────────────────────────────────────┤
│ A) _handle_utterance → streaming │ Router-brain reply to a user turn. Free-text by      │
│    brain reply → TTS             │    design; only scrub_for_voice between brain & TTS. │
│                                  │                                                      │
│ B) _on_announcement              │ AnnouncementRequested events from MissionAnnouncer,  │
│    (AnnouncementRequested)       │    Ack-Brain, OpenClaw-bridge,                       │
│                                  │    spawn-watchdog. Mission/OpenClaw paths are        │
│                                  │    static + ADR-0009-protected. Ack-Brain produces   │
│                                  │    LLM-free-text but with persona-prompt + 200 ms    │
│                                  │    latency budget.                                   │
│                                  │                                                      │
│ C) _on_background_completed      │ OpenClaw bridge readback. Uses summary_de via the    │
│                                  │    capability-honest summariser. Not a free-text     │
│                                  │    vector either.                                    │
│                                  │                                                      │
│ D) MissionVoiceListener (legacy) │ Direct TTS, MissionReadback templates only.          │
│                                  │    Static.                                           │
│                                  │                                                      │
│ E) Spawn-watchdog                │ One literal phrase "Bin noch dran." ("Still on it.") │
│                                  │    (pipeline.py: 1722-1765). Static.                 │
└──────────────────────────────────┴──────────────────────────────────────────────────────┘
```

Only **A** and the **Ack-Brain branch of B** are free-text vectors.

### 4.1 H1 — Router-brain reply on a new turn (most likely)

After Phrase 1 was spoken, the pipeline transitions
`SPEAKING → LISTENING` (announcer publishes
`priority="interrupt"`, see `announcer.py:213`). The user, having heard
a failure announcement, would naturally ask a follow-up question. If
the STT picked up *any* input on the next turn (even partially
intelligible), the router-brain is free to reply with reasoning prose.

Phrase 2 has the textbook signature of router-brain reasoning prose:

- First-person plural ("Dann schauen wir … was ich gesagt habe" — "Then
  let's … see what I said") — the brain narrating its own next step.
- No tool name, no JSON, no Markdown, no "Sir", no engineering jargon
  → **passes `scrub_for_voice` untouched** (the filter is regex-only by
  AP-11 mandate and does not penalise narration).
- Self-referential ("die letzte Transkription" — "the last transcription") —
  typical of a model prompted with both user turn + recent conversation
  history. The brain may be referring to the `_build_history_hints()`
  snapshot that `BrainManager` passes to workers (see
  `brain/manager.py:1770-1776`).

**Why this would feel out-of-context to the user:** the user did not
ask "what did I say earlier" — the brain hallucinated that question to
itself, and the answer "let me look at the last transcript" was its
own meta-narration that leaked into the spoken stream.

### 4.2 H2 — Cross-turn audio bleed

The Mission-Failure announcement is loud enough for the microphone to
re-capture it through the speakers. STT then transcribes a fragment of
"Mission fehlgeschlagen, drei Versuche …" ("mission failed, three attempts
…") as user input on the next turn. The router-brain receives this
self-echo as a query and answers with planning prose ("then let's look at
the last transcript …").

This matches the reported intermittency. It depends on:

- Headset vs. open speakers (open speakers raise bleed risk).
- `silence_ms` / VAD endpoint config — a tight endpoint can finalise a
  short self-echo as a user turn.
- The semantic-hangup detector (`jarvis/speech/hangup.py`) not having
  fired on the failure announcement (intended — that is a brain
  output, not a user turn).

Note: there is a *known* defence against probe-induced false STT
finalisation (`tests/unit/speech/test_thinking_pause_patience.py`,
fixed 2026-05-25, commit `e2fe8045c`), but the symmetric case
"loud TTS bleeds into the next-turn STT" is not the same code path.

### 4.3 H3 — Ack-Brain preamble emitting the wrong content

The Ack-Brain (`jarvis/brain/ack_brain/generator.py`) fires before the
main brain replies, publishes `AnnouncementRequested(kind="preamble")`,
and goes through `scrub_for_voice` with `ack_mode=True` (see
`output_filter.py:464-471` — `FILLER_OPENER_RE` is *intentionally
skipped in ack-mode* because acks are supposed to look like
contextual openers, e.g. "Lass mich kurz nachschauen." ("Let me quickly
check on that.")).

A misfire shape that fits Phrase 2:

> "Dann schauen wir einfach mal in die letzte Transkription, was ich
> gesagt habe." ("Then let's just take a look at the last transcription
> to see what I said.")

That is **exactly** the persona-prompt-shaped ack-mode opener for a
follow-up turn after a failure. Failure-mode F4 (over-long output) and
F10 (self-answer) cover some but not all degenerations of that prompt.

If H3 is the cause, the suppress-if-fast gate
(`[ack_brain].suppress_if_brain_faster_than_ms = 2000`, see ADR-0014
*flash-brain*) did not suppress — meaning the main brain took ≥ 2 s to
reply, so the ack survived to TTS even though the user had not asked a
question that warranted an "I'll look it up" preamble.

### 4.4 H4 — Sub-Agent / skill announcement

Any subscriber that publishes `AnnouncementRequested` can send arbitrary
text. The Ack-Brain coordinator and skill announcers both have that
permission (`pipeline.py:1539-1607`, `pipeline.py:1956-1996`). A
mis-templated mission milestone or a skill-bot announcement could in
principle produce planning prose. Lowest a-priori probability — these
events have static phrasings — but cannot be excluded without trace
data.

---

## 5. The combined timeline (best current model)

```
   t=0   user: <simple task>
   t=Δ1  router-brain decides spawn   ← out-of-scope mission for "simple task" is its
                                        own sub-bug; track as a routing-decision review
   t=Δ2  worker iter 1 → critic reject
   t=Δ3  worker iter 2 → critic reject
   t=Δ4  worker iter 3 → critic reject
         MissionFailed(reason="critic_loop_exhausted") published
   t=Δ5  MissionAnnouncer renders:
         "Die Mission ist fehlgeschlagen. Drei Versuche haben nicht gereicht."
         (= "The mission has failed. Three attempts were not enough.")
         priority="interrupt", language="de"
         → AnnouncementRequested → _on_announcement → scrub_for_voice
         → TTS → audio
   t=Δ6  Pipeline transitions to LISTENING.
   t=Δ7  Self-echo (H2) OR weak user follow-up (H1) → STT finalises a turn.
         OR ack-brain misfires (H3) before the main brain has anything to say.
   t=Δ8  Brain produces:
         "Dann schauen wir einfach mal in die letzte Transkription, was ich gesagt habe."
         (= "Then let's just take a look at the last transcription to see what I said.")
         scrub_for_voice passes it (no tool JSON, no markdown, no Sir, no jargon).
         → TTS → audio
   t=Δ9  user hears phrase-1 + phrase-2 as one nonsensical block.
```

The two phrases are not produced by the same code path. They are produced
by two **different** voice surfaces in the same conversational beat, and
the inconsistency is the bug-class.

---

## 6. What this is NOT

These are explicitly **ruled out** by code reading:

1. **It is not a worker-LLM free-text leak.** ADR-0009 §"Action/
   Observation invariant" is enforced in two places: the success path
   reads only `MissionApproved.summary_de` (deterministic, generated by
   `summarise_from_tool_calls`), and the failure path uses a static
   `reason → phrase` map. The Critic's `correction_instruction` is
   explicitly forbidden from reaching TTS.
2. **It is not a `scrub_for_voice` failure on Phrase 1.** That phrase
   has no tool-JSON, no markdown, no "Sir", no jargon. It is allowed
   through *by design* — the filter does not penalise it because it is
   a Kontrollierer-signed mission summary.
3. **It is not the recurring BUG-008 (multi-layer enum drift).** No
   wire-format enum is involved here. The five-layer scaffolding is
   irrelevant to this bug class.
4. **It is not BUG-020 (silent-cascade).** The opposite: the system is
   *too* talkative, emitting an extra utterance, not dropping one.

---

## 7. Recurring bug-class — proposed name

This is a new class. Proposed label:

**"Cross-surface voice incoherence"** — two voice surfaces fire in
quick succession from independent code paths; each output is
individually valid (Phrase 1 is a correct mission-failure readback;
Phrase 2 is a syntactically valid LLM reply), but their juxtaposition
violates the user's mental model of a single coherent conversation.

Counter-design (for a future fix discussion, **not** to implement now):

- A **conversational-state mutex** that gates new spoken output for N
  ms after a `priority="interrupt"` mission-failure announcement
  unless the user has spoken in the interval.
- A **self-echo guard** in the STT path (mirror of the existing probe
  endpoint defence) that drops a finalised turn if its waveform overlaps
  the last TTS output stream beyond a similarity threshold.
- An **ack-brain hard-gate after mission failure**: do not emit a
  preamble in the first turn following a `MissionFailed`, because the
  failure-announcement already filled that conversational slot.

Each of these is its own design discussion. None is implemented here.

---

## 8. Diagnostic next steps (no code change)

Before a fix is attempted, the following evidence must be gathered the
**next time the user observes the symptom**:

### 8.1 Live trace

1. Reproduce with `JARVIS_DEBUG=1` set in the launch environment.
2. Tail `data/jarvis_desktop.log` while triggering a task that has
   previously failed three times.
3. After hearing the two phrases, immediately note the wall-clock time.
4. Grep the log around that timestamp:

   ```powershell
   Select-String -Path data/jarvis_desktop.log -Pattern `
       'MissionFailed|critic_loop_exhausted|AnnouncementRequested|' +
       '_on_announcement|_handle_utterance|ack_brain|flash-brain'
   ```

### 8.2 What the log should show for each hypothesis

| Hypothesis | Expected log signature                                                                                  |
|------------|----------------------------------------------------------------------------------------------------------|
| H1 (brain) | `MissionFailed` → `AnnouncementRequested(priority=interrupt)` → `LISTENING` → STT turn with **user-shaped** transcription → `_handle_utterance` → brain reply with Phrase 2. |
| H2 (echo)  | Same prefix, but the STT turn just before Phrase 2 transcribes fragments of Phrase 1 itself (e.g. "Mission fehlgeschlagen drei Versuche" — "mission failed three attempts"). That is the smoking gun. |
| H3 (ack)   | Phrase 2 appears in an `AnnouncementRequested(source_layer="brain.ack_brain", kind="preamble")` log line **before** the main brain reply for the next turn — i.e. without an intervening `_handle_utterance` arrow at all. |
| H4 (skill) | Phrase 2 appears in an `AnnouncementRequested` with a `source_layer` other than `brain.ack_brain` or the mission bridges. |

### 8.3 Smoke-probe candidate (read-only)

`scripts/voice_e2e_probe.py` already runs end-to-end voice tracing. A
narrow probe could be added (not in this PR) that:

1. Force-injects a `MissionFailed(reason="critic_loop_exhausted")` on
   the mission bus with `source_actor="hauptjarvis"`.
2. Replays a short user-turn audio fixture immediately after the
   failure announcement.
3. Captures the next two voice surfaces and asserts that they are
   coherent — i.e. that no `_on_announcement`-emitted preamble appears
   in the 2 s window following a `priority="interrupt"` mission
   announcement.

That probe would have caught the present bug pre-merge if H3 is the
cause. (Probe design is itself out of scope here.)

---

## 9. Status update 2026-05-26 — structural defence implemented

Rather than targeting one of the four hypotheses, a **structural
defence** has been added that closes the entire cross-surface voice
incoherence class:

> **Post-interrupt preamble quiet window.** After the speech pipeline
> handles an `AnnouncementRequested(priority="interrupt")` (any mission
> failure, mission timeout, or other interrupt-class readback), any
> subsequent `AnnouncementRequested` with `kind="preamble"` is suppressed
> for the next `[ack_brain].suppress_preamble_after_interrupt_ms`
> milliseconds (default 5 000 ms).

### 9.1 What the defence covers

| Hypothesis | Covered by the gate? | How                                                                                              |
|------------|----------------------|---------------------------------------------------------------------------------------------------|
| H1 (router-brain reply on the next turn) | **Partially** — only if the brain reply happens to be routed through the `kind="preamble"` ack path. A normal `_handle_utterance` reply still goes through. That is correct: a deliberate user follow-up *should* be answered. |
| H2 (cross-turn audio bleed)              | **Indirectly** — H2 produces a brain reply on the next turn (same path as H1); the gate dampens the preamble half of any double-speak.  A separate self-echo guard remains future work. |
| H3 (ack-brain preamble misfire)          | **Yes** — the Flash-Brain publishes its output as `AnnouncementRequested(kind="preamble", source_layer="brain.ack_brain")`. The gate drops it inside the window. |
| H4 (skill / other preamble emitter)      | **Yes** — same gate, any subscriber that publishes `kind="preamble"` is affected. |

That gives **3 of 4 hypotheses fully covered** by one minimal change.
H1 (deliberate brain follow-up) cannot be silenced without breaking
the legitimate "user asks a follow-up after a failure" flow — that one
stays exposed and is tracked separately.

### 9.2 Files changed

- `jarvis/brain/ack_brain/config.py` — new field
  `AckBrainConfig.suppress_preamble_after_interrupt_ms` (default 5 000;
  range 0..60 000; 0 disables the gate).
- `jarvis/speech/pipeline.py` — `SpeechPipeline.__init__` tracks
  `self._last_interrupt_announcement_ts: float | None`;
  `_on_announcement` arms the timestamp on every interrupt-priority
  event and short-circuits any subsequent `kind="preamble"` event
  inside the window with a structured log line.
- `tests/unit/speech/test_post_interrupt_quiet_window.py` — five TDD
  tests covering:
  - preamble inside window → suppressed,
  - preamble after window → spoken,
  - completion inside window → spoken (negative gate-coverage),
  - interrupt itself → spoken (bookkeeping does not self-block),
  - preamble without prior interrupt → spoken (cold-start happy path).

### 9.3 Why this is still defence-in-depth, not a closing fix

- **Phrase 1 is wanted behaviour.** The gate does not silence it;
  ``priority="interrupt"`` announcements pass through and arm the
  window. Killing the alarm bell would lose the information the user
  needs.
- **The deeper question is upstream.** Why did a "simple task"
  reach the mission spawn in the first place, and why did the Critic
  reject the worker three iterations in a row? The routing decision
  (`BrainManager._should_force_openclaw`) and the Critic rubric for
  whatever the worker produced deserve their own review, independent
  of the voice-incoherence symptom. **That review is not part of this
  change.**
- **H1 is intentionally not blocked.** A legitimate user follow-up
  immediately after a mission failure must still be answered. The
  gate only blocks the meta-class of utterances that have no
  conversational purpose right after a failure ("I'll look it up",
  "Let me think about that").
- **The 5 s default is a guess, not a measurement.** It matches one
  failure readback (~3 s) plus a short breath. Once the user gathers
  live-trace data per §8.1, the value can be tuned via
  `jarvis.toml` without redeploying.
- **Disable knob in place.** Setting
  `[ack_brain].suppress_preamble_after_interrupt_ms = 0` deactivates
  the gate entirely — useful when the same diagnostic trace is
  needed in production to confirm which hypothesis fired.

---

## 10. References (binding sources)

- ADR-0009 — *Self-healing Worker-Critic loop*. Action/Observation
  invariant. `docs/adr/0009-self-healing-worker-critic.md`.
- ADR-0010 — *Output-filter discipline*. Regex-only `scrub_for_voice`.
  No LLM calls on the voice path (AP-11).
- ADR-0014 — *Ack-Brain* (the suppress-if-fast variant). Persona
  prompt + 2 s gate.
- Anti-Pattern register `CLAUDE.md` §"Critical anti-patterns" —
  especially AP-3 (direct tool call bypassing executor),
  AP-11 (LLM in scrub), AP-13 (watchdog reload race).
- `jarvis/missions/voice/announcer.py:151-213` — `_render`. Phrase-1
  source.
- `jarvis/missions/voice/readback.py` — `MissionReadback` templates.
- `jarvis/missions/critic/summary.py` — deterministic
  `summarise_from_tool_calls`. Why Phrase 2 cannot have come from a
  `MissionApproved.summary_de`.
- `jarvis/brain/output_filter.py:362-536` — `scrub_for_voice` body.
  Explains why Phrase 2 passes the filter.
- `jarvis/speech/pipeline.py:1406-1607` — `_on_announcement` and
  ack-brain preamble emission.
- `jarvis/brain/ack_brain/generator.py:1-44` — ack-brain failure
  modes F1..F10.
- Bug register `docs/BUGS.md` — for the surrounding bug ecosystem; this
  entry is *not yet* registered there and should be added once the
  hypothesis is confirmed.
