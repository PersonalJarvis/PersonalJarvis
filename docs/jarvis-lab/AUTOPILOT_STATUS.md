# JARVIS-LAB Autopilot

This branch is advanced through two execution modes:

1. Interactive work while a ChatGPT session is actively running.
2. Scheduled autonomous review and continuation once per hour.

The hourly pass must verify that `jarvis-lab` is progressing. If progress has stalled, it should resume the highest-priority unblocked JARVIS-LAB task, preserve the existing PersonalJarvis architecture, add focused tests where appropriate, and commit only to `jarvis-lab`.

Safety constraints remain unchanged:

- never merge automatically into `main`;
- never use destructive history rewrites;
- never publish releases automatically;
- never modify credentials or introduce paid services without explicit approval;
- defer native macOS permission prompts and physical-Mac qualification until a user-driven test pass;
- keep one orchestrator, one safety boundary, and one authoritative memory system.

Current workstream priority:

1. finish checking CI for the latest `jarvis-lab` HEAD; triage any new failures
   against the exact parent under the same environment;
2. MacAgentBench permission-degradation and prompt-injection-resistance
   coverage, after the dedicated cancellation-only receipt landed;
3. next highest-priority architecture gap that is not blocked on physical macOS testing.

Completed remotely in the current benchmark phase: physical-user-takeover,
semantic-target-hit, stale-target-refusal, focus-type-landing, cross-window-handoff,
browser-to-desktop-handoff and handoff-cancellation receipt contracts. Browser handoff tests now also
exercise the real tool's early return, including read-only mode, inactive caller
and kill-switch precedence, without reaching either browser executor.
Upstream main is merged through `6368c2e` with no overlapping
JARVIS-LAB paths in that sync. Native qualification remains deferred until an
explicit real-Mac pass can grant the required permissions and capture live
receipts.

## Remote validation: 2026-10-02 handoff contracts

- Audited parent: `bfaaf0255a986ae1ebb98acec1e4d95dab38fdfc`; PR #1 remains
  open and draft. Upstream `6368c2e201dd2e767080f0fad9d04207b1767e90`
  is already included; fork `main` remains separate (99 ahead / 1 behind at
  that parent).
- Focused Linux/Python 3.12 run: **80 passed** across
  `test_macos_bench.py`, `test_browser_handoff.py` and `test_macos_readiness.py`;
  38 new cases cover the two receipt evaluators and the browser tool boundary.
  Ruff and `git diff --check` pass. One dependency deprecation warning comes
  from FastAPI/Starlette's test-client import.
- Parent CI run `36983225556` at 08:31 UTC: 42 jobs successful, 4 Windows
  shards still running, 3 skipped. No failures observed at that check; this
  is not a completed CI result. Check the new HEAD separately after publication.
- This Linux checkout has no PowerShell, so `preflight.ps1` could not run;
  `import jarvis` was verified to resolve to this checkout. Test dependencies
  live in an isolated environment; no desktop installation was repinned.
- Scope: pure benchmark contracts and no-I/O test coverage only. No new
  orchestrator, safety boundary, memory store, desktop driver or permission
  request. Native macOS transitions and live receipt collection remain unqualified.

## Remote validation: cancellation contract

- The cancellation-only contract requires the handoff to be pending when
  cancellation is requested, cancellation to be observed and reported as a
  structured outcome, no intended effect, no browser/desktop actions after the
  request, and no resume after cancellation.
- Focused Linux/Python 3.12 run after the contract change: **97 passed** across
  the MacAgentBench receipts, browser handoff routing, macOS readiness and
  physical-input pause tests. Ruff and `git diff --check` pass; the existing
  FastAPI/Starlette test-client deprecation warning remains non-functional.

The scheduled pass is intentionally limited to the platform-supported maximum cadence of once per hour; it is not a continuously resident daemon.
