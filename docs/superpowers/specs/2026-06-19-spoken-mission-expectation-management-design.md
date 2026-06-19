# Spoken expectation management for background missions — design

**Date:** 2026-06-19
**Status:** Approved (brainstorming), implementation in progress
**Surface:** Voice path only (no visual chat indicator — explicitly chosen by the maintainer)

## Problem

When a voice/chat turn force-spawns a background sub-agent mission (`spawn_worker`
/ OpenClaw), the user hears exactly one tailored opening line and then **silence**.
The only follow-up is a single hard-coded German phrase `"Bin noch dran."` emitted
once at 90 s; otherwise nothing until the result. To a voice-first user talking to
Jarvis like a voice assistant ("Hey Google" style), that long silence reads as a
crash, and the opening line does not make clear that the task is a *substantial*
one that legitimately takes time.

The maintainer's ask (paraphrased from a voice session, 2026-06-19): in the natural
spoken conversation flow, Jarvis must **clearly and warmly say** that this is a
bigger task, that he is handling it in the background, and that it will take a
moment — and the conversation must stay alive instead of going dead.

## Goal

Two spoken improvements, both off the latency-critical answer path:

1. **Sharpen the opening spawn announcement** so it reliably conveys, in the user's
   language and a warm, human tone: *I'm taking this on · it's a more involved task ·
   I'm handling it in the background · it'll take a moment · I'll report back* — while
   keeping every existing guard (no stock phrase, no completion claim, no internal
   component names, ≤ 22 words, `scrub_for_voice`).

2. **Replace the one-shot 90 s `"Bin noch dran."`** with a varied, language-resolved
   (de/en/es), bounded **recurring** "still on it" heartbeat so the wait feels alive
   instead of dead — spoken only at a turn boundary (never over the user), and never
   after the result has already been spoken.

## Non-goals (YAGNI)

- The visual inline chat card (the maintainer explicitly chose the spoken surface).
- A `SPAWN_PERSONA_ES` LLM persona variant. Spanish is covered deterministically by a
  new `es` fallback pool; the LLM persona stays de/en (an `es` turn skips the LLM and
  uses the `es` pool). The locked 2026-05-11 flash-brain *preamble* persona
  (`persona_prompt.py`) is **not touched**, so no spec amendment is required. The spawn
  personas live in `spawn_announcement.py` and are owned by that module (per its
  docstring), not by the locked flash-brain spec.
- Topic interpolation inside the heartbeat. The opening announcement carries the
  concrete topic + substance; the heartbeats are varied, topic-agnostic reassurances.
  This keeps fragile per-language templating off the critical path. (Documented
  deviation from the brainstorm sketch, which floated a topic-aware first beat — same
  user-visible outcome, lower risk.)

## Components

### A. `jarvis/brain/ack_brain/spawn_announcement.py` (opening announcement)

- **Persona DE/EN:** add the "this is a more involved task" dimension and an explicitly
  warm, human tone, without weakening any existing ban or the word cap.
- **`_FALLBACK_SPAWN` pool:** refresh wording toward "bigger task + background + takes a
  moment + warm", and **add an `es` pool**.
- **`_FALLBACK_ALREADY_RUNNING` pool:** add an `es` pool.
- **`_resolve_language`:** return `de` / `en` / `es` (was `de` / `en` only).
- **`compose`:** only attempt the flash-LLM when a native persona exists for the
  language (`de` / `en`); an `es` turn goes straight to the `es` pool (a brain-supplied
  `candidate` still wins when it survives validation). No wasted LLM round-trip that
  would always reject on the language-match check.

### B. `jarvis/speech/pipeline.py` (heartbeat)

- New pipeline-local phrase table `_STILL_RUNNING_PHRASE: dict[str, tuple[str, ...]]`
  (de/en/es, 4–6 varied warm phrases each), TTS-clean (this path does not scrub),
  conveying "still on the bigger task, almost there" — never a completion claim.
  Mirrors the existing sibling tables (`_BRAIN_UNAVAILABLE_PHRASE`, …) — kept
  pipeline-local rather than routed through the spawn composer, to avoid a new import
  on the critical path.
- Cadence as instance attributes (no config-schema change):
  - `_spawn_watchdog_delay_s` (kept name) = **first** beat delay, default `30.0`
    (was 90; 90 s of silence reads as a crash). Tests override it.
  - `_heartbeat_interval_s` = subsequent interval, default `60.0`.
  - `_heartbeat_max_count` = cap, default `3`. Bounds the whole heartbeat to ≈ first +
    (max-1)·interval so it can never run forever.
