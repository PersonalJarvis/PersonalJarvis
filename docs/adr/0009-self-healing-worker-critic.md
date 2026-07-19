---
title: "ADR-0009: Self-Healing Worker-Critic"
slug: adr-0009-self-healing-worker-critic
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-07-19
phase: 6
audience: developer
---

# ADR-0009 — Self-Healing Worker-Critic-Loop with Action/Observation Invariant

**Status:** Amended 2026-07-19
**Phase:** 6 — Self-Healing Multi-Agent Orchestrator

## Context

Phases 0-5 deliver Voice→Brain→Tool/Harness, but every tool call is a single shot: if it fails, the user hears an error message and has to try again. The master-plan goal "Jarvis-Agent as Kontrollierer with critic loop" (see `Jarvis-Behavior/persona-delegation-mandate.md`) cannot be fulfilled with the current `SubJarvisManager` (Phase 5) — it spawns a sub-brain, but without verification, without retry, without worktree isolation, and writes directly into the user's working tree.

The private research note later moved to `docs/research/self-healing-architecture.md` distills six converging pattern lines (Reflexion, Self-Refine, CRITIC, Constitutional AI, Aider Architect/Editor, OpenHands Action/Observation) into a single viable architecture for a single-user Windows orchestrator. Phase 6 implements this architecture.

The core question is not "which algorithm" — but "which invariant carries everything else". Six of the top-10 failure modes (hallucinated execution, file races, lost mission state, WS-disconnect data loss, cascading failures, triangle deadlock) are neutralized by a single design decision: **Action/Observation strict separation as a typed event stream with WAL**.

## Decision

**Phase 6 builds the Self-Healing Worker-Critic-Loop on four non-negotiable invariants** — everything else composes around them.

### 1. Action/Observation invariant (OpenHands pattern)

Every worker step is a pair `(Action, Observation)`:

- **Action** = typed Pydantic object emitted by the LLM (`ToolCall`, `Edit`, `Bash`, etc.).
- **Observation** = typed Pydantic response produced by the runtime (`CommandOutput`, `FileEdit`, `Error`).

The LLM may **never formulate an Observation itself** — only the runtime module that executed the Action signs the Observation. The voice-readback path (the main Jarvis telling the user the result) reads back **only Observations**, never LLM narrative ("I did X…"). Violating this rule = BLOCKER in code review.

> **Amendment 2026-06-28 — natural surface form of a signed Observation.**
> The invariant binds the *content* of an Observation, not its exact spoken
> wording. A bounded flash-LLM (`jarvis/voice/contextual_readback.py`) MAY render
> the already-signed `summary_de`/`summary_en` into a more natural spoken sentence
> on the readback path, under two hard guards: (a) it is given ONLY the signed
> line as ground truth and instructed to rephrase, never invent; (b) an
> `honesty_bound` check rejects any output whose content words do not sufficiently
> overlap the signed line (no new noun/number/claim), with the signed line as the
> instant fallback on any miss. The Observation is still authored and signed by
> the runtime/Kontrollierer; the LLM only chooses words for an existing,
> verified fact. It may NOT author a failure/timeout Observation's *facts*, and
> it never sees `correction_instruction`. This satisfies the maintainer's
> "no fixed stock phrases" mandate without weakening the no-hallucinated-execution
> guarantee. Wiring: `MissionAnnouncer`/`MissionVoiceListener` (production +
> fallback readback paths). Regression guards in
> `tests/missions/test_voice_announcer.py` (rephrase-faithful + fallback-to-signed)
> and `tests/unit/voice/test_contextual_readback.py`.

### 2. Worker-Critic-Loop with MAX_CRITIC_LOOPS=3

```
Worker.execute() -> diff
  -> Critic.review(goal, diff, log_tail, prior_reflections) -> verdict
       verdict.approve  -> MissionApproved
       verdict.revise   -> Worker.resume(critique) [iteration += 1]
       verdict.reject   -> MissionFailed("critic_rejected")
  -> on iteration == 2: upgrade Critic model Sonnet -> Opus
  -> on iteration == 3 + revise: MissionFailed("critic_loop_exhausted")
```

