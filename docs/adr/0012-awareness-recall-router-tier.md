# ADR-0012 — `awareness-recall` lives in the router tier, not a worker tier

**Status:** Accepted · **Date:** 2026-05-11 · **Phase:** Awareness A3

## Context

`JARVIS_AWARENESS_PLAN.md` §7 introduces a Phase A3 tool, `awareness-recall`,
that performs a BM25 full-text search over the recent episode log so the
brain can answer questions like „was war der Befehl von vorhin?" without
fabricating an answer. The plan was written before Welle 4 of the
OpenClaw-Bridge migration and assumes the existence of a "Sub-Jarvis"
worker tier. In that world the plan unambiguously assigns the tool to
`SUB_TOOLS` (Plan §7, Hard-Negative #1) — the rationale at the time was
that recall is a slightly heavier operation (~50–300 ms SQLite hit) that
the latency-sensitive router should not perform.

Welle 4 deleted the Sub-Jarvis tier in its entirety
(`jarvis/brain/factory.py:90-103`). Heavy work is now executed by
external OpenClaw subprocesses spawned via `spawn_openclaw`. Those
subprocesses run their own OpenClaw instances and do **not** inherit
the Jarvis in-process tool registry — they can only call the tools that
OpenClaw itself exposes (`Bash`, `Read`, `Grep`, MCP servers, etc.).
A Python tool registered as a `jarvis.tool` entry point is invisible to
them.

This leaves two real homes for `awareness-recall`:

1. **Router tier.** The router brain calls the tool directly in
   process. Latency is fine — a single FTS5 `MATCH` on the local SQLite
   file is well under the 300 ms budget set by the plan, and the result
   stays inside the router's context window so it can decide whether to
   answer directly or spawn an OpenClaw worker.
2. **Inside an MCP server that OpenClaw connects to.** Workable but
   premature: it requires standing up an MCP transport, exposing
   internal Jarvis state across a process boundary, and forcing the
   worker to discover and call the tool. Three additional moving parts
   for a Phase-A3 minimum-viable surface.

## Decision

`awareness-recall` is registered in `ROUTER_TOOLS`
(`jarvis/brain/factory.py:40-58`) and constructed via the standard
entry-point loader. The tool's constructor takes the existing
`RecallStore` instance the factory already builds for `MessageRecorder`
and `StoryTracker`, so no new dependency is introduced.

If the router brain decides to delegate work to an OpenClaw worker
after recalling, it may bake the recall result into the worker's
`context_hints` field on the `spawn_openclaw` call. That follow-up is
out of scope for the initial A3 wave but the placement chosen here does
not block it.

## Consequences

- **Plan §7 is partially superseded.** The hard-negative "❌ in
  `ROUTER_TOOLS`" no longer applies — Welle 4 made the worker tier it
  referenced disappear. This ADR is the canonical record of the
  override.
- **Router tier gains its first non-trivial IO tool.** Up to now the
  router-tier tools were either pure state reads (`awareness-snapshot`,
  `whoami`) or dispatchers (`spawn_openclaw`, `dispatch-to-harness`).
  `awareness-recall` performs one SQLite query. The latency budget set
  in the plan (p95 < 300 ms on 1000 episodes) is generous; the unit
  test enforces p95 < 300 ms on 100 episodes as a CI-friendly bound.
- **Tool surface stays stable across awareness on/off toggles.** The
  factory builds `awareness-recall` even when the `RecallStore` is
  unavailable; the tool's `execute` returns `success=False` with a
  clear error rather than disappearing from the schema mid-session.
  This prevents the router brain from re-learning the tool list every
  time awareness is toggled.
- **Recursion-protection is structural.** Because OpenClaw workers
  cannot reach `awareness-recall` at all (no MCP bridge exposes it),
  there is no risk of a recursive spawn chain — analogous to how
  `spawn_openclaw` itself is intentionally absent from any worker tool
  set.

## Alternatives Considered

- **Skip A3 entirely and let the brain hallucinate.** Rejected — the
  whole point of the awareness layer is to ground answers about
  earlier work in actual log data.
- **Expose recall via an MCP server.** Future-compatible but adds a
  process boundary, MCP transport plumbing, and worker-side tool
  discovery for what is otherwise a 200-line addition. Revisit if and
  when OpenClaw workers need recall directly, e.g. for long-running
  analytic tasks. The router-tier placement does not preclude this
  upgrade.
- **Add an in-process worker tier.** Would directly resurrect what
  Welle 4 just deleted. Not entertained.

## References

- `JARVIS_AWARENESS_PLAN.md` §7 (Phase A3 original specification).
- `docs/openclaw-bridge.md` §11 (Code-Migrations-Tabelle, Welle 4).
- `jarvis/brain/factory.py:40-58` (ROUTER_TOOLS frozenset).
- `jarvis/plugins/tool/awareness_recall.py` (tool implementation).
- `jarvis/memory/recall.py:357` (`search_episodes` with `since_ns`).
- `tests/unit/awareness/test_awareness_recall_tool.py` (tier placement assertions).