- `_spawn_watchdog_body` becomes a bounded loop: sleep → (if not muted, bus present, and
  the AD-OE5 floor guard in `_on_announcement` permits) publish one
  `AnnouncementRequested(text=<picked>, language=<resolved>, priority="normal",
  kind="progress")` → repeat up to `_heartbeat_max_count`. The `kind="progress"`
  marks the beat "droppable when stale" so the floor guard DROPS it (rather than
  deferring it) while the user holds the floor. Language via `self._output_language(None, "")`
  (honors the `brain.reply_language` pin + sticky conversation language). A
  `deque(maxlen=2)` no-repeat guard so consecutive beats differ.
- Preserve every existing invariant: the task is still tracked in
  `_spawn_watchdog_tasks` (the "mission in flight" signal), still self-removes on every
  terminal path, still FIFO-cancellable by `_on_background_completed`. `CancelledError`
  exits quietly.
- **Completion overlap guard:** in `_on_announcement`, when a `kind="completion"`
  readback (the mission's actual answer) is delivered, cancel pending heartbeat tasks so
  Jarvis never says "still on it" right after the result.

## Data flow

```
Turn → router force-spawns → spawn_worker.execute()
  ├─ dispatch mission
  └─ compose() → sharpened opening announcement (topic + substance + warmth)  [tool output → spoken reply]
OpenClawAnnouncement(action,target) → _on_spawn_announcement → _schedule_spawn_watchdog()
  → bounded recurring heartbeat (de/en/es, varied, VAD-gated, ≤ max_count)
Mission result → MissionAnnouncer → AnnouncementRequested(kind="completion")
  → _on_announcement: speak result AND cancel pending heartbeats
Crash path → OpenClawBackgroundCompleted → _on_background_completed (FIFO cancel)  [unchanged]
```

## Edge cases

- **Never over the user:** heartbeats are `priority="normal"` with `kind="progress"`;
  the AD-OE5 floor guard in `_on_announcement` DROPS a `progress` announcement while the
  user holds the floor (a preamble/progress beat is ephemeral; only a completion readback
  is deferred). So a stale "still on it" is never spoken over the user — and never parked
  to replay after the user finishes or after the answer.
- **Muted:** each beat re-checks `_muted` and stays silent; the loop still self-removes.
- **No completion event ever (success path):** the heartbeat is capped and self-removes
  after the last beat → the "in flight" hold is bounded (same as today, ≈150 s vs 90 s)
  → no regression of the idle-timeout / finish-after-response logic
  (`_live_spawn_watchdogs`, `_background_mission_in_flight` are untouched).
- **Multiple spawns:** one heartbeat task per spawn; FIFO cancel spares newer ones.
- **AP-18:** the body swallows publish errors and never propagates.

## Acceptance criteria

1. Opening announcement (composer): for a de/en turn the persona output (and every
   fallback phrase) conveys "bigger task + background + takes a moment" warmly, with no
   completion claim, no forbidden vocab, ≤ 22 words. An `es` turn yields an `es` pool
   phrase. A brain-supplied `candidate` still wins when valid.
2. Heartbeat fires its first beat after `_spawn_watchdog_delay_s`, then up to
   `_heartbeat_max_count` total at `_heartbeat_interval_s`, each from the
   `_STILL_RUNNING_PHRASE` pool, consecutive beats differing.
3. Heartbeat language follows `_output_language` (pin > conversation > default), proven
   for de/en/es — never hard-coded `"de"`.
4. A `kind="completion"` readback cancels pending heartbeats (no "still on it" after the
   answer).
5. Muted → zero heartbeat phrases. Completion within the first-beat window → zero
   phrases. The heartbeat is hard-bounded (cannot run forever).
6. All pre-existing watchdog / idle-in-flight tests stay green (helper sets
   `_heartbeat_max_count = 1` to preserve their one-beat assertions; the two tests that
   pinned the literal `"Bin noch dran."` are updated to the new contract).

## Tests

- `tests/unit/brain/test_spawn_announcement.py`: es pools present + selected; persona
  output conveys substance/warmth (heuristic on the fallback pool wording); no
  completion claim / no forbidden vocab across all pools; `es` skips the LLM.
- `tests/unit/speech/test_spawn_watchdog.py`: updated text/lang assertions; new tests for
  recurring cadence, language resolution (de/en/es), and completion-readback cancel.
- `tests/unit/speech/test_idle_spawn_inflight.py`: unchanged, stays green.
