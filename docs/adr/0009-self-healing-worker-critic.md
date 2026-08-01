# ADR-0009 — Self-healing mission worker and Critic loop

**Status:** Accepted
**Date:** 2026-04-26
**Updated:** 2026-08-01

## Context

Long-running user tasks need more than a single model/tool call. They must
survive recoverable failures, keep edits isolated, expose progress, and return
only results backed by evidence. The voice/router process must remain responsive
while this work runs.

## Decision

Heavy tasks run as persistent missions owned by `jarvis/missions/`. Every
mission receives a fresh git worktree and a contained worker process. The
worker streams observations and artifacts but cannot approve its own output.

A separate Critic evaluates the result and returns a structured verdict. A
failed verdict may produce a correction instruction and a fresh attempt. The
constant `MAX_CRITIC_LOOPS = 3` is binding and is not configurable. After the
Critic accepts, a deterministic Controller validates terminal evidence and
signs the user-facing completion state.

The action/observation boundary is strict: model-authored prose is an action or
claim, not an observation. File existence, command results, tests, hashes, and
other externally checked facts form evidence. Voice/chat readback uses only the
approved terminal summary and always passes through the normal output-language
and voice-scrub path.

## Failure and recovery

- Worker crash or timeout terminates the contained process tree.
- A recoverable attempt failure may enter the Critic correction loop.
- Cancellation is explicit and idempotent; voice hangup alone is not cancel.
- Supervisor restart may reconstruct persisted mission state, but never assumes
  a previous worker process is still trustworthy or alive.
- Exhausted retries end in an honest failed state with retained evidence.

## Safety boundaries

- Workers operate inside their mission worktree and per-mission state root.
- Supervisor credentials and tool objects do not cross the process boundary.
- Delegated tools use short-lived mission grants and execute through
  `ToolExecutor` in the supervisor.
- Recursive spawn, secret access, skill activation, and unrestricted config
  mutation are never granted to a worker.
- Every subprocess follows the platform containment and no-window rules.

## Consequences

- The router stays responsive while heavy work proceeds asynchronously.
- Failures are visible and bounded instead of silently retried forever.
- Worktree creation, cleanup, event persistence, and Controller evidence checks
  add complexity and disk use.
- A successful worker answer can still be rejected when it lacks independent
  evidence; this is intentional.

## Alternatives considered

- **Single-shot execution:** rejected because transient failures and unsupported
  claims would reach the user directly.
- **Worker self-review:** rejected because the same context would judge its own
  mistakes and evidence.
- **Unbounded autonomous repair:** rejected because it has no predictable cost,
  latency, or safety ceiling.
- **Edits in the user's active worktree:** rejected because concurrent missions
  would race and partial failure could corrupt user work.

## Verification

The binding tests are under `tests/missions/`, including Critic verdict,
Controller evidence, cancellation, worktree isolation, recovery, and worker
runtime tests. Related contracts are documented in
`docs/jarvis-agents-bridge.md` and ADR-0025 through ADR-0027.
