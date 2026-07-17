# Deep dive (DRAFT): doubled greeting + 10 s mid-reply silence in the realtime run 2026-07-17 15:11

**Status:** draft for maintainer review — analysis verified, fix not yet implemented.
**Session:** `364fce33-0613-4a73-bd32-db8e85b9a18a`, 2026-07-17 15:11:16–15:12:12, desktop realtime,
provider `gemini-live`, model `gemini-3.1-flash-live-preview`, tool mode `delegate`, 2 turns, ended by hotkey.
**Evidence sources:** `data/jarvis_desktop.log`, `data/sessions.db` (`voice_turns`, `voice_events`),
`data/flight_recorder/2026-07-17.jsonl`.

## TL;DR

The user heard, in turn 2: *"Servas Ruben,"* → **~10 s of dead silence** → the full answer **again
starting with "Servas Ruben,"**. <!-- i18n-allow: forensic quote of the spoken product-surface output under analysis -->
That perceived "aborted, then repeated itself" behavior is the interaction of **two independent,
fully verified defects**:

1. **Doubled greeting (repetition bug).** The delegate brain's reply already opens with a
   salutation, and the live model prepends its *own* salutation when reading the injected
   `spoken_reply` back. Proof: the stored `jarvis_text` is exactly 237 chars =
   `"Servas Ruben,"` (13 chars, live model's own opener) + the delegate brain's reply
   (exactly 224 chars per `BrainTurnCompleted.text_len`), which itself starts with
   `"Servas Ruben, i hätt …"`. <!-- i18n-allow: forensic quote of the spoken product-surface output under analysis -->
   No layer de-duplicates salutations on this path.
2. **Scrub-gate starvation (the 10 s pause).** `ScrubHoldGate` releases audio only against
   scrub-vetted transcript chars (coverage budget, 55 ms/char — BUG-069 design). Gemini Live's
   output transcription fell ~10–14 s behind its audio in this turn, so the budget ran dry right
   after the first greeting delta: audio kept arriving but stayed buffered. Log signature (turn 2):
   `mid-reply audio stalled 10483 ms before this chunk (scrub-gate hold 10420 ms)` at 15:11:56.670,
   and a second hold of 14 485 ms released only by turn-boundary `finalize()` at 15:12:11.375
   (`… 12276 ms beyond the vetted-text coverage estimate — the provider transcription lagged or
   stopped mid-turn`).

The stall lands **exactly between greeting #1 and greeting #2**, which is why it reads as
"started speaking, broke off, then started over". This is cause #1 (scrub-gate stalls) and a new,
now precisely-attributed sub-case of the delegate-readback family from the 2026-07-17 smoothness
forensic. Turn 1 of the same session shows the stall class is **not** delegate-specific: a native
(non-delegate) reply stalled 5 672 ms mid-sentence at 15:11:29.246.

## Verified timeline (turn 2)

| Time (local) | Event | Source |
|---|---|---|
| 15:11:41.077 | Turn 2 starts; final user text 66 ms later | `voice_events` |
| 15:11:41.142 | Routing: `path=orchestrator; reasons=private_data,uncertain` | LatencySpan `realtime_routing_decision` |
| 15:11:41.233 | Deterministic delegate dispatched (`kind=deterministic`) | desktop log + LatencySpan |
| 15:11:45.464 | Delegate brain reply ready: provider `gemini`, model `gemini-3.5-flash`, `text_len=224`, `finish_reason=STOP` | `BrainTurnCompleted` |
| ~15:11:45.5 | Trusted result injected into the live session (`spoken_reply`) | code path, see below |
| 15:11:46.061 | **First audio AND first transcript of the whole turn** (4 984 ms after commit) | LatencySpans |
| ~15:11:46.2 | Gate releases ≲1 s of audio (budget from the first transcript delta(s)); user hears "Servas Ruben," — then the gate starves; hold clock starts (15:11:56.670 − 10 483 ms ≈ 46.19) | derived: scrub-gate hold arithmetic <!-- i18n-allow: forensic quote of the spoken product-surface output under analysis --> |
| 15:11:56.670 | Transcript catches up → backlog released after a **10 420 ms scrub-gate hold**; playback resumes with the delegate reply read 1:1, re-greeting included | desktop log |
| 15:11:56.89 | Next chunk starts buffering again (second transcription stall) while the released backlog is still playing | derived (15:12:11.375 − 14 485 ms) |
| 15:12:11.375 | `finalize()` releases a 120 ms tail, 12 276 ms beyond the coverage estimate → logged as provider transcription stall | desktop log |
| 15:12:12.172 | User ends the session by hotkey (`reason=hotkey`) | desktop log |

Recorded turn totals: `latency_total_ms=31113`, `think_ms=4920` (the delegate round trip),
`speak_ms=26160` (first audio → turn end — this *includes* the silent holds; the actual audio is
~16–17 s for 237 chars at Gemini's measured ~14 chars/s).

Note: first audio and first transcript both arrive only *after* the delegate result delivery —
the tool-call generation produced no output, so **both greetings live inside the single readback
generation**. This rules out a barge-in/regeneration explanation: no `interrupted`, no
`REALTIME_CANCEL`, no barge-in marker exists anywhere in this session's logs or events.

## Root cause A — doubled greeting

Two layers each add a salutation, and nothing removes either one:

1. **The delegate brain greets mid-conversation.** `_run_deterministic_delegate`
   (`jarvis/realtime/session.py:3403`) dispatches the router brain; the persona-shaped reply opens
   with `"Servas Ruben,"` even though the conversation already exchanged greetings in turn 1. <!-- i18n-allow: forensic quote of the spoken product-surface output under analysis -->
   The delegate prompt includes recent history (`_DELEGATE_HISTORY_MAX_MESSAGES`), but nothing
   instructs or enforces "no fresh salutation mid-conversation".
2. **The live model greets again while reading back.** Delivery injects the reply either as a
   tool result (`session.py:3505-3512` / `3100-3105`) or as a text prompt
   (`_delegate_result_prompt`, `session.py:321`). Both the role directive
   ("deliver that content to the user in your own voice", `session.py:207`) and the result prompt
   ("Speak only a concise, natural rendering …") leave room for the model's natural instinct to
   open with an address. Gemini rendered: own greeting + the 224-char reply **verbatim** (the
   known 1:1 readback behavior).
3. **No de-duplication exists on this path.** The pipeline's salutation scrub
   (`removed_anrede_drift`, `jarvis/brain/output_filter.py:614-638`) only strips the "Sir" <!-- i18n-allow: established telemetry action-name identifier (ADR-0010), not prose -->
   honorific — it does not catch a repeated `"<greeting> <name>,"` opener, and in realtime the
   audio is the provider's native voice anyway: once the text reaches the model, the double
   greeting is already in the audio and cannot be scrubbed out.

Prompt compliance is not a correctness boundary (BUG-047 class rule), so the fix must be
deterministic and land *before* injection.

## Root cause B — the 10 s silence

`ScrubHoldGate` (`jarvis/realtime/scrub_gate.py`) is fail-closed by design (AP-11 / ADR-0010):
audio buffers until the matching transcript region has passed `scrub_for_voice`. Release is a
coverage budget — each vetted transcript char funds 55 ms of audio (BUG-069 redesign). The gate
therefore inherits the provider's transcription pacing:

- Gemini Live pushed the readback **audio** quickly (faster than realtime), but its
  **output transcription** stalled twice: ~10.4 s (released 15:11:56.670) and ~14.5 s (released
  only by `finalize()` at 15:12:11.375, 12 276 ms beyond the coverage estimate).
- After the first transcript delta(s) — the greeting — the budget covered ≲1 s of audio. Then
  starvation: the user got "Servas Ruben," and silence, while the full answer sat vetted-pending
  in the buffer. <!-- i18n-allow: forensic quote of the spoken product-surface output under analysis -->
- Same class, same session, native turn: turn 1 stalled 5 672 ms mid-reply (15:11:29.246). The
  2026-07-17 smoothness forensic counted ~20 incidents in 2 days, worst 22.5 s.

The bitter irony specific to **delegate** turns: the orchestrator already *possesses* the full,
trusted reply text minutes of audio before the provider's transcription confirms it — the gate is
waiting for a slow re-transcription of text we already have.

## Why the two bugs compound

The first stall boundary sits at the end of the funded budget — i.e. right after greeting #1.
So the audible experience is: greeting → hard cut → long dead air → the answer restarts *from a
greeting again*. Either bug alone would be an annoyance; together they present as
"it aborted and repeated itself", which is exactly how the maintainer reported it.

## Fix plan

### Fix A (repetition) — deterministic salutation handling for delegate readbacks

- Strip a leading salutation (`greeting word(s) + optional user name/brand + comma`) from the
  delegate reply before it is wrapped into `spoken_reply` / `_delegate_result_prompt`, whenever
  the session is past its first turn. Regex-only, language-table driven (all supported locales
  equal, §1 runtime-language rules), applied at the single injection choke point in
  `_run_deterministic_delegate` (and the late-delivery path) so both delivery vehicles
  (tool result and text prompt) are covered.
- Add one sentence to `_delegate_result_prompt` and `_DELEGATE_ROLE_DIRECTIVE`: do not add a
  greeting or address, start directly with the content. (Belt; the strip above is the braces.)
- Tests: unit tests for the stripper (de/en/es greetings, name-brand variants, first-turn
  exemption, no-greeting replies untouched), pinning an arbitrary agent brand per §4.

Risk: low; reversible; no doctrine change. It also shortens readbacks slightly (a known
secondary complaint: delegate answers are read back 1:1 and are too long).

### Fix B (silence) — pre-fund the scrub gate with the trusted reply on delegate turns — **Recommended**

When a trusted delegate result is injected, feed the (already scrubbed, already vetted) reply
text into the gate's coverage budget at delivery time — e.g. a `prefund_vetted_text(text)` that
runs the same `scrub_for_voice` aggregate checks and then credits `covered_chars`. Provider
transcript deltas keep flowing for display and hard-leak detection, but playback no longer waits
on Gemini's laggy re-transcription of text the orchestrator itself supplied.

- Removes **both** holds of this run (10.4 s + 14.5 s) and, per the smoothness forensic, the
  worst stall population (long delegate readbacks).
- Trust basis is unchanged in kind: the gate's `finalize()` *already* releases the entire
  un-vetted tail at every response boundary; crediting text that *did* pass the scrubber is
  strictly more conservative than that existing release. A hard leak in the trusted text
  still blocks (the prefund runs the same scrub and refuses on a hard result).
- Residual risk to name honestly: if the live model departs from the injected text and speaks
  *other* content, that audio is now funded by the trusted text's budget. Mitigation: cap the
  prefund at the trusted text's estimated duration (it already is, by construction) and keep the
  existing `fail_if_pending_exceeds` bound.
- Explicitly **out of scope**: native-turn stalls (turn 1's 5.7 s) have no trusted text to
  pre-fund. The candidate follow-up — a bounded mid-reply hold that fails *open* after N seconds —
  is a real ADR-0010 doctrine change and needs its own decision; not bundled here.

Recommended because it wins over the alternative (a time-bounded fail-open hold for all turns) on
risk and reversibility: it fixes the observed worst-case class without weakening the fail-closed
contract for un-vetted content.

### Verification

- Unit: gate prefund tests (budget credit, hard-leak refusal, drain resets), salutation-strip
  tests, existing `scrub_gate` suite stays green.
- Live: re-run the same two-turn script (greeting turn, then an action question that routes
  `path=orchestrator`) and assert in the log: no `scrub-gate hold` over ~1 s during the readback,
  and a single salutation in the recorded `jarvis_text`.

## Open questions for the maintainer

1. Should the delegate brain itself be told (prompt-level) not to greet mid-conversation, in
   addition to the deterministic strip? Cheap, but redundant once the strip exists.
2. Is the bounded fail-open hold for native turns worth an ADR discussion now, or wait until
   after Fix B's effect is measured?
