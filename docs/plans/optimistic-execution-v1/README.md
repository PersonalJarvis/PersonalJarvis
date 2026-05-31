# Optimistic Execution v1 — Implementation Plan

> Derived from `~/Downloads/Architektur-Spezifikation_ Personal Jarvis.md` (v1.0).
> Authored as a **reality-aligned close-the-gap plan**, not a greenfield plan:
> Phase 0–7 of this repo already implement and exceed the v1.0 seed vision.
> Language: English (repo `CLAUDE.md` Output-Language Policy for `docs/plans/`).

---

## 0. Reality Check (READ FIRST)

The v1.0 spec is the **seed DNA** of this project. The four pillars it describes —
Optimistic Execution, the Router/Worker split, Smart-vs-Dumb tool routing, and the
"Oops" protocol — are already wired into the live codebase. This plan therefore does
**not** rebuild them; it closes the four real gaps between "mostly there" and "the
v1.0 promise, end-to-end, measured".

| v1.0 concept | Live component | Status | Gap this plan closes |
|---|---|---|---|
| Main Jarvis (Talker) | Router-Brain (`jarvis/brain/factory.py` `ROUTER_TOOLS`) + Ack-Brain (`jarvis/brain/ack_brain/`) | exceeded | ACK must fire *before* dispatch, guaranteed |
| Heavy-Duty Worker ("4.6") | Mission-Manager + Worker-Critic + `claude-cli` Sonnet (OAuth) | live | success-rate telemetry |
| Event-Bus / Task-Queue | `jarvis/core/bus.py` + mission event store | live | latency spans on the bus |
| Optimistic Execution | Force-spawn ACK + `BrainManager._should_force_openclaw` | partial | make it the guaranteed default path |
| Smart Tools (Gmail/MCP) | `jarvis/mcp/adapter.py` + OpenClaw worker | Welle 2/3 open | worker-makes-the-call, end-to-end |
| Dumb Tools (local scripts) | `jarvis/brain/local_action_gate.py` | gaps (BUG-020) | allowlist coverage + zero false-spawn |
| Oops protocol / VAD / organic correction | Always-speak guards (BUG-020) + announcement path + Silero VAD | **no closed loop** | the full error→inject→VAD-gated→correct loop |
| Latency < 3s | AsyncIterator streaming + `voice_latency` marker | **no SLO gate** | p95 budgets enforced in CI |

---

## 1. Deep-Dive Workshop — 5 specialist lenses

### 1.1 Zero-Latency Architect
- The optimistic ACK exists (Ack-Brain + force-spawn) but is **not the guaranteed
  default** — the router still decides synchronously whether to spawn. The "Geht klar"
  utterance MUST be emitted before the spawn dispatch returns.
- Nail the latency budget *per stage*: wake-end → first audible ACK syllable < 1.2 s;
  intent-end → ACK utterance complete < 3.0 s. Anything blocking the talker on an MCP
  round-trip is a bug, not a slow path.
- The routing *decision itself* is on the hot path. Measure it. It must stay < 150 ms
  and never await a network call.

### 1.2 Asynchronous Engineer
- The "silent context package" handoff = a `MissionSpawn` bus event carrying the chat
  transcript; the background worker = Mission-Manager + `claude-cli` Sonnet (OAuth) in a
  git worktree wrapped in a Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
- Delivery semantics already exist: recovery sweep marks stranded `PENDING` missions as
  `OrchestratorCrash` (BUG-016). Make the success/failure rate a first-class metric read
  off the event store.
- Do **not** introduce Kafka/Redis/Celery. The in-process `EventBus` + mission store is
  the queue. An external broker would violate the €5-VPS cloud-first doctrine (no new
  hard dep). A channel adapter for multi-user VPS is a later, optional layer — not a
  rewrite.

### 1.3 MCP & Tooling Specialist
- Spec-correct invariant: the **worker** (not the talker) issues the MCP call. Gmail is
  the canonical Smart-Tool path via `jarvis/mcp/adapter.py` + OpenClaw worker; it is
  live-testable through the `gws` CLI / Gmail MCP.
- Dumb tools (local scripts: "mach die Adjusties", "spiel X ab") resolve in-process via
  `jarvis/brain/local_action_gate.py` — no worker wakeup, milliseconds.
- Tool-surface discipline is sacred: `ROUTER_TOOLS` is a frozenset; a worker tool set
  must NEVER contain `spawn-openclaw`/`dispatch-with-review`/`run-skill` (D9 recursion,
  AP-5/AP-14).

### 1.4 UX & Error Designer
- The "Oops" protocol is the **largest real gap**. Today: always-speak guards (BUG-020)
  + announcement path. Missing: the closed loop *error-event → inject into context →
  wait for VAD turn-boundary → organic correction*.
- VAD discipline: the correction utterance attaches to the next turn-boundary (Silero
  VAD in the turn-taking layer), never mid-utterance (barge-in-safe).
- Every spoken correction routes through `scrub_for_voice` (`pipeline.py:647`
  announcement path) so a tool name / address / secret never leaks into TTS.
