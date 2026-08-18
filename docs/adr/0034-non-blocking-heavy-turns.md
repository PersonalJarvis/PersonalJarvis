# ADR-0034 — Non-blocking heavy turns: the user keeps talking while the tool model works

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** Voice UX + text surfaces (every engine, every provider)

## Context

A heavy turn — the tool model, a router round trip, a background agent —
takes 5–60 s. Until now the conversation stopped for that time: in the
realtime engine the live model was muted while its delegate ran (and, on a
provider-requested `jarvis_action`, the transport itself waited for the
function response); in the classic pipeline the turn `await`ed the brain
inline and any speech into the wait was treated as "cancel and recombine";
on the text surfaces the reply simply arrived when it arrived. ADR-0033
gave the wait a first sign of life within 2 s. This ADR removes the wait
from the conversation altogether.

The maintainer's vision (2026-08-18): hand the heavy work to the background
at once, keep conversing, and have the result come back later — without the
result talking over the user, without it landing in the middle of another
topic unannounced, and without the fast model inventing or denying it in
the meantime.

What already existed and is kept (measured in `jarvis/realtime/session.py`
and `jarvis/speech/pipeline.py` on 2026-08-18):

- Realtime: the delegate is a background `asyncio` task; a real new
  utterance splits the turn; a result whose turn closed is queued as a
  *late result* and spoken once the session is at rest
  (`_queue_late_delegate_result` / `_flush_late_delegate_results`); the
  live model carries `_DELEGATE_PENDING_DIRECTIVE`; presence probes get a
  deterministic status line (BUG-070); stop words cancel (`interrupt_intent`).
- Pipeline: `AnnouncementRequested(kind="completion")` is a proven
  proactive-speech channel — deferred while the user holds the floor,
  flushed at the next turn boundary, punching through a hang-up
  (AD-OE5/OE6); the instant ack + one grounded progress line (ADR-0033).
- Both: the deterministic planner (`plan_turn(...).requires_orchestrator`)
  decides "heavy" at dispatch with zero latency.

What blocked the vision (log evidence 2026-08-18 15:34 and code):

1. A provider-requested `jarvis_action` is answered on the wire only when the
   delegate finishes; a transport that waits for the function response
   (Gemini Live on Vertex — `behavior` is unsupported there; the Developer
   API only on models that support asynchronous calls) answers nothing new
   until then.
2. `_LATE_DELEGATE_DELIVERY_TIMEOUT_S = 30`: a result that finds no 30 s of
   rest is dropped with a log warning — the user talks, the answer is gone.
3. `_session_is_at_rest()` refuses every delivery while *any* delegate task
   exists, so a second background order blocks the first result.
4. A probe or a continuation of the running order gets one canned pool line
   through the surface TTS — a second voice, no real progress.
5. The pipeline has no background turn at all; the continuation window is
   armed for the whole thinking phase, so every utterance recombines.

## Decision

The rule of CLAUDE.md §3 applies from the first sentence: this is ONE
provider-neutral design. Every voice engine, every realtime transport,
every brain family (cloud or local), and the text surfaces get the same
user-visible behaviour; a transport-specific mechanism is a declared
capability, never the design.

### 1. One vocabulary: the parked result

A heavy turn that outlives the conversation's attention becomes a **parked
result** — `jarvis/voice/parked_results.py` (stdlib only, engine-neutral):

- `ParkedResult(text, language, request_text, success, request_turn_id,
  delivery_id, queued_at, intervening_turns)`; `ParkedResultLedger` with
  `park()`, `peek()`, `pop(delivery_id)`, `note_turn_completed()`,
  `retrieve_for(query)`, `supersede(request_text)`, `cancel_all()`,
  `drain()`; the closed vocabulary `classify_wait_query()`
  (PROGRESS / RESULT); `requested_result(items, query)`; and `reanchor()`
  with `topic_of()`. The realtime session keeps its own list of
  `ParkedResult`s (its `_LateDelegateResult` alias) and the pipeline uses
  the ledger; both read the same vocabulary.
- **No time-based expiry.** A parked result leaves the ledger only when it
  was spoken/sent, when the user cancelled the order (stop word / explicit),
  when the same order was re-issued (superseded), or at session end — and
  the last case logs the request it drops by name (BUG-070 lesson).
