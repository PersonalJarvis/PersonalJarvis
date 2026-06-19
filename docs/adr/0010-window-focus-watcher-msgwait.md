# ADR-0010 — WindowFocusWatcher: MsgWaitForMultipleObjects instead of PumpMessages

**Status:** Accepted (2026-04-25)
**Phase:** A1 — L1 Live Frame
**Related:** ADR-0009 (Awareness-layer architecture)

## Context

Plan `JARVIS_AWARENESS_PLAN.md` §5 describes the `WindowFocusWatcher` and states verbatim:

> The hook runs in a dedicated Win32 message-loop thread (`pythoncom.PumpMessages`).

`pythoncom.PumpMessages()` is the simple, standard solution for Win32 message pumps in Python: it blocks in a C loop, dispatches all messages to the registered hooks, and exits only on `WM_QUIT`. Shutdown via `PostThreadMessage(thread_id, WM_QUIT, 0, 0)` from another thread.

Three problems with this pattern for our lifecycle (<2s shutdown guaranteed, idempotent, without hangs):

1. **Race condition in the startup window:** `PostThreadMessage` must fire *after* the thread is actually inside `PumpMessages`. If `SetWinEventHook` is still running and `WM_QUIT` is posted in between, it is lost — the pump then runs forever.
2. **No deterministic wakeup handle:** `thread.join(2.0)` is the only validation. If join waits 2s, you don't know why (did WM_QUIT take effect? did Win32 eat it?).
3. **`WM_QUIT` is non-specific:** a `WM_QUIT` from elsewhere (the Windows logoff sequence, another thread in the same process) would terminate our pump — we cannot distinguish between "stop from us" and "WM_QUIT external".

## Decision

`WindowFocusWatcher._pump_loop` uses `MsgWaitForMultipleObjects(stop_event, QS_ALLINPUT)` instead of `pythoncom.PumpMessages()`:

```python
while True:
    rc = win32event.MsgWaitForMultipleObjects(
        [stop_event_handle], False, INFINITE, QS_ALLINPUT,
    )
    if rc == WAIT_OBJECT_0:
        break    # stop signaled — we wake up immediately
    # rc == WAIT_OBJECT_0 + 1 → Win32 messages are pending
    while user32.PeekMessageW(byref(msg), 0, 0, 0, PM_REMOVE):
        user32.TranslateMessage(byref(msg))
        user32.DispatchMessageW(byref(msg))
```

Shutdown via `win32event.SetEvent(stop_event_handle)` from the asyncio thread. Wakeup is deterministic and immediate.

## Rationale

`MsgWaitForMultipleObjects` is the Win32 SDK solution for "a GUI thread with additional wait objects". It solves all three `PumpMessages` problems:

- **No race:** `SetEvent(stop_event)` is explicitly signaling. The pump wakes up immediately, regardless of whether it is currently in the wait or in message dispatch.
- **Deterministic wakeup:** `rc == WAIT_OBJECT_0` is unambiguously "our stop". If join still hangs, we know it is due to Win32 hook cleanup in the finally block, not the wait itself.
- **Source of the wake distinguishable:** `rc == WAIT_OBJECT_0` is stop, `rc == WAIT_OBJECT_0 + 1` is a Win32 message. External `WM_QUIT` would come via the message path and would not kill the loop.

## Plan conflict

Plan §5 names `pythoncom.PumpMessages` verbatim. CLAUDE.md says: "On conflict between plan and code, the plan wins; code deviations must be documented in the plan." This ADR is that documentation.

It is recommended that §5 be updated accordingly at the next plan review (or that the ADR reference be added to the plan). In practice: both patterns would work — `PumpMessages` with the three risks named above, `MsgWaitForMultipleObjects` without them.

## Consequences

+ Deterministic shutdown <2s without a `PostThreadMessage` race.
+ `stop_event_handle` is a dedicated Win32 resource that we manage explicitly — `CloseHandle` in the 6-phase stop sequence is clear (see `window.py:stop()`).
+ No dependency on `pythoncom.CoInitialize` for the pump (we make pure `user32` message calls + `win32event` for the wait object).
- More code than the `pythoncom.PumpMessages()` one-liner. Readability slightly reduced, but offsettable through comments in `_pump_loop`.
- Three additional Win32 constants (`QS_ALLINPUT`, `WAIT_OBJECT_0`, `INFINITE`, `PM_REMOVE`) as module constants, because we cannot pull them from `win32con` (lazy-import requirement).

## Alternatives Considered

- **`pythoncom.PumpMessages()` with `PostThreadMessage(WM_QUIT)` shutdown** (plan wording): race condition, no deterministic wake. Discarded — see above.
- **`pythoncom.PumpWaitingMessages()` in a `time.sleep(0.1)` loop**: the polling portion runs permanently, eating CPU + latency. Discarded.
- **`WaitForSingleObject(hStop, INFINITE)` without a message pump**: no hook callbacks would ever be dispatched (no pump tick). Discarded.
- **`asyncio.run` directly in the Win32 thread with `loop.add_reader`**: an asyncio loop and a Win32 pump on the same thread mixes two event-loop models — error-prone. Discarded.

## References

- `JARVIS_AWARENESS_PLAN.md` §5 (plan wording of the deviation)
- `jarvis/awareness/watchers/window.py:_pump_loop` (implementation)
- ADR-0009 (Awareness-layer architecture — parent)
- Win32 docs: [`MsgWaitForMultipleObjects`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-msgwaitformultipleobjects)