- Failure taxonomy: recoverable (ask the user) vs. retryable (silent retry) vs. fatal
  (audit + brief apology). **Zero silent drops** — the anti-BUG-020 invariant.

### 1.5 QA & Benchmarking Analyst
- TDD per micro-task. The repo already has contract/unit/integration/e2e buckets, fakes,
  and the JARVIS-20 benchmark convention — reuse it as `OPTIMISTIC-20`.
- Hard SLO gates in CI on M1–M5 below. A latency regression fails the build.
- Hard-negatives to encode: ACK claims "done" while a synchronously-failing worker has
  no oops path; a dumb tool wakes the heavy worker; a correction interrupts mid-utterance;
  a secret reaches the correction utterance.

---

## 2. GOAL

Keep a single uninterrupted spoken conversation alive while all real work runs
asynchronously: the Talker acknowledges optimistically in < 3 s and never blocks on an
MCP round-trip, the Heavy-Duty Worker executes Smart-Tool calls in the background off the
chat transcript, and any background failure self-corrects organically at the next
turn-boundary with zero silent drops.

## 3. END STATE

- The user speaks naturally; the Talker replies in < 3 s for every turn, including turns
  that trigger heavy work ("Geht klar, kümmere mich drum"), because the ACK is emitted
  **before** the worker dispatch completes.
- Smart Tools (Gmail, Calendar, Drive) are formulated and called by the background worker
  off the chat context; the user has already moved to the next topic.
- Dumb Tools (local scripts) fire in-process in milliseconds without waking the worker.
- When a background task hits a missing-prerequisite (e.g. no email address for Max), the
  Talker injects the error silently, waits for the user's current utterance to end, then
  organically asks for exactly the missing piece — the flow never breaks.
- Every latency, success-rate, and recovery metric is observable via a telemetry endpoint
  and gated in CI.

## 4. METRICS & KPIs

| ID | Metric | Threshold | Source |
|---|---|---|---|
| M1 | p95 wake-end → first audible ACK syllable / p95 intent-end → ACK complete | < 1.2 s / < 3.0 s | bus latency spans |
| M2 | p95 router decision latency (talker-only, no worker) | < 150 ms | router span |
| M3 | background mission success rate (`COMPLETED` / dispatched, rolling window) | ≥ 95 % | mission event store |
| M4 | Oops coverage = every worker/MCP failure → silent retry OR VAD-gated correction; time-to-correction p95 | 100 % (zero silent drops) / ≤ 1 turn-boundary | oops E2E + audit log |
| M5 | Smart/Dumb routing: false-spawn rate on local-action allowlist; misroute rate on OPTIMISTIC-20 | 0 % / < 5 % | routing tests + benchmark |

## 5. STRATEGY

**Pattern:** Router/Worker + Optimistic Execution layered on **event-sourced background
missions** (CQRS-flavoured: Talker = command + immediate ACK; Worker = execution;
EventBus = append-only log; mission store = read model).

**Tech-stack recommendations (extend, do not replace):**
- **Event-Bus:** keep `jarvis/core/bus.py` (frozen dataclasses, `trace_id`,
  `timestamp_ns`, `_safe_dispatch` swallow). The talker↔worker queue is the mission event
  store. No external broker (cloud-first doctrine).
- **Router logic:** keep the `_should_force_openclaw` heuristic (smalltalk allowlist >
  action verb > external-system marker). Add (a) latency instrumentation, (b) an *optional*
  embedding-similarity tiebreaker that runs only on ambiguous cases, behind a config flag,
  default off, hard < 150 ms budget, heuristic fallback.
- **Background worker:** Mission-Manager + Worker-Critic + `claude-cli` Sonnet (OAuth) +
  git worktree + Job Object. Already live.
- **Streaming:** AsyncIterator providers + WebSocket for desktop; add an **SSE** endpoint
  for the browser/VPS path (cloud-first).

**Claude Code dev workflow:**
- One git worktree per wave; `/agents` + `dispatching-parallel-agents` to fan out the four
  waves where independent.
- Subagent types: `test-runner` (after every change), `code-reviewer` (after each wave),
  `plan-verifier` (acceptance criteria), `Explore` (fan-out lookups).
- `test-driven-development` skill per micro-task; `writing-plans` / `planung-des-plans`
  conventions (this repo already ships HARD-NEGATIVES / ANTI-PATTERNS / JARVIS-20 /
  EXECUTION-PLAYBOOK / PROMPTS per plan).

## 6. MILESTONES (4 waves, 15–30 min micro-tasks)

### Wave 1 — Optimistic Execution as the guaranteed default + latency instrumentation
- **1.1** Add `trace_id`-stamped latency spans (wake-end, ACK-start, ACK-end, intent-end,
  spawn-dispatched) as frozen bus events (`jarvis/core/events.py` + `pipeline.py`).
