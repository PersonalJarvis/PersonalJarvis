# Delegation follow-through

## What was present

Jarvis already dispatched Society assignments without waiting for their model
turns, retained their request IDs, watched turn completion, and recorded results
on the board. Internal questions had parent-message correlation and explicit
reply policies. Coding panes had submission receipts, a process-aware activity
watcher, structured transcript adapters and notifications. The speech pipeline
already supported deferred announcements, live-model report synthesis and
speaker-drain confirmation.

The missing connection was result delivery: Society and coding-pane speech had
been removed, while dispatch acknowledgements could still promise a report.
Society notices targeted the newest front-page chat, rather than the requesting
chat. Those notices were not included when rebuilding model conversation history.
Re-enabling the old implementation would also have restored a separate paid
summary composer and inherited weak freshness checks for terminal answers.

## Resulting behavior

- Explicit Jarvis voice delegations return through the existing live voice
  conversation. The original task, source, reported status and evidence accompany
  every result. The active model summarizes these in its own voice. Classic TTS
  has a deterministic short fallback. There is no additional summary model call.
- Text-chat delegations return to their originating conversation and remain in
  its subsequent model context. They do not speak into an unrelated voice call.
  A deleted origin is never redirected into a newer chat.
- A 750 ms coalescing window groups up to four results. Longer lists drain in
  subsequent batches. Each result keeps a separate context budget, including its
  beginning and ending. The queue never waits for unfinished sibling tasks.
- Delivery waits for LISTENING, including a second check after the coalescing
  window. User speech, foreground processing, existing speech, mute and hangup
  retain results instead of interrupting. Only one live readback can be in flight.
  Speaker completion confirms delivery; interrupted readbacks retain the existing
  retry-on-next-call behavior. Reports already enter the active conversation's
  context while awaiting speech.
- Direct agent chat, agent-to-agent traffic, manual terminal input and work
  owned by a Society coding supervisor retain their own reporting owner. Reply
  policies (`always`, `on_error`, `none`) remain authoritative.

## Completion and uncertainty

Society completion is a typed lifecycle event, not the dispatch receipt. A
correlated question or proposal reaches the user as needing input; the following
turn end records a blocker rather than manufacturing success or a duplicate report.

Coding receipts belong to one submission and process generation. A replacement
process or subsequent manual prompt invalidates them. A recorded answer must be
new relative to the pre-submission transcript and answer the exact submitted
prompt. An old answer to an identical prior prompt is insufficient. Short jobs
that finish between activity sweeps are recovered from fresh recorded evidence.
Remote panes never read their stale local transcript as a current result.

A settled terminal is not independent proof of task success. When the actual
answer cannot be established, the report explicitly identifies that uncertainty.
Questions, process exit and startup failure have separate outcomes. No result
automatically grants permission or starts another tool operation.

## Implementation boundaries

- `core/delegation.py`: trusted origin capture, evidence envelopes and bounded
  batching/deduplication; no provider or boot-time initialization.
- `society/runtime.py`: request-correlated answers and completion reports;
  existing scheduler and board remain the execution owners.
- `agentic_ide/followthrough.py`: submission ownership and transcript freshness;
  existing notification sweeps supply activity evidence independently of the bell.
- `speech/pipeline.py` and `realtime/report_prompt.py`: conversational scheduling
  and synthesis through the selected live model.
- `agent_chat/runner_api.py` and the server's result bridge: existing persisted
  notice events carry results back into text-chat history.

No REST route, provider, permission tier, model default or frontend appearance
changes. Result tracking does not hold a speech turn open and does not poll a model.

## Verification and limits

Focused tests cover batching, deduplication, user/assistant floor ownership,
mute/hangup, arrivals during playback, prompt freshness, superseded work,
questions, failed submission and text-chat origin. Integration tests exercise
the real Society scheduler, SQLite board, event bus and speech inbox with fake
providers, including two concurrent assignments and question-before-completion.
Existing provider readback and speech lifecycle tests exercise the shared adapter
contract. All tests are run without paid provider probes or browser processes.

The terminal tracker and voice queue are process-local. Society board results and
chat notices persist, but automatic spoken replay after an application restart
is not promised. Terminal detection retains the existing activity watcher's
settle latency and uncertainty for unreadable or remote transcripts. Text notices
use deterministic excerpts; live voice performs contextual synthesis. Real-device
voice quality and non-Windows runtime behavior require separate live validation.

The broad Windows regression runs passed 995 and 698 tests respectively (one
skip in the first run). Two failures were reproduced individually on the exact
unchanged base commit `ab09f5b13`, with the same assertions and causes:

- `test_routine_approval_delegation_is_only_for_society`: the existing routine
  permission expectation no longer matches the base implementation.
- `test_jarvis_chat_receives_controller_without_becoming_a_coding_cli`: its fake
  session lacks the provider field required by the existing browser tool builder.

Neither failure was hidden or added to a baseline. The final focused pane and
integration rerun passed 68 tests; changed Python files passed Ruff and the
documentation passed the privacy scan. The four required routing, output-filter,
hangup-parity and turn-language guards are included in the second broad run.

After integrating with GitHub main `2971510a5`, 102 focused lifecycle, Society,
workspace, speech and integration checks passed again. The existing guard against
claiming success from an empty agent report was preserved during integration.
CLI route coverage passed; the terminal-prompt route keeps its public schema.
