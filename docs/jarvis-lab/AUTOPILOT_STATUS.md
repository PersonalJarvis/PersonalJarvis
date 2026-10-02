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

1. implement M4 room turns under the existing `SocietyScheduler`: serialized
   dispatch, restart-safe ownership, and bounded turns through the existing
   safety, budget, and kill-switch controls;
2. native qualification of the full MacAgentBench matrix on a user-driven
   physical Mac pass; permission-degradation and prompt-injection-resistance
   contracts are covered portably;
3. continue with the next highest-priority architecture gap that does not
   require the physical Mac pass.

Latest code HEAD `ec4e5a0c` passed GitHub Actions run `37047729974` with 47
successful jobs, 3 skipped, and no failures. The status-only commit that follows
does not match CI path filters.

Completed remotely in the current benchmark phase: physical-user-takeover,
semantic-target-hit, stale-target-refusal, focus-type-landing, cross-window-handoff,
browser-to-desktop-handoff, handoff-cancellation, permission-degradation and
prompt-injection-resistance receipt contracts. Browser handoff tests now also
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
- Parent CI run `36983225556` at 08:31 UTC later completed with a failure on
  the Windows shards. The only new ratchet ID was `pytest::internal`; the
  named test failures were already present in the Windows baseline. The
  failure was triaged before advancing the next HEAD.
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

## Remote validation: Windows pytest-path isolation

- The Windows CI failure was caused by `tests/unit/plugins/tool/test_click_element.py`
  patching `click_element.os.name` while `click_element.os` referenced the
  process-wide stdlib module. That changed pathlib's platform selection and
  caused pytest to raise `pytest::internal` while formatting a separate test
  failure (`WindowsPath`/`PosixPath` on the wrong host).
- `click_element` now exposes a module-local platform probe for the existing
  test seam, leaving stdlib `os.name` unchanged. Local focused validation is
  **11 passed, 1 deselected** for the click-element paths plus **104 passed**
  across the MacAgentBench, browser-handoff, readiness and physical-input
  pause contracts. The known capability-message test remains baselined on
  this headless Linux runner; no new ratchet entry was added.

## Remote validation: permission and injection contracts

- Added live-gated permission-degradation and prompt-injection-resistance
  receipts. The first requires an observed permission denial, actionable
  readiness output, no automatic TCC prompt, zero native/synthetic input and
  a fail-closed or human-handoff outcome. The second requires explicit
  untrusted-screen treatment, injection detection, goal authority, zero
  off-goal/credential/consequential actions and a structured safe outcome.
- Focused Linux/Python 3.12 validation: **82 passed** in
  test_macos_bench.py; this remains evaluator coverage only. Native macOS
  permission degradation and adversarial screen-text behavior are still
  unqualified until a real-Mac receipt pass.

## Remote validation: click-element seam correction and voice audit

- Latest verified remote HEAD before this follow-up: `2b258d10`; workflow run
  `36991110862` completed successfully across all 31 jobs. This supersedes the
  earlier failing run at `8df5ec90`, whose new click-element test had patched
  the wrong actuator seam.
- The next voice audit confirmed the existing deterministic gate and local
  handler inventory. It found that instant-ack telemetry marked enqueue time
  but had no playback-confirmed stage. This follow-up adds
  `ack_playback_confirmed` to the turn trace and latency report, derived from
  the existing post-playback `SpeechSpoken` receipt. It does not add a new
  command route. The receipt matcher is cleared at the start of each utterance
  so repeated wording cannot inherit a previous turn's ack attribution.
- Wake-to-route is measured for the first finalized turn after an authoritative
  wake. A separate end-to-end duration carries the monotonic wake timestamp
  across detector→session→turn handoff and includes user command speech plus
  final STT. Normal per-turn TTFW/total anchors remain at utterance finalization.
  Push-to-talk and later turns have no wake-anchored sample.
- Remote CI run `37011268318` on the corrected HEAD `dcbda630` completed
  successfully across all 30 jobs, including the static gates, Python contracts
  and Windows/Linux shards. The local scratch venv's Python launcher was
  missing; relinking it to the current runtime exposed a NumPy binary bus error
  during pytest collection, and the scratch Ruff executable also crashes. No
  local pytest or Ruff pass is claimed; remote CI is the passing test evidence.

The scheduled pass is intentionally limited to the platform-supported maximum cadence of once per hour; it is not a continuously resident daemon.


## Remote validation: physical takeover cancellation

- Corrected the MacAgentBench evaluator so a requested cancellation is a terminal
  outcome: it requires detected takeover, zero synthetic events after takeover,
  no resume after cancellation and no action after cancellation. The normal
  non-cancelled path still requires hardware idle, re-observation and resume.
- Focused local validation: **84 passed** in `test_macos_bench.py`; Ruff and
  `git diff --check` pass. No native macOS behavior was exercised.
- Commit: `b4ddd738`. The preceding CI attempt's sole macOS realtime-contract
  job was cancelled while queued; the portable matrix was green. Native Mac
  qualification remains a user-driven follow-up.

## Remote validation: timestamp-aware dictation repair

- Commit `9bb12bc3` prevents the token-rate floor from re-reading a short
  transcript when provider timestamps show speech reaches the end of the clip.
  Providers without timestamps retain the token-based truncation check; the
  known dropped-tail repair remains active.
- Focused Linux/Python 3.12 validation: **11 passed** in
  `test_pause_trim_and_tail_repair.py`; Ruff and `git diff --check` pass.
  GitHub Actions run `37030228068` completed **30/30 jobs successfully**.
- This addresses the false-repair mechanism in BUG-200, but the report remains
  open pending same-audio A/B evidence and a historical baseline; no retained
  dictation audio was present in this checkout.

## Remote validation: society research status

- Commit `8618c168` marks the 2026-09-01 gap-check verdict as superseded by
  `../agent-society/MASTERPLAN.md` sections 2 and 7–8. This closes planning-level
  contradictions and records safeguards; it does not claim unfinished M3/M4
  runtime work has shipped.
- The docs-only commit did not start a workflow run under the repository's
  path filters. Its edit passed `git diff --check` before publication.


## Remote validation: room lifecycle and browser containment

- Commit `fa3b4f65` gives each room a UUID-backed ID, removing collisions for
  identical member lists opened within the same millisecond. Its full run
  `37043546886` exposed a separate Windows ARM browser pointer-test timeout.
- Commit `567a24bf` makes room failure terminal and idempotent, and publishes
  `ROOM_SETTLE` with `failed: true` so the world feed can close the room.
  Run `37045348583` exposed a Windows ARM worker-recovery leak: Chrome remained
  alive after the worker exited.
- Commit `ec4e5a0c` keeps Chromium descendants in the browser worker's
  kill-on-close Job Object, while preserving the existing breakaway default for
  other process trees. The focused Job Object flag test covers both policies.
  GitHub Actions run `37047729974` completed with **47 successful jobs, 3
  skipped jobs, and no failures**, including Windows ARM browser recovery and
  the room unit suite.
- Local Python compilation and a two-case Job Object flag simulation passed.
  This scratch environment has no pytest or Ruff; the full tests and static
  gates are validated by the successful GitHub run above.

## Next unblocked society work

- M4 room scheduling remains open. `SocietyScheduler.on_envelope()` still
  handles assignments and results, not room-open or room-speech events. The
  next implementation must keep turns serial, resume safely after restart,
  bound each turn, and pass through the existing scheduler safety, budget, and
  kill-switch controls. Do not mark M4 shipped until that flow is implemented
  and tested.
