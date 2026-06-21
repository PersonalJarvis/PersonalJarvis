# Cross-Platform Computer-Use Recovery Design

Date: 2026-06-21
Status: Draft for user review

## Problem

Computer-Use is no longer primarily failing because it cannot route or because
Grok cannot see screenshots. The current evidence shows that explicit
Computer-Use requests reach the screenshot loop, Grok vision works, and the
targeted routing/harness tests pass.

The live failure shape is now runtime quality:

- The Computer-Use loop builds the BrainManager fallback chain, but it does not
  apply the same provider error classification that normal chat turns apply.
- Each planning/verifier call repeatedly tries known-bad providers before the
  working vision provider:
  - antigravity is text-only for screenshots and is skipped correctly.
  - gemini currently returns 429 / depleted credits.
  - claude-api currently returns 401 invalid key or provider-side 5xx.
  - grok is the first working vision provider and succeeds.
- This repeats inside one mission, so each step pays unnecessary latency and
  makes the loop look broken even when it can eventually plan correctly.
- Read goals can be marked incomplete because "app is open" and "useful content
  was read and returned" are not separated cleanly enough.

## Goals

1. Keep Computer-Use cross-platform. The fix must sit above the platform
   capture/action backends, not in a Windows-only API.
2. Keep provider independence. No provider-name or model-id gate should decide
   whether Computer-Use works. Use capabilities and health state.
3. Make the working vision provider reachable fast within a mission.
4. Make failures honest and actionable: no silent acknowledgement without action,
   no blind planning, no repeated known-bad provider probes.
5. Preserve current safety boundaries for UI automation and confirmations.

## Non-Goals

- Do not replace the screenshot loop with the OpenAI/Windows Computer Use
  plugin. That would be Windows-only and would not solve macOS/Linux.
- Do not pin Computer-Use to Grok. Grok is currently healthy, but the design
  must work for any provider with `supports_vision=True` and usable credentials.
- Do not redesign all capture/action backends in this pass.
- Do not loosen high-risk UI confirmation policy.

## Architecture

Add a small provider-selection layer for Computer-Use planning calls:

`ComputerUsePlannerSelector`

Responsibilities:

- Build a mission-local candidate chain from `BrainManager._build_fallback_chain("fast")`.
- Filter by runtime health and capability:
  - skip providers in `_dead_providers`.
  - skip provider/model pairs under `_rate_tracker` cooldown.
  - when screenshots are attached, skip brains where `supports_vision` is false.
- Classify provider failures with the same semantics as `BrainManager.generate()`:
  - 429 / rate limit -> mark `_rate_tracker`.
  - missing/invalid auth such as 401 invalid key -> mark provider dead for the
    session.
  - terminal account/quota/billing problems -> mark provider dead for the
    session and report account-blocked.
  - transient 5xx/network failures -> record for the current call, do not mark
    permanently dead.
- Cache mission-local skip decisions so one failed provider is not retried on
  every planner, verifier, or click-refine call in the same mission.

The screenshot loop continues to call a single helper for "ask a brain with
these images and this prompt", but that helper delegates selection to the new
selector instead of open-coding provider iteration.

## Data Flow

1. User request reaches `match_local_action` or `computer_use` tool.
2. `ComputerUseContext` starts `run_cu_loop`.
3. The loop captures a screenshot through the existing `VisionEngine`.
4. `_call_brain` builds a `BrainRequest` with the screenshot image.
5. `ComputerUsePlannerSelector` selects the first healthy vision-capable brain.
6. The selected brain returns one JSON action or verifier verdict.
7. The existing action registry executes through the platform backend.
8. The verifier evaluates goal completion by goal type.

## Goal-Type Verification

Keep the existing verifier structure, but make the success contract explicit:

- Open-app goals: success is the requested app/window visibly open.
- Read/informational goals: success requires extracted visible content or a
  concise answer proof, not merely the app being open.
- Play/media goals: success requires evidence of playback or motion/state
  change; a frozen screen must not be rescued by a generic "done" answer.
- Submit/send/upload/delete/security-sensitive goals: preserve action-time
  confirmation requirements.

## Error Handling

Provider call errors should update shared BrainManager health state where safe:

- Rate limit: `manager._rate_tracker.mark_rate_limited(provider, model)`.
- Missing/invalid key: `manager._dead_providers.add(provider)`.
- Account blocked/no credits: `manager._dead_providers.add(provider)`.
- Vision unsupported: mission-local skip only unless the brain capability itself
  is static false.
- Transient provider/network failure: no session-dead mark; record and fall
  through.

If no usable provider remains, the loop returns a clear Computer-Use failure:
"Computer-Use needs a vision-capable provider, but all configured candidates are
blind, unavailable, rate-limited, or misconfigured", with a short diagnostic in
stderr/logs.

## Cross-Platform Boundary

This design does not change how screenshots are captured or how clicks/typing
are executed. Windows, macOS, and Linux can keep separate implementations under
the existing `VisionEngine` and action registry. The new selector only decides
which brain is allowed to plan from an image.

## Tests

Add focused tests around the selector and existing loop integration:

- A blind active provider is skipped for screenshot calls.
- A 429 provider is marked rate-limited and skipped on the next CU sub-call.
- A 401 invalid-key provider is marked dead and skipped on the next CU sub-call.
- A transient 5xx provider is skipped for the call but not marked dead.
- A healthy vision fallback receives the image and returns an action.
- Read goals reject "app open" unless visible content/proof is returned.
- Existing routing suites remain green:
  - explicit Computer-Use routing.
  - Computer-Use vs spawn routing.
  - Computer-Use tool/offload.
  - screenshot-loop robustness/runaway guards.

## Rollout

1. Implement selector behind the existing Computer-Use path with no config flag.
2. Run targeted unit suites.
3. Run a live smoke with a harmless read-only goal:
   "Use computer use to look at my screen and tell me the foreground window."
4. Run one app-read smoke:
   "Open Discord and tell me what is visible in the selected channel."
5. Only after those pass, consider deeper coordinate/click-refine work.

## Implementation Decisions

- 429 messages that mention depleted credits, billing, quota exhaustion, or
  payment problems are treated as account-blocked for the session, not as a
  short cooldown. Plain transient 429/rate-limit messages stay cooldown-only.
- The selector starts in `jarvis/harness/computer_use_planner.py`. Broader
  provider-health extraction can happen later only if a second caller needs it.
