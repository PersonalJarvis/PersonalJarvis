# ADR-0036 — Continuous voice and one active agent selection

**Status:** Implemented; cross-platform release acceptance pending
**Date:** 2026-09-19
**Reference:** GPT-Live migration; ADR-0035

## Context

The legacy realtime session combines provider events, turn routing, task
delegation, audio and transcript handling. The separate Tool Model setting can
also select a different billing account from the user's active agent. GPT-Live
provides continuous voice with managed Responses delegation, requiring different
session and continuation semantics from the older Realtime API.

## Decision

The continuous voice adapters use `jarvis/live` and a shared tool gateway.
OpenAI uses the explicitly chosen Responses thinking model for delegation and
voice-triggered computer control, with the same OpenAI credential. Gemini and
local providers use their own tool-capable protocol; capability failures are
reported explicitly. All local actions still pass through ToolExecutor.

Browser/WebView audio owns simultaneous microphone capture and playback.
OpenAI uses WebRTC with a server control connection, or a primary WebSocket for
server audio. Native provider adapters preserve their required turn mechanics.
Neither the routing heuristics nor forced result readback in ADR-0035 apply to
the continuous OpenAI adapter. Cloud recording is disabled by default.

Text chat and scheduled tasks use the selected `brain.worker` access. A task
captures that selection at entry and carries it through nested operations;
changing settings only affects later tasks. Subscription execution uses scoped
Jarvis MCP grants and fails explicitly when runner isolation is unavailable.

Speech termination leaves already-started work alive. Explicit task cancellation
invalidates the current execution generation and its approvals. A new user
request can proceed without reviving the cancelled work. Receipts, revisions,
argument validation and approval checks remain application responsibilities.

Reconnection pays a shared budget and adds jitter. Only sessions without pending
actions, uncertain results or approvals recover automatically. Recovery seeds
bounded history and verified receipts, discards buffered audio and requires new
user input before further execution. Interrupted usage stays unconfirmed.

## Consequences

- One OpenAI credential serves voice and its thinking backend; connected plugins
  still require their own authorization.
- Model changes require an explicit settings choice; no hidden billing fallback
  substitutes another account for a selected agent.
- The full catalog remains reachable through discovery and invocation even when
  the initial provider declaration set is bounded.
- Unsafe-to-replay sessions end with an actionable error instead of resuming an
  action whose outcome is unknown.
- Legacy sessions and configuration aliases remain during qualification.
  Their removal is not established by a synthetic API test.

## Alternatives considered

- Renaming the legacy Realtime model: rejected because Live delegation and
  continuous audio have different event and continuation contracts.
- Removing local authorization with the old router: rejected because model
  intelligence does not provide action receipts or user approval.
- Replaying every disconnected tool call: rejected because the transport can
  fail after an external side effect has already happened.

## Acceptance still required

This is a T3 change. Contract tests and forced-loss probes cover protocol and
execution semantics, not the complete product matrix. A fresh Python 3.11 slim
wheel installation boots without PortAudio, passes the Live contracts and an
isolated one-key synthetic tool/reconnect probe. The settings form has been
inspected in both themes. Native Windows, macOS and Linux audio, fresh desktop
installations, complete one-key and subscription journeys, offline tools, all
tool families, long conversations, and a comparative median-latency/success-rate
benchmark remain release gates. The catalog still needs per-model capability
qualification, including reasoning levels and native asynchronous tool support.

Keep the migration draft until those results are recorded. Do not remove the
legacy recovery path or claim a latency improvement from the synthetic probe's
single first-audio measurement.
