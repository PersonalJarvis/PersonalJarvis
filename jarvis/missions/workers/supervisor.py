"""WorkerSupervisor — Done/Stuck/Waiting detection across 5 signals.

From the risk register (research doc §I rank #3) + ADR-0009 §2:

Signal hierarchy (in order of clarity):

1. **Process exit** — `proc.returncode is not None`. The clearest signal.
   `returncode == 0` -> DONE_OK, otherwise DONE_ERR.
2. **`result` event received** (Claude) or `turn.completed/turn.failed/error`
   (Codex) — a logical end signal even while the process is still winding down.
3. **`api_retry` event** — a legitimate pause. We extend the idle deadline
   by `retry_delay_ms`, otherwise the idle heuristic wrongly classifies it
   as 'stuck' while Anthropic is playing out its backoff.
4. **Idle timeout** — no event for N seconds, no process exit, no
   api_retry. Default: 90 s for the Sonnet tier, 300 s for extended-thinking
   (the caller sets this via a constructor argument).
5. **Hard wall-clock cap** — total_runtime > MAX. Default 900 s. Fail-safe
   against workers that never emit `result`.

The supervisor is *passive* — it only classifies the state, it does not kill.
A killer hook (e.g. a `WorkerKilled` event emitter) belongs in a different
layer (the mission-manager tier).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkerState(str, Enum):
    """Discrete states a worker can be in."""

    RUNNING = "RUNNING"
    WAITING = "WAITING"  # api_retry — legitimate silence
    STUCK = "STUCK"  # idle past the threshold with no discernible reason
    DONE_OK = "DONE_OK"
    DONE_ERR = "DONE_ERR"
    TIMED_OUT = "TIMED_OUT"  # hard wall-clock cap reached


@dataclass
class WorkerSupervisor:
    """Lifecycle classifier for a single worker.

    Stateful — call the methods in chronological order:

        sup = WorkerSupervisor(idle_timeout_s=90, hard_cap_s=900)
        sup.start()
        async for event in worker.spawn(...):
            state = sup.observe_event(event)
            if state in (WorkerState.DONE_OK, WorkerState.DONE_ERR):
                break
        # after EOF:
        final = sup.observe_exit(returncode=proc.returncode)

    Threading: not thread-safe. One instance per worker; calls from the
    same coroutine context.
    """

    idle_timeout_s: float = 90.0
    hard_cap_s: float = 900.0
    monotonic: Any = field(default_factory=lambda: time.monotonic)

    _started_at: float | None = None
    _last_event_at: float | None = None
    _waiting_until: float | None = None  # during api_retry: not stuck until this point

    # --- Lifecycle ---

    def start(self) -> None:
        now = self.monotonic()
        self._started_at = now
        self._last_event_at = now
        self._waiting_until = None

    def observe_event(self, event: Any) -> WorkerState:
        """Classifies the worker state after a received event.

        Args:
            event: Pydantic event from `parse_*_stream_json`.

        Returns:
            The current WorkerState based on the event type. Terminal
            events return DONE_OK/DONE_ERR. Otherwise RUNNING (or WAITING
            if an api_retry is still in progress).
        """
        if self._started_at is None:
            raise RuntimeError("Call WorkerSupervisor.start() before observe_event()")

        now = self.monotonic()
        self._last_event_at = now

        etype = getattr(event, "type", None)
        subtype = getattr(event, "subtype", None)

        # Claude `result` — terminal.
        if etype == "result":
            is_error = bool(getattr(event, "is_error", False))
            return WorkerState.DONE_ERR if is_error else WorkerState.DONE_OK

        # Codex terminal events.
        if etype == "turn.completed":
            return WorkerState.DONE_OK
        if etype in ("turn.failed", "error"):
            return WorkerState.DONE_ERR

        # api_retry extends the idle deadline by retry_delay_ms.
        if etype == "system" and subtype == "api_retry":
            delay_ms = getattr(event, "retry_delay_ms", None) or 0
            self._waiting_until = now + (delay_ms / 1000.0)
            return WorkerState.WAITING

        # Otherwise: active.
        return WorkerState.RUNNING

    def observe_exit(self, returncode: int | None) -> WorkerState:
        """Classifies the final state after the subprocess exits.

        Called *after* the stream EOF. `returncode is None` while
        wait() is still running -> defensively DONE_ERR (the caller has
        waited but there is no code, so not successful).
        """
        if returncode is None:
            return WorkerState.DONE_ERR
        return WorkerState.DONE_OK if returncode == 0 else WorkerState.DONE_ERR

    def check_idle(self) -> WorkerState:
        """Checks the idle timeout + hard cap.

        Typically called by the caller in a parallel watchdog loop
        (`asyncio.sleep(5); state = sup.check_idle(); ...`).

        Order: hard cap first (overrides WAITING), then idle.
        """
        if self._started_at is None or self._last_event_at is None:
            return WorkerState.RUNNING

        now = self.monotonic()

        # Hard cap always first — also applies in the WAITING state.
        if now - self._started_at >= self.hard_cap_s:
            return WorkerState.TIMED_OUT

        # WAITING tolerance: not stuck until _waiting_until.
        if self._waiting_until is not None and now < self._waiting_until:
            return WorkerState.WAITING

        # Idle heuristic.
        if now - self._last_event_at >= self.idle_timeout_s:
            return WorkerState.STUCK

        return WorkerState.RUNNING


__all__ = ["WorkerState", "WorkerSupervisor"]
