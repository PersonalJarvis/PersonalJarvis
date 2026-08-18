# ADR-0033 — Instant acknowledgment: the first sign of life comes from the plan, not the model

**Status:** Accepted · **Date:** 2026-08-17 · **Phase:** Voice UX (both engines)

## Context

When a request needs the Tool Model or a background agent, the user waited
5–30 s with no sign of life. Measured on the maintainer's box (BUG-072):
delegated realtime turns p50 12–18 s before the fix, 4.5–12.5 s after; the
classic pipeline's action turns are non-streaming by construction
(`manager.generate_stream` withholds every chunk while
`plan_turn(...).requires_orchestrator`).

Every earlier interim-speech mechanism was structurally late or gated away:

| Mechanism | Why it did not cover the wait |
|---|---|
| Flash-Brain preamble (ADR-0014) | speculative, median 2.98 s to first token, only output on 22 % of turns — retired 2026-06-21 |
| Grounded per-tool ack (`brain.router.ack`) | fires only after the router's FIRST model round has streamed (2–16 s), then 2.5 s commit grace, 30 s min gap, 3/min cap, 180 s dedup |
| Realtime delegate bridge (BUG-051/054) | deliberately 6 s after dispatch (the "double-tap" fear), one generic pool line ("still on it") |
| Provider-requested delegate | no bridge at all |
| Public-fact grounding ack | immediate, but a surface-TTS second voice reading the whole question back |

The maintainer's rule (2026-08-17): under two seconds, always, for heavy
work — never for plain conversation; never a stock filler; never an
ack-then-answer double-tap; the ack for an ACTION must reference the request.

## Decision

One shared core, `jarvis/voice/instant_ack.py`, used by both voice engines.

1. **Trigger = the committed plan, at dispatch.** `plan_instant_ack(plan_turn(text))`
   maps the deterministic planner reasons to a work class with an expected
   duration: RESEARCH / SCREEN / MISSION and connected personal lookups speak
   **immediately**; ACTION and local personal lookups wait a **3 s grace**
   (1.2 s until 2026-08-18) and speak only if the turn is still processing. Plain conversation, voice
   control and vague orchestrator turns get nothing.
2. **State only what Jarvis is doing.** Closed de/en/es pools per class
   (no-repeat memory, `{agent}` = wake-word brand). The ACTION pool is empty
   by design: an action line is model-composed (the live model in realtime,
   the flash `ReadbackComposer` in the pipeline) and accepted only by the
   structural validator `contextual_ack_is_valid` — intent grammar, ≤ 12
   words, every content word from the user's own request, no digits, no
   result marker, no forbidden vocabulary, at least one subject word. An
   invented outcome needs new words or a non-intent verb, so it cannot pass.
   No composer → silence, never "on it".
3. **No double-tap.** A result ready before the line's first sample wins; a
   line already playing finishes (never cut mid-word). Realtime passes the
   spoken line into the trusted-result rendering order (*continue, do not
   repeat*); the pipeline drops the grounded router ack within 8 s of an
   instant ack (later it doubles as the progress line) and the spawn
   announcer receives the recently spoken line as context.
4. **One voice per call.** Realtime renders the line through the live model
   (existing bridge mechanics: exact-line or contextual order, transcript
   validated at the response boundary); verbatim-speech transports use the
   composer or stay silent for actions. The public-fact grounding path hands
   its surface-TTS ack to the bridge whenever a plan exists.
5. **Gates.** The instant ack is exempt from the pipeline's anti-loop cap and
   wording dedup (it is armed once per user utterance and cancels its
   predecessor); the "user holds the floor" and "Jarvis already speaking"
   drops and the `should_play` predicate still apply. `[ack_brain].instant_ack
   = false` is the kill switch. Mission heartbeat first beat 30 → 20 s.
