---
title: "ADR-0004: Kill-Switch < 2s"
slug: adr-0004-kill-propagation
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0004 — Kill-Switch Propagation Under 2 Seconds

**Status:** Accepted  (2026-04-22)
**Phase:** 5 — Control

## Context

Mandate requirement: `Ctrl+Alt+Shift+K` aborts **all** running Computer-Use tasks in <2s. The mandate explicitly warns: "The kill switch must be faster than the current LLM call. Cancel propagation through the async hierarchy is a design problem, not an implementation problem."

An additional complication: per `CLAUDE.md`, the desktop app starts a **second event bus** (the Brain-Factory bus). A kill event published only to the UI bus does not reach the CU loop.

## Decision

**Three-layer cancel model: event + token + process kill.**

### Layer 1 — Event (`KillRequested`)
Published by hotkey / voice intent / tray menu / web-UI button. Wildcard subscriber on **both buses** (UI bus and Brain-Factory bus). The UI-bus subscriber explicitly forwards to the brain bus via `KillSwitch.forward_kill(event, to_bus=brain_bus)`. This addresses the two-bus bug from CLAUDE.md.

**API correction (2026-04-22):** An earlier version of this ADR referred to `brain_manager.forward_kill(event)` — this method does not exist. The production API is `KillSwitch.forward_kill(ev, to_bus=...)` in `jarvis/control/cancel.py`.

### Layer 2 — CancelToken (`asyncio.Event`-based)
```python
class CancelToken:
    def __init__(self): self._event = asyncio.Event()
    def cancel(self, reason: str): self._reason = reason; self._event.set()
    async def wait_until_cancelled(self): await self._event.wait()
    def is_cancelled(self) -> bool: return self._event.is_set()
    @property
    def reason(self) -> str | None: return getattr(self, "_reason", None)
```
Every long-running operation (brain stream, vision observe, task runner, CU loop) receives a token via `ExecutionContext.cancel_token`. Before every `async for chunk in …`, `if token.is_cancelled(): break` is checked.

### Layer 3 — Subprocess kill
On `cancel()`, all subprocess harnesses get: first `process.terminate()` (SIGTERM equivalent on Windows), 500ms grace, then `taskkill /T /F /PID <pid>` (kills the process tree, including child processes of openclaw/codex).

### Time budget (should be <2s)
- t+0ms: hotkey fires → KillRequested published
- t+~10ms: KillSwitch handler sets all active CancelTokens + sends `forward_kill` to the brain bus
- t+~50ms: the next `async for` iteration in streams sees `is_cancelled()`, breaks
- t+~100ms: subprocess harnesses receive `cancel()` → `terminate()`
- t+~600ms: if the subprocess does not respond → `taskkill /T /F`
- t+~700ms: task queue pauses (`state='cancelled'` for all running)
- t+~1500ms: pending elevation requests in the helper are answered with `{"status":"rejected_by_kill"}` and the pipe is closed

## Consequences

+ Kill takes effect deterministically, even in the middle of a streaming LLM call.
+ Both buses covered — no invisible CU loop.
+ Process-tree kill (`/T`) prevents zombie children.
- Brain calls that are mid-request may produce incomplete responses → log them as `aborted` state, not `failed`.
- The admin helper itself is **not** killed (it runs elevated) — the mandate says "reject pending elevation requests", not "terminate the helper". Correct behavior: pipe close + helper stays idle.

## Alternatives Considered

- **`CancelledError` propagation only:** Works for pure-async code, but subprocess harnesses swallow it. Rejected.
- **Global event only:** Async tasks do not check events automatically. They must be polled explicitly. Rejected as the sole layer — hence the three-layer design.
- **Strict supervisor pattern (anyio/TaskGroup):** Elegant, but would mean re-architecting the existing BrainDispatcher. YAGNI.

## Open

- What happens to tasks in `state='running'` when the kill hits them? → `state='cancelled'`, `last_error="kill_switch"`. No auto-retry (user intent = stop, not "retry later").
