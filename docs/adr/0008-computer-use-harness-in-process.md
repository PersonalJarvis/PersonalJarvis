---
title: "ADR-0008: Computer-Use in-process"
slug: adr-0008-computer-use-harness-in-process
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0008 — Computer-Use harness runs in-process (exception to the subprocess pattern)

**Status:** Accepted  (2026-04-22)
**Phase:** 5 — Computer-Use capability

## Context

The existing five harnesses (`jarvis_agent`, `codex`, `open-interpreter`, `python-script`, `mcp-remote`) run as a subprocess with their own Python interpreter/shell and communicate via stdout/stdin NDJSON. `CLAUDE.md` §Security states:

> **Harness isolation:** Sub-harnesses run in their own subprocess without access to Jarvis secrets (own ENV allowlist).

The new Computer-Use harness deliberately breaks this pattern — see the decision below. This ADR documents the exception.

## Decision

**`jarvis/plugins/harness/computer_use.py` runs in-process** (in the Jarvis main process), while the other five harnesses remain subprocesses.

### Rationale
By its nature, Computer-Use is **not an external Jarvis-Agent framework** (as Jarvis-Agents or Codex are), but an internal plan-observe-act-verify loop that:

1. Uses the existing `BrainManager` directly (Opus plans, Sonnet observes, Haiku steps — cost tracking via ADR-0006 applies automatically)
2. Consumes the `VisionEngine` directly (no screenshot serialization over pipes)
3. Passes the `CancelToken` from ADR-0004 through transparently
4. Emits event-bus events (`ObservationCaptured`, `ActionProposed`, `ActionVerified`) directly — no IPC translation

Architecturally this is closer to the `BrainManager` or `HarnessManager` (both in-process) than to the external Jarvis-Agent worker harness (which wraps an external binary).

### Position relative to the CLAUDE.md rule
The rule targets **third-party agent code** (Jarvis-Agents, Codex, Open Interpreter), which could potentially read/write arbitrarily. The Computer-Use harness is not third-party code — it is our own orchestrator code, whose tools run through the existing risk-tier/whitelist system. The secret-isolation clause is therefore not violated, because no foreign binary is started.

### Security mitigation
- All individual actions (click, type, screenshot) run through the `ToolExecutor` including the blacklist check.
- Destructive actions (from the whitelist: see mandate §6.2 — `uninstall`, `remove_service`, `remove_firewall_rule`, …) trigger a user prompt independent of the risk tier.
- Admin operations **never** run directly from the CU harness, but through the admin-helper pipe (ADR-0001), which in turn enforces the fixed vocabulary.
- On cost overrun or kill-switch: `CancelToken.cancel()` propagates through the loop; no zombie processes are conceivable, because there are no subprocesses.

## Consequences

+ No IPC layer for brain calls or vision observations — ~200 LOC saved.
+ Cancel is trivial: an `if cancel_token.is_cancelled(): break` before every action step.
+ Cost tracking gets the loop for free.
+ Event-bus events can be published synchronously on both buses (relevant because of the CLAUDE.md two-bus bug, addressed in ADR-0004).
- A bug in the CU loop (infinite loop, exception in the wrong thread) can affect Jarvis as a whole. **Mitigation:** strict per-step timeouts (default 30s), max 20 steps per plan, `asyncio.wait_for` around every sub-operation.
- `pyautogui.click` is synchronously blocking. **Mitigation:** `loop.run_in_executor(None, pyautogui.click, x, y)` — the action step runs in the thread pool, the event loop stays responsive.
- Pattern break. Future harnesses with the same justification (internal orchestrator loop, not external binary) may invoke this ADR. Others remain subprocesses.

## Alternatives Considered

- **Subprocess with HTTP callback to the parent** (which I had proposed in the plan discussion): pattern-consistent, but ~200 LOC of overhead (new FastAPI routes `/api/brain/complete`, `/api/vision/observe`, an IPC client in the child, serializing screenshots as Base64). With a simultaneous <2s kill-switch guarantee the parent must be able to kill the child process anyway — the added complexity is out of all proportion to the security gain, because the child would have just as much `Desktop` access as the parent (both `asInvoker`, no privilege boundary).
- **Dedicated thread pool (`ThreadPoolExecutor`):** Half-baked — the thread runs in the same GIL, but cancel semantics are treacherous. Rejected.
- **Subprocess without HTTP callback, own brain stack in the child:** Duplicate API calls, duplicate token burn, no shared cost tracker. Rejected.

## Amendment 2026-07-02 — engine rebuilt as `jarvis/cu` (decision unchanged)

The loop implementation was rebuilt from scratch as the modular package
`jarvis/cu/` (geometry / capture / conventions / actuate / verify / ledger /
engine) and became the default via `[computer_use].engine = "v2"`. The
architectural decision of THIS ADR — the Computer-Use harness runs
in-process — is unchanged and applies to the new engine identically: it
consumes `BrainManager`, `ToolExecutor`, `CancelToken` and the event bus
directly, and every action still dispatches through the ToolExecutor risk
tiers. The legacy engines remain selectable (`"current"`, `"stable"`,
`"june13"`) as one-line rollbacks. Root causes fixed by the rebuild and the
measurement rig are documented in `docs/computer-use.md`.

## Open

- Timeout configuration in `jarvis.toml:[computer_use]`:
  ```toml
  [computer_use]
  enabled = false
  max_steps = 20
  per_step_timeout_s = 30
  observe_before_first_action = true
  screenshot_every_step = true
  ```
- Should the CU loop ever need to load foreign code (e.g. browser-use as a sub-library), it will get a new ADR addressing the isolation **before** that point.