6. **One grounded progress line.** When the work outlasts the first line by
   `PROGRESS_AFTER_S` (8 s), both engines speak ONE more line grounded in
   the tool the turn is actually running — `ToolExecutor`'s `ActionProposed`
   is classified into search / read / screen / handover / other pools
   ("Still searching.", "Still on the screen."); a handover speaks nothing
   (the spawn reply states it). At most one per turn, and never within 8 s
   of any other interim line (the grounded router ack may have covered it).
7. **Chat surface.** The text-chat path (desktop app and headless launcher)
   shows the same first line as a muted pre-ack bubble
   (`MessageSent(role="preamble")`, the bubble the chat view already
   renders), armed before the brain call and cancelled the moment the reply
   is in. Never spoken.
8. **Topic lines for pooled classes.** `[ack_brain].instant_ack_compose_all`
   lets the composer / live model produce a request-specific line for
   research / personal / screen / mission too, under the same validator, with
   the pool line as fallback. **On by default since 2026-08-18** (maintainer:
   an ack must always speak to the request, never a stock class line);
   `false` restores pool-only lines. Verbatim-speech realtime transports keep
   the pool line for these classes (they cannot compose).

## Consequences

- Realtime: first audio for a research question moves from ~7 s (generic) to
  ~1.5–2 s (class line, same voice); actions get "I'm opening Spotify."
  after 3 s if still running. `_DELEGATE_BRIDGE_DELAY_S` (6 s) is now only
  the cap / the delay for unclassified turns.
- Pipeline: heavy turns speak at 0 / 3 s instead of after round one; the
  grounded router ack becomes the 8 s+ progress line.
- 2026-08-18 amendment (first maintainer feedback, before a live session):
  short-work grace 1.2 → 3 s — a line 1.2 s in front of an answer that lands
  at second 3 is the double-tap the ADR forbids, while 3 s is still far
  inside the "waiting feels rude" window; `instant_ack_compose_all` on by
  default — pooled class lines survive only as the instant fallback. Same
  day, pipeline: the progress line is timed from the turn's OWN ack (grace +
  composer put it well after arm) and the "another interim line just spoke"
  gate no longer counts that ack — before, the gate saw it and no progress
  line ever followed.
- Two chargeable flash calls per action turn at most (ack + spawn/interim),
  both bounded (700 ms ack budget) and breaker-guarded.
- Amends ADR-0014 gate 1 (suppress-if-fast) — superseded by the class grace
  for the classic pipeline; BUG-054's closed-pool rule is extended, not
  relaxed: the contextual class is validated structurally, never by prompt
  compliance.

## Alternatives considered

- **Earcon at t≈0** — fastest honest signal, but the maintainer's target is
  a conversational partner, not a beep; kept out (the chime generator exists
  if wanted later).
- **Surface TTS for the realtime ack** (~0.8 s) — a second voice mid-call is
  the exact BUG-090 failure; rejected in favour of the live model (+~1 s).
- **Flash-LLM topic line for every class** — the pool line already fits
  research/personal/screen/mission and costs 0 ms; the composed line stays
  the ACTION-only path (and an Etappe-2 opt-in elsewhere).
- **Prompt-only "do not invent" for the action line** — BUG-054 proved
  prompt compliance is not a boundary; the structural validator is.
- **A bridge for provider-requested `jarvis_action` calls** — the live model
  usually speaks its own line before calling; injecting a response while a
  native function call is pending behaves differently per transport (Gemini
  Live blocks on the tool response) and cannot be verified without a live
  session, so this path keeps the model's own pre-call line as its ack.
  Follow-up once a transport declares the capability.

## References

- `jarvis/voice/instant_ack.py`, `tests/unit/voice/test_instant_ack.py`
- `jarvis/realtime/session.py` (`_run_delegate_bridge`, boundary validation,
  `_delegate_result_prompt(already_said=…)`), `tests/unit/realtime/test_session.py`
- `jarvis/speech/pipeline.py` (`_arm_instant_ack`, `_on_announcement` gates),
  `tests/unit/speech/test_instant_ack_pipeline.py`
- `jarvis/brain/turn_planner.py::is_lookup_shape`
- ADR-0014, BUG-051, BUG-054, BUG-070, BUG-072, BUG-090
