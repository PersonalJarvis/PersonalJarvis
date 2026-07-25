---
title: "ADR-0031: Mission-Scoped Tool Pre-Authorization"
slug: adr-0031-mission-scoped-tool-preauthorization
diataxis: adr
status: active
owner: project-maintainers
last_reviewed: 2026-07-25
phase: 6
audience: developer
---

# ADR-0031 — Mission-Scoped Tool Pre-Authorization

**Status:** Accepted (2026-07-25)
**Phase:** 6 — Self-Healing Jarvis-Agents Orchestrator
**Reference:** ADR-0025, ADR-0009, AP-3, AP-5, AP-14; BUG-096 four-facts rule

## Context

Brokered worker tool calls (ADR-0025) run through `ToolExecutor` with
`voice_confirm=False`. An ask-tier call therefore armed an approval ticket,
published `ActionApprovalRequired`, and blocked until a human decided — or the
60-second default timeout denied it. Unattended missions have no human present:
every non-pre-approved ask-tier call was structurally guaranteed to stall for
a minute and come back denied. Before the observed-refusal split (W3,
`WorkerToolExecutionSummary.integrity_compromised`), that single denial also
failed the whole iteration.

Scheduled tasks solved the same problem earlier with
`jarvis.tasks.approval_bridge.TaskAutoApprover` ("Option B"): a bridge that
listens for `ActionApprovalRequired` and ANSWERS the gate by publishing
`ActionApproved` for pre-authorized tools on the task's own trace — never
bypassing the executor. `ToolExecutor` deliberately arms the approval ticket
BEFORE publishing the event so such a synchronous bus answer is never lost.

## Decision

A `MissionToolAutoApprover` (`jarvis/missions/tool_approvals.py`) answers the
approval gate for tools pre-authorized to one mission:

1. **Armed by the broker, exactly for the lifetime of a grant.**
   `WorkerToolBroker.issue()` arms `(mission_id, grant_key)` with the
   auto-approvable name set; every revocation path (explicit revoke, TTL
   reaper, test reset) disarms it. `grant_key` is a truncated SHA-256 of the
   bearer token — the secret itself never leaves the broker.
2. **The auto-approvable set is `granted ∩ allowlist`.** `granted` is the
   grant's spec names (already filtered by the Command-Registry
   `worker_allowed ∧ ¬dangerous` rule and the broker denylist). The allowlist
   is `[phase6.safety].auto_approve_tool_families`: exact tool names or an
   MCP server family written as `server/`. Config resolution is fail-closed —
   any error yields the empty set.
3. **Approval predicate (all must hold, fail-closed):** the event carries the
   armed `mission_id`; `risk_tier ∈ {ask, monitor}` (never `block`);
   `reason ∈ {risk_tier, plausibility}` — the plausibility guard is a
   voice-context heuristic with no signal for an unattended worker;
   `worker_tool_name_allowed(tool)` re-checked at approval time; tool name
   matches the armed set exactly or by `server/` prefix.
4. **The gate is answered, never bypassed.** The approver publishes
   `ActionApproved(approved_by="mission-grant:<id>")` onto the bus. The full
   `Proposed → ApprovalRequired → Approved → Executed` audit chain is
   preserved, and `MissionToolApprovalCoordinator` clears its pending entry
   through its normal `_on_resolved` subscription — the UI surface stays
   consistent.
5. **Feature flag + timeout knob.** `[phase6.safety].worker_tool_auto_approve
   = false` restores the previous behavior wholesale. The (previously
   hardcoded) approval wait is now `[safety].tool_approval_timeout_s`
   (default 60 s, passed to every `ToolExecutor` construction site). The
   default stays short on purpose: with the W3 refusal semantics, a
   non-pre-authorized call should fail fast and honestly rather than burn the
   worker's iteration budget blocked inside the MCP call.

`[phase6.safety]` is the first Pydantic-modeled slice of the `[phase6.*]`
tables (`Phase6SafetyConfig` / `Phase6Config`, both `extra="allow"` per
AP-16); the other tables remain raw TOML consumed via `bootstrap_missions`
defaults.

## Consequences

* Unattended missions can complete flows that need pre-authorized ask-tier
  tools without a human in the loop, per explicit operator configuration.
* The shipped default allowlist contains only read-only knowledge tools
  (`search_web`, the wiki quartet, `session-latest-turn`) — these are
  safe/monitor tier anyway, so out of the box the approver only prevents
  plausibility-escalated stalls. Real autonomy gains are an explicit operator
  opt-in per tool family.
* Messaging / mail / social-send families are deliberately NOT in the default
  and the config comment says so.
* Residual risk: an allowlisted `server/` family silently covers every future
  tool that server adds, including destructive ones. Mitigation: the default
  contains no family prefixes, the config comment recommends exact names, and
  the broker denylist plus the registry `¬dangerous` rule still bound the
  reachable surface.
* The spawn-gate mandate (agents spawn only on explicit user request) is
  untouched — this ADR governs what an already-authorized mission may do.

## Alternatives considered

* **Per-dispatch autonomy flag on `DispatchBody`** — rejected as the primary
  mechanism: missions arrive via voice, task bridge, CLI, and REST; the
  dominant voice path would have stayed unattended-broken. The config surface
  leaves room for a per-mission override later.
* **Blanket `ToolExecutor` bypass for `worker_broker=True` calls** — rejected:
  it would skip the gate instead of answering it, destroying the audit chain
  and violating the TaskAutoApprover precedent.
* **Raising the approval timeout for workers** — rejected: a longer stall
  helps nobody unattended; the W3 refusal path plus pre-authorization is
  strictly better.
