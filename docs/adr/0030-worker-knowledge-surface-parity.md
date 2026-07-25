---
title: "ADR-0030: Worker Knowledge-Surface Parity"
slug: adr-0030-worker-knowledge-surface-parity
diataxis: adr
status: active
owner: project-maintainers
last_reviewed: 2026-07-25
phase: 6
audience: developer
---

# ADR-0030 — Worker Knowledge-Surface Parity

**Status:** Accepted (2026-07-25)
**Phase:** 6 — Self-Healing Jarvis-Agents Orchestrator
**Reference:** ADR-0025 (broker mechanism), ADR-0012 (superseded placement
note), ADR-0031 (pre-authorization); AP-5, AP-14, AP-21

## Context

Maintainer directive 2026-07-25: mission workers should reach the knowledge
surface the voice brain has — "connected to all our wiki systems and the
complex knowledge base, with everything you normally have via voice." The
classic wiki quartet (`wiki-list`, `wiki-recall`, `wiki-page-read`,
`wiki-ingest`) was already brokered (ADR-0025). Everything else was
router-only: session/episode memory (`awareness-recall`), web search
(`search_web`), contact resolution (`contact-lookup`), and the new UltraWiki
semantic store had no tool surface at all.

The "router tier only, never in a worker set" placement notes on
`awareness-recall` (ADR-0012) and `contact-lookup` predate the broker: they
were written when workers could not reach in-process tools at all. The broker
removed that premise — the tool object, the store, and the credentials stay
in the supervisor; a worker only sends a named request that still runs
through `ToolExecutor`. ADR-0012 explicitly invites this reopening ("Reopen
if/when Jarvis-Agent workers need recall directly").

## Decision

`restricted_worker_knowledge_tools()`
(`jarvis/missions/workers/capabilities.py`) becomes dynamic and gate-driven;
the static wiki triple stays exported for read-compat. Per tool:

| Tool | Decision | Gate |
|---|---|---|
| `wiki-list` / `wiki-recall` / `wiki-page-read` | granted (unchanged) | none |
| `wiki-ingest` | granted via `worker_allowed` app command (unchanged) | live registry |
| `awareness-recall` | **granted** | `[awareness].enabled`, fail closed |
| `search_web` | **granted** | none (keyless, safe-tier) |
| `contact-lookup` | **granted** | none (degrades to a clean "contacts unavailable" error) |
| `ultrawiki-search` | **granted** | `cfg.ultrawiki.enabled` ∧ live `UltraWikiService` on the web-app state, fail closed (gate went live 2026-07-25 together with the router tool, ADR-0011 amendment "UltraWiki Search") |
| `awareness-snapshot` | **excluded** | live-desktop read: near-zero value in an isolated worktree, pure privacy leakage of current screen activity into external CLI transcripts |
| `contact-upsert` | **excluded (deferred)** | unattended write to the contact store; least privilege — not part of a knowledge surface |

Rules the decision preserves:

* **Phantom-tool honesty.** A gate failing closed means the name never enters
  the grant, so a worker never sees a tool whose every call can only error.
  The gateway-catalog intersection in `_BrokerScope._descriptors()` remains
  the structural backstop for tools that never loaded.
* **Execution stays in the supervisor** (ADR-0025): worker → broker →
  `BrainSupervisorToolGateway` → `ToolExecutor`. No credential, store handle,
  or `Tool` object crosses the process boundary.
* **No spawn/recursive/desktop/host-shell tool enters any worker set**
  (AP-5/AP-14; broker denylist unchanged).
* **The worker prompt names the surface** (`SUPERVISOR_BOUNDARY_DIRECTIVE`)
  and pins the honesty clause: a tool absent from the tool list is not
  available for this mission.

## Consequences

* Workers can answer "continue what I was doing this morning"-class tasks
  (session memory), research with the same keyless web search the router
  has, and resolve contact details for drafting tasks.
* Privacy: episode summaries and contact PII now flow into external worker
  CLI transcripts and mission logs. Accepted by explicit maintainer mandate
  ("everything you normally have via voice"); bounded by the read-only
  choice (`awareness-snapshot` and `contact-upsert` stay out).
* Token cost: up to four additional tool schemas per worker grant (~0.5–1k
  tokens) — bounded because knowledge grants stay flat.
* ADR-0012's "not in any worker tier" note and `contact_lookup.py`'s
  placement rule are superseded FOR BROKERED ACCESS ONLY; direct in-worker
  loading remains forbidden.

## Alternatives considered

* **Grant `awareness-snapshot` too** — rejected: value decays within seconds
  of spawn; a worktree worker acting on the live-desktop state would blur the
  isolation story ADR-0025 depends on.
* **Let workers call the wiki via the `jarvis` control CLI** — rejected:
  ADR-0025 already names the CLI adapters as a forbidden surface; the broker
  is the single sanctioned path.
* **Relevance-gate the knowledge tools like MCP servers** — rejected for
  now: the wiki tools ship ungated today and the new additions are cheap;
  revisit if grant schemas measurably bloat worker context.
