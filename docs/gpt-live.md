# Continuous voice

GPT-Live is an experimental voice path with a separately selected Responses
thinking model. Both use the same OpenAI credential. Voice duration and backend
tokens are accounted for separately. The application does not enable cloud
recording (`store: false`).

## Settings and migration

Select the voice, thinking model, reasoning effort and web search in API Keys.
Saving creates a pre-migration configuration backup and applies to the next
call. A model is never chosen or upgraded automatically. Gemini/Vertex and local
realtime remain available. Provider-specific function schemas are translated at
the adapter boundary.

Text chat and background work follow the active Agents selection. The OpenAI
voice thinking model is also used for voice-triggered computer control without
another model key. Connected services retain their own credentials.

## Execution and media

The `jarvis/live` package separates the provider connection, transcript stream,
tool receipts and execution. GPT-Live uses WebRTC media plus a server-side
control connection, or a primary WebSocket for server audio integrations.
Browser/WebView capture owns echo cancellation; the voice engine does not mute
the microphone merely because it is speaking.

Tool execution remains behind the supervisor gateway and ToolExecutor. The
Responses adapter retains complete function calls, sends all required outputs
and explicitly continues the response. Discovery keeps tools reachable beyond
the initial declaration set. Screenshots enter a vision-capable backend as
images, with existing privacy settings enforced at capture.

Operation receipts prevent the same call ID from executing twice. Uncertain
results are not retried automatically. Confirmations belong to the application,
and already-started work can finish after speech closes. Subscription tasks use
the same gateway with a scoped tool grant; unsupported isolation is reported
instead of silently using another account.

An explicit task cancellation invalidates queued calls and pending approvals.
The next user request gets a new cancellation token, while interrupted work
retains its cancelled token. Scheduled turns keep their starting agent selection
through settings changes, including nested computer-control calls.

Transcripts keep their original fragments and timestamps. The legacy archive
stores one compatibility group at close, not a fabricated provider turn boundary.
Voice duration updates are cumulative snapshots. Backend completion, generated
speech and actual playback are separate states.

## Qualification

Synthetic live API probes have exercised tool execution, response continuation
and spoken results on OpenAI and Gemini. Contract tests run on Windows and in a
headless Python 3.11 Linux container. A fresh base wheel installation on
`python:3.11-slim` boots a healthy API without PortAudio. An isolated OpenAI
synthetic probe using exactly one credential also passes there, including a
forced reconnect and exactly one tool execution. These checks do not establish
native audio parity or release readiness. Native-device audio, fresh desktop
installations, long-session recovery and comparative latency still require qualification.

Safe transport failures reconnect with jitter and a shared connection budget.
Recovery restores bounded conversation history and completed tool receipts;
it never replays buffered microphone audio. New actions wait for fresh user
input. Pending approvals, running actions or uncertain outcomes prevent automatic
reconnection. Missing final usage remains marked unconfirmed across reconnects.

Synthetic OpenAI and Gemini probes have recovered from forced connection loss,
spoken the previous verified result and kept the tool execution count at one.
These probes do not replace long-call or native-device qualification.

Run `python scripts/verify_gpt_live.py --run-live` for the opt-in OpenAI synthetic
probe. The test uses configured credentials and incurs normal API usage; it
captures neither microphone nor screen data. Gemini is selectable with
`--provider gemini --model gemini-3.1-flash-live-preview`.
Add `--reconnect` to verify recovery after a forced connection loss.

The architecture decision and remaining acceptance requirements are recorded in
[ADR-0036](adr/0036-continuous-voice-and-agent-selection.md).

The settings form has been inspected in light and dark themes. Its provider
test uses the continuous session contract and waits for `session.started`;
browser-owned media no longer waits for the legacy desktop offer broker.
The separate Live profile owns model and voice selection without duplicate
legacy pickers on the provider card.

References: [OpenAI architecture](https://developers.openai.com/api/docs/guides/live),
[delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[migration](https://developers.openai.com/api/docs/guides/live-migration), and
[Gemini tools](https://ai.google.dev/gemini-api/docs/live-api/tools).
