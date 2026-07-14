---
title: "ADR-0025: Mission-Scoped Worker Tool Broker"
slug: adr-0025-mission-scoped-worker-tool-broker
diataxis: adr
status: active
owner: project-maintainers
last_reviewed: 2026-07-13
phase: 6
audience: developer
---

# ADR-0025 — Mission-Scoped Worker Tool Broker

**Status:** Accepted (2026-07-13)
**Phase:** 6 — Self-Healing Jarvis-Agents Orchestrator
**Reference:** ADR-0009, ADR-0011, AP-3, AP-5, AP-14, AP-21, AP-22

## Context

Mission workers run in isolated processes and worktrees. That isolation kept
the supervisor's connected Marketplace, MCP, and app-command tools out of the
worker capability surface. A tool could therefore be healthy and callable in
the main Jarvis while a delegated mission honestly reported that it was not
available. Directly copying connector configurations into every worker was not
acceptable: it exposed credentials, worked only for selected CLI backends, and
bypassed the supervisor's `ToolExecutor` safety path.

The production worker chain is provider-agnostic. Claude CLI, Codex CLI,
Gemini CLI, Google CLI, and API workers need one capability contract that does
not depend on a provider-specific MCP implementation.

## Decision

Personal Jarvis uses a process-local, mission-scoped worker tool broker. Each
mission receives a short-lived authenticated grant containing only
task-relevant MCP tools, connected native connector tools, and the restricted
app commands explicitly allowed for workers. CLI workers reach the grant
through a stdio MCP companion connected to a loopback-only HTTP endpoint; API
workers call the same binding directly.

The supervisor retains the live tool objects and all credentials. Every broker
request executes through the supervisor's `ToolExecutor`; workers never call
`Tool.execute()` directly. An ask-tier request remains suspended on its original
trace until an explicit approval or denial arrives. The pending call is exposed
through mission REST and CLI actions without adding a global waiting mission
state; approval resumes that exact call, while denial and timeout execute
nothing.

The mission controller owns one broker grant per worker iteration and closes it
as a bounded quiescence barrier before invoking the critic. The grant retains a
credential-free terminal outcome for every supervisor call. Critic review is
allowed only when no call remains active and every recorded call succeeded;
denied, cancelled, timed-out, failed, or outcome-unknown calls force the
existing worker-error mission path. A worker model therefore cannot ignore a
tool error and claim successful completion.

The grant fails closed. Recursive mission tools, skill execution, credential
surfaces, secret-reading names, and config mutation are never exported. A
missing supervisor reference, empty relevant tool set, expired token, or
unsupported runtime produces no grant rather than a partially trusted one.
Catalog descriptors are resolved from the live supervisor on each list and
execute operation, and grant closure revokes the bearer and cancels active
calls.

## Consequences

- Connected capabilities remain usable after delegation without copying OAuth
  tokens, API keys, or MCP environment blocks into a worker workspace.
- All supported worker families consume the same broker contract; no provider
  name is treated as the capability boundary.
- Risk-tier evaluation, audit events, plausibility checks, and approvals remain
  centralized in `ToolExecutor`.
- A worker receives only the capabilities relevant to its mission, not the
  supervisor's complete tool catalog.
- Consequential actions wait for an explicit user decision through the mission
  API/CLI (or an already-authorized scheduled-task decision); silence never
  becomes implicit approval.
- A successful worker transcript is insufficient evidence when its broker
  completion certificate contains an unclean call; the critic is not invoked.
- The supervisor now owns a small authenticated loopback server for the
  lifetime of active grants. Tokens are revoked on completion and bounded by a
  TTL as a crash backstop.

## Alternatives considered

- **Pass original MCP configs to every worker:** rejected because configs can
  contain credentials and support differs across provider CLIs.
- **Let workers invoke connector SDKs directly:** rejected because it bypasses
  `ToolExecutor`, duplicates auth logic, and breaks the safety audit trail.
- **Expose every supervisor tool:** rejected because it reintroduces recursive
  spawn paths, secret access, off-topic actions, and excessive model schemas.
- **Keep connected tools router-only:** rejected because heavy missions that
  genuinely need a connected source would remain unable to fulfill the task.
- **Pin the feature to Claude CLI MCP support:** rejected under AP-21/AP-22;
  capability transport must work across provider families.

## Follow-up items

- Keep provider-parity tests for every supported worker backend.
- Keep an end-to-end stdio MCP test that proves list and execute behavior across
  the authenticated loopback boundary.
- Keep the credential-free mission test covering grant selection, approval
  resume, completion certification, and revocation.
- Preserve the explicit recursive-tool and secret/config deny guards.