- **1.2** `tests/voice_latency/` assertion: p95 ACK-start < 1.2 s against recorded traces.
- **1.3** Guarantee ACK fires **before** spawn dispatch returns; audit
  `_handle_utterance` return paths (BUG-007/BUG-020 territory).
- **1.4** Verify the Ack-Brain suppress-if-fast gate (2000 ms) composes with the
  optimistic path (`jarvis/brain/ack_brain/`).
- **1.5** `GET /api/telemetry/latency` → p50/p95 per span.

### Wave 2 — Harden Smart/Dumb routing
- **2.1** Extend `local_action_gate` allowlist + regression cases ("mach die Adjusties",
  "spiel X ab") (`jarvis/brain/local_action_gate.py` + `tests/unit/brain/test_local_action_gate.py`).
- **2.2** False-spawn guard: every allowlist entry must NOT trigger
  `_should_force_openclaw` (`tests/unit/brain/test_routing.py`).
- **2.3** Instrument router decision latency; CI fails if p95 > 150 ms.
- **2.4** Optional embedding tiebreaker behind a config flag (default off, < 150 ms,
  heuristic fallback).
- **2.5** Re-assert `ROUTER_TOOLS` discipline: AP-5/AP-14 guard test (no spawn-tool leaks).

### Wave 3 — Close the Oops-protocol loop
- **3.1** Define `WorkerCorrectionNeeded` frozen bus event (`trace_id`, missing-info
  reason, mission ref). Apply the five-layer enum pattern
  (`docs/anti-drift-three-layer.md`) for the reason vocabulary.
- **3.2** Talker subscribes and injects the correction need into its context window
  (not yet spoken).
- **3.3** VAD-gated emission: speak the organic correction only at the next turn-boundary
  (Silero VAD), never mid-utterance; route through `scrub_for_voice` (`pipeline.py:647`).
- **3.4** Failure taxonomy: recoverable (ask) / retryable (silent retry) / fatal (audit +
  apology). Zero silent drops (anti-BUG-020 invariant).
- **3.5** E2E: the spec's missing-email scenario → assert a scrubbed organic correction
  at the turn-boundary with original task context preserved.

### Wave 4 — Benchmark, SLO gates, hard-negatives, CI
- **4.1** Author `OPTIMISTIC-20.md` (20 scenarios: smart, dumb, oops, smalltalk, ambiguous)
  in JARVIS-20 style.
- **4.2** Wire M1–M5 thresholds as CI gates.
- **4.3** `HARD-NEGATIVES.md`: ACK "done" without an oops path; dumb tool wakes the worker;
  correction interrupts mid-utterance; secret in correction utterance.
- **4.4** `ANTI-PATTERNS.md` (AP-OE1..) + `EXECUTION-PLAYBOOK.md`.
- **4.5** `PROMPTS.md`: one paste-able prompt per wave for fresh Claude Code sessions.

---

## 7. CLAUDE.md SETUP block (compact, copy-paste — see chat for placement decision)

```markdown
## Optimistic Execution & the "Oops" Protocol (binding)

The core UX contract is one uninterrupted spoken conversation. The Talker
(router-brain + ack-brain) acknowledges optimistically and never blocks on an
MCP round-trip; the Heavy-Duty Worker (Mission-Manager + claude-cli Sonnet)
executes in the background off the chat transcript.

### Architecture Decisions
- **AD-OE1** The optimistic ACK ("Geht klar") is emitted BEFORE the worker
  dispatch returns. Never after. Audit every `_handle_utterance` return path.
- **AD-OE2** The Talker never `await`s an MCP/network call on the voice path.
  The talker↔worker queue is the in-process EventBus + mission event store —
  no external broker (cloud-first €5-VPS doctrine).
- **AD-OE3** Dumb tools (local scripts) resolve in-process via
  `local_action_gate`; they MUST NOT wake the worker (false-spawn rate = 0).
- **AD-OE4** Smart tools: the WORKER issues the MCP call, never the Talker.
- **AD-OE5** Oops loop: worker failure → frozen `WorkerCorrectionNeeded` event →
  inject into Talker context → speak ONLY at the next Silero-VAD turn-boundary →
  through `scrub_for_voice`. Never interrupt mid-utterance.
- **AD-OE6** Zero silent drops: every worker/MCP failure yields a silent retry
  OR a spoken correction OR an audited apology (anti-BUG-020).

### Coding Standards
- Latency budgets are SLO-gated: p95 wake→ACK < 1.2 s, intent→ACK < 3.0 s,
  router decision < 150 ms. Regressions fail CI.
- Every spoken path (utterance + announcement) goes through `scrub_for_voice`
  (regex only, no LLM call).
- New wire-format vocab (correction reasons, mission status) uses the five-layer
  enum pattern (`docs/anti-drift-three-layer.md`) + parity test.
- `ROUTER_TOOLS` stays a frozenset; no spawn-tool ever enters a worker set
  (AP-5/AP-14). Every subprocess uses `NO_WINDOW_CREATIONFLAGS`. Config writes
  go through `config_writer` (lock + tempfile + BOM-safe).
```