`MAX_CRITIC_LOOPS = 3` is **hardcoded**, not a config parameter. Reflexion (NeurIPS 2023) defaults to 5; we reduce it to 3 because Aider/Cline production data show that 3 iterations are enough and cost is 2x lower. **Parameterizing it would invite drift** (someone sets 10, a mission costs $50). Whoever wants to change it changes the constant and writes a new ADR.

The Critic must cite `evidence_ref` with `file:line`, `log_line:N` or `test:name` — bare prose verdicts are rejected orchestrator-side as an abstention and retried once with adversarial framing.

### 3. Worktree-per-task + Windows Job Object

Every worker runs with `cwd = sub-agents-outputs/<mission-slug>/<task-id>/workspace/` — a branch freshly created via `git worktree add -b agent/<task-id>`. The user's working tree is **never** written to directly.

Every worker subprocess is assigned to a per-mission Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. If the orchestrator crashes, the OS atomically reaps the entire descendant tree — no zombies, no orphans. Pattern: `claude-squad/session/git/worktree.go` + `microsoft/win32-jobobject`.

### 4. Typed event stream + SQLite WAL

Every event is a `frozen=True` Pydantic model with `event_id (UUIDv7)`, `seq (server-assigned monotonic)`, `mission_id`, `parent_event_id`, `source_actor`, `ts_ms`, `payload`. **Write-ahead requirement:** the event is `INSERT`ed into SQLite (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`) **before** it is published on the bus. Crash recovery at startup: scan for non-terminal missions, mark as `FAILED("crash_recovery")`, reap orphan PIDs from the events log.

The WebSocket layer (Mission Control UI) replays via the `Last-Event-ID` pattern from SQLite on reconnect. This avoids Redis/NATS/Temporal — a single-user system does not need the daemon overhead.

### 5. Positioning relative to existing Phase-5 components

The Phase-5 `SubJarvisManager` (`jarvis/brain/sub_jarvis.py`) stays **untouched**. Phase-6 code lives in parallel under `jarvis/missions/` (see file layout in the research document §"Recommended file/module structure"). The main Jarvis dispatches depending on the risk tier:

- **safe / monitor / single-shot** -> existing `SubJarvisManager` (Phase 5, tool loop without critic)
- **ask / multi-step / repair / refactor / cross-file** -> new `MissionManager` (Phase 6, Worker-Critic-Loop)

Heuristic: a mission is Phase-6-eligible if the router classifier returns `code` **or** the user utterance contains an `external_system_marker` OR more than two `spawn_verbs` (cf. ADR-0011 §2). Exact wiring comes in the Phase-6 plan.

## Consequences

+ **Six failure modes mitigated in one stroke** via event stream + WAL: hallucinated execution, file races, lost mission state, WS-disconnect data loss, cascading failures, triangle deadlock (see research document Section I).
+ **The voice path stays untouched.** The main Jarvis (Haiku tier) still emits a single `MissionDispatched` event in Phase 5 — Phase 6 is just a new subscriber.
+ **Cost discipline via $5 per-mission + $50 daily budget** (`jarvis.toml:[budget]`). Hard abort + voice warning at 50%/80%.
+ **Crash safety without a daemon.** SQLite WAL + Job Objects replace Temporal/Redis. If Jarvis crashes: recovery on the next start. If the user pulls the power: ditto.
+ **Critic verdicts are grounded.** Empty-evidence verdicts are rejected. Sycophancy risk (SycEval: 58% default rate) is countered through adversarial framing + anchor token (original goal in every critic call).
- **Two tool stacks in parallel** (`SubJarvisManager` + `MissionManager`) until Phase 6 is in production. No big-bang refactor, but additive. Risk: cognitive overhead at routing — when Phase 5 / when Phase 6?
- **Worker subprocesses are out-of-process**, not in-process. A pattern break from ADR-0008 (Computer-Use). **Rationale:** Computer-Use is Plan-Observe-Act in a single-brain loop; Phase-6 workers are third-party binaries (`openclaw agent`, `codex exec`) with their own permission surface — subprocess isolation is correct here, not stubborn.
- **Worktree-per-task costs disk** (Cursor community measurement: 9.8 GB over a 2 GB codebase with many worktrees). Mitigation: `cleanup_period_days = 14`, optional Dev Drive (ReFS block cloning).

## Alternatives Considered

- **In-process worker (like ADR-0008 Computer-Use):** fails in three places — (1) `openclaw agent` and `codex exec` are external binaries, not Python code; (2) parallel file edits without a worktree would have race conditions; (3) a worker crash would take Jarvis down as a whole. **Rejected.**
- **MAX_CRITIC_LOOPS as config:** abused forever as a tuning knob ("just bump it to 5 briefly for this one problem"). Cost discipline requires hardcoding. **Rejected.**
- **Single-critic pass (no loop):** the Self-Refine paper shows that even one iteration yields ~20%; the Reflexion paper shows 3 iterations are the cost/value optimum. A single-pass critic is throwaway money. **Rejected.**
- **Docker-per-worker (OpenHands pattern):** 30-60s cold start destroys the voice UX. Single-tenant, no security gain over Job Object + worktree. **Rejected.**
- **Redis Streams / NATS / Temporal as the event backbone:** an additional daemon lifecycle that Alex would have to maintain. SQLite WAL delivers 95% of the value at 0% ops cost. Escape hatch documented: if the main Jarvis and the Kontrollierer ever move into separate processes, swap `EventBus.publish` -> `redis.xadd`. **Rejected for now.**
- **Cross-model critic as default** (Worker = Claude, Critic = Codex/GPT): the multi-agent-debate literature supports it, but it doubles auth flows + cost trackers. **Optional via config flag, not default.**
- **Phase 6 overrides Phase 5:** unnecessary risk concentration. Phase 5 works for the smalltalk-tier Jarvis-Agent (see ADR-0011); Phase 6 is additive for multi-step missions. **Rejected.**

## References

- Research document: private source note, later moved to `docs/research/self-healing-architecture.md`.
- Phase-6 plan: `docs/phase6-plan.md` (TBD).
- Prompt chain: `docs/phase6-prompt-chain.md` (skeleton created).
- Persona mandate: `Jarvis-Behavior/persona-delegation-mandate.md` §"Phase 4 — Multi-Step-Missions".
- Master plan: `<USER_HOME>\.claude\plans\also-er-muss-auch-lexical-pond.md` §"Phase 6 — Self-Healing".  <!-- i18n-allow -->
- Existing code (NOT to be changed): `jarvis/brain/sub_jarvis.py:SubJarvisManager`.
- Planned new modules: `jarvis/missions/{manager,state_machine,kontrollierer,workers,critic,isolation}/*`.
- Subagents for the Phase-6 implementation: `.claude/agents/jarvis-{architect-explorer,test-runner,critic-design-reviewer}.md`.

## Open

- **Reflexion memory layout:** do we persist the last 3 critic reflections as `reflections.md` in the worktree (Reflexion-paper pattern) or as JSON in SQLite (query-baker)? Decision when writing `jarvis/missions/critic/runner.py`.
- **Cross-model-critic trigger:** should the switch to Codex/GPT as the critic happen automatically when Worker = Claude and iteration == 3, or only via config? Gather observations during Phase 6, amend if needed.
- **Worktree-cleanup policy on MissionFailed:** immediate `git worktree remove --force` or keep for N days for forensics? Proposal: keep + automatic prune after 7 days.
- **Voice readback at iteration 2:** should the user hear "I'm trying again" after the critic verdict `revise`, or loop silently until approval? Default silent, opt-in via `[voice].announce_critic_loop = true`.

## Amendment 2026-07-19 — goal-based review and truthful partial outcomes

### Context

Local mission forensics found a repeated false-negative pattern in otherwise
substantial HTML and Markdown deliverables. The direct Codex critic returned a
structured decision, but its flat-schema reconstruction bypassed the tolerant
validator used by every full verdict. A `summary` or `summary_de` longer than
the 280-character voice-readback cap therefore invalidated the entire decision.
Some fallback critics subsequently returned approval-shaped JSON, but the lost
primary decision and an adversarial retry could still end the mission as
`critic_loop_exhausted`.

The prompt amplified that parser defect. It required an adversarial *code*
critic to find at least three bugs even when the original goal was a report or
standalone HTML document. Optional polish, unavailable browser automation, and
new requirements invented during review were consequently treated like
blocking failures. Correction iterations also reused an accumulating worker
log, so stale errors from an earlier attempt could be judged again after the
worker had fixed them. Finally, the Outputs read model collapsed every terminal
failure with an archived deliverable into a generic error without exposing the
terminal reason or the existence of reviewable partial work.

Artifact existence alone is not approval. The same forensic set contained an
old placeholder artifact and deliverables with real factual or safety defects.
The signed `MissionApproved` event remains the only success authority.

### Decision

- The critic judges the original mission goal and uses a blocking-defect
  threshold. `revise` means that cited evidence shows the requested outcome is
  missing, unusable, unsafe, materially incorrect, or violates an explicit
  requirement. Low- and medium-severity polish may be reported with an
  approval; it must not be promoted into a blocking defect to satisfy a quota.
  The critic remains skeptical, evidence-grounded, read-only, and subject to
  the action/observation invariant.
- Every provider path applies the same presentation-field tolerance. Overlong
  voice summaries are truncated to their declared cap only after all other
  schema fields validate. Missing axes, invalid enums, empty approval evidence,
  empty revision evidence, and malformed decisions still fail closed. A direct
  provider's prompt and enforced output schema must describe the same shape.
- Each worker correction attempt owns a fresh runtime log. Evidence from prior
  attempts remains available through bounded reflections and per-iteration
  artifacts, not by concatenating stale process streams into the current
  observation.
- A terminal read model distinguishes signed success, cancellation, genuine
  failure without a usable artifact, and a non-approved mission that still has
  reviewable output. Only exhausted, unavailable, or time-limited review can
  enter the last category; an explicit rejection, execution failure, or safety
  failure with a leftover file remains failed. A reviewable partial is never a
  green success, and its terminal reason remains visible.
- Draft deliverables are copied into a complete sibling tree before the latest
  `files/` snapshot is promoted. A copy or promotion error preserves and rolls
  back to the prior durable tree; no platform-specific rename primitive is
  required.
- `MAX_CRITIC_LOOPS = 3` remains fixed. This amendment changes decision quality
  and presentation, not the retry budget.

### Consequences

- A valid critic decision can no longer be discarded solely because its spoken
  summary is verbose.
- Reports and static documents are evaluated against their requested purpose,
  while evidence-backed factual, safety, security, and missing-deliverable
  defects continue to block approval.
- A correction is judged on the current attempt instead of stale worker errors.
- Users can recover a useful but unsigned artifact without being told that it
  is either fully successful or wholly absent.
- Existing historical events are not rewritten. Their audit trail remains
  intact; the read model may describe retained artifacts more precisely.

### Alternatives considered

- Treat any created file as success: rejected because a file can be empty,
  placeholder-only, unsafe, factually wrong, or unrelated to the request.
- Add more critic retries: rejected because it preserves the biased decision
  rule, increases cost, and violates the fixed-loop decision.
- Remove adversarial review: rejected because grounded review still catches
  real defects and protects the signed success contract.
- Rewrite historical mission events after reclassification: rejected because
  it would destroy the append-only forensic record.