- **Delivery gate = a predicate the engine supplies**, evaluated per item at
  every rest: user not speaking, no output active, no delivery in flight,
  no interim line in the last `INTERIM_GAP_S`. A running background task of
  ANOTHER order is not a reason to wait (gate per task, not per session).
- **Re-anchoring is mandatory when anything happened in between.** The
  spoken result opens with a short reference to the request it answers
  (realtime: the `late=True` rendering order carries the request text; the
  pipeline and the text surfaces use a closed de/en/es prefix pool with the
  request's own words). Straight after the ack with no turn in between,
  no prefix.
- **On demand.** A user question that names the parked work ("was kam bei
  X raus?", "hast du das Ergebnis?") delivers the ready result now — after
  the user stops speaking, in the same voice — instead of waiting for rest.
  A closed multilingual vocabulary decides it (regex, AP-9/11); a miss
  simply stays native.

### 2. Realtime engine

1. **The transport is freed the moment the user moves on.** A fast order
   that finishes inside its own turn still answers the provider's function
   call directly (unchanged wire, unchanged tests). When the user opens a
   NEW turn while an earlier order's `jarvis_action` call is still open on
   the wire, the session answers that call at once with the closed interim
   payload (`_PENDING_TOOL_CALL_INTERIM_RESULT`: "still executing, do not
   invent an outcome, do not call again, answer the new request"), so a
   transport with blocking function calls (Gemini Live — `NON_BLOCKING` is
   unsupported on Vertex and on the 3.1 Live model, and blocking is the
   default everywhere) can answer the new turn. The order keeps running;
   its result is parked and delivered through the late-result path — a
   developer text turn at rest — never as a late tool result against the
   answered call id. A stop word closes open calls the same way ("cancelled
   by the user"). Switch: `[voice].realtime_unblock_pending_tool_calls`
   (default on). Native asynchronous calls stay a follow-up: no supported
   default model offers them today, so no plumbing is shipped for them.
2. **Rest is per delivery.** `_session_is_at_rest()` and
   `deliver_announcement` no longer refuse because a delegate task exists;
   they refuse while a trusted reply is between injection and its readback
   (`_delegate_delivery_in_flight`, the BUG-143 window). A background order
   that is still computing does not hold a ready result hostage. Running
   orders stay reachable after their turn closed
   (`_delegate_states_by_turn`).
3. **No expiry.** The late-result flush polls until spoken or until the
   session ends (`end()` re-routes what is left to the detached completion
   channel, named in the log); the 30 s bound is gone. A parked result
   carries `request_text`, and the `late=True` rendering order names it so
   the live model ties the answer back to the request.
4. **Wait queries are owned by the orchestrator, before the planner.**
   `classify_wait_query` runs on every final while an order runs or a result
   is parked: a PROGRESS question, or a RESULT request while the order
   still runs, speaks ONE line grounded in the running tool
   (`ActionProposed` → SEARCH / READ / SCREEN pools; unknown tool → the
   BUG-070 status pool); a RESULT request while a result is parked delivers
   that result now — as this turn's response on transports that generate
   only on request, or the moment the probe turn closes on transports that
   answer on their own VAD (their freestyle answer is dropped). Neither
   becomes a brain dispatch. The grounded line still rides the surface-TTS
   status channel BUG-070 established; moving it into the live voice needs
   the bridge to own a non-delegate turn and is a follow-up for a live
   session.
5. Everything else stays: the withhold of the model's own words for the
   turn that owns a running delegate (BUG-054), the repeat-order refusal,
   `_DELEGATE_PENDING_DIRECTIVE`, the endpoint protection.

### 3. Classic pipeline

1. **Heavy turns park.** When `plan_turn(text).requires_orchestrator` and
   `[voice].background_heavy_turns` (default `True`), the pipeline arms the
   instant ack as before, then runs the brain call in a background task
   (`_run_parked_turn`) and returns to `LISTENING` at once. The continuation
   window is marked idle at that moment (2.5 s grace as for a finished
   turn), so a fast follow still recombines and a later utterance is a new
   turn.
2. **New utterances are ordinary turns**, answered by the same brain
   concurrently (`generate_stream` holds no lock; history is appended per
   completed generation). Three utterance classes are answered by the
   orchestrator instead: stop/redirect (`interrupt_intent`) cancels the
   parked turn and speaks the cancel line; presence probe / progress
   question speaks ONE grounded progress line; a re-issue of the same order
   is refused with the progress line (no double execution).
3. **The result is delivered as a completion readback**:
   `AnnouncementRequested(kind="completion", source_layer="speech.parked_turn")`
   — deferred while the user holds the floor or a turn is being processed
   or spoken, flushed at the next `LISTENING`/`IDLE` boundary, scrubbed by
   the same output filter, recorded on the transcript as the answer to the
   parked turn. The instant ack, the progress line and `_on_action_proposed`
   read the parked turn's own "still running" state, no longer the global
   `PROCESSING` state.
4. Failure and silence are spoken: a timeout, an exception, or an empty
   answer speaks the existing honest lines (`_speak_brain_timeout`,
   `_handle_silent_brain_turn`) — never a silent drop (AD-OE6).
5. `[voice].background_heavy_turns = false` restores the inline await.

### 4. Text surfaces

Chat (desktop, headless launcher, browser) and channel adapters keep their
per-message concurrency; the pre-ack bubble stays. A heavy reply that lands
after a later exchange is prefixed with the same re-anchoring line
(`ParkedResult` prefix pool) so the message names the request it answers.

### 5. Observability

Two frozen bus events: `ParkedResultQueued(request_text, delivery_id,
surface)` and `ParkedResultDelivered(delivery_id, surface, waited_s,
delivered_on_demand)`; a named WARNING for every parked result a session
end abandons. Nothing about a parked result is silent (AP-30).

## Consequences

- The user can keep talking through any heavy turn on every engine and
  transport; the answer arrives when there is room for it, introduced by
  what it answers, and never disappears after 30 s.
- Two chargeable calls stay the ceiling per heavy turn (ack + interim);
  the parked delivery reuses the result already computed.
- Ordering of brain history changes: a parked pair is appended when it
  completes, possibly after a later fast turn. The fast turn sees "still
  running" via the pending directive / the parked-turn state; this is the
  honest picture.
- The interim answer to an open function call is the universal baseline
  and needs no provider cooperation; a native asynchronous path (Gemini
  `NON_BLOCKING` + `WHEN_IDLE`, OpenAI's asynchronous wire) is a declared
  capability for later, once a supported model offers it and a live session
  can verify it.
- Amends BUG-051's rule "a bridge must be a bystander" — unchanged — and
  BUG-070's presence probe: same trigger, now a grounded line in the live
  voice.

## Alternatives considered

- **Keep the 30 s expiry, just longer** — any bound drops a result the user
  asked for; a result may only leave the ledger for a named reason.
- **Native asynchronous calls as the design** — unavailable on Vertex,
  not offered by the 3.1 Live model on the Developer API, meaningless on
  the pipeline; it is one cell of the matrix, so it stays a capability.
- **Answer every provider call with the interim payload immediately** —
  would change the wire for the common fast case (result inside the same
  turn) that 25 live-forensic tests pin, for no user-visible gain; the
  block only hurts once the user actually moves on, so that is when the
  call is answered.
- **Deliver the parked result through the surface TTS in realtime** — a
  second voice mid-call (BUG-090); rejected, the live model renders it.
- **Keep the pipeline inline and only shorten the wait** — the ack (ADR-0033)
  already does that; it does not give the floor back.
- **Interrupt the user for a ready result** (`scheduling=INTERRUPT`) — the
  maintainer's explicit fear; a result waits for rest or for being asked.

## References

- `jarvis/voice/parked_results.py`, `tests/unit/voice/test_parked_results.py`
- `jarvis/realtime/session.py` (`_session_is_at_rest`, `_flush_late_delegate_results`,
  `_handle_tool_call`, `_speak_pending_action_status`), `jarvis/realtime/protocol.py`
- `jarvis/plugins/realtime/gemini_live.py`, `jarvis/plugins/realtime/openai_realtime.py`
- `jarvis/speech/pipeline.py` (`_run_parked_turn`, `_deliver_parked_result`)
- ADR-0033, BUG-051, BUG-054, BUG-070, BUG-090, BUG-143, BUG-144
- CLAUDE.md §3 "Provider & mode parity", CLOUD.md Rule #2
