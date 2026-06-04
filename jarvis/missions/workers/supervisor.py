"""WorkerSupervisor — Done/Stuck/Waiting-Detection ueber 5 Signale.

Aus Risk-Register (Research-Doc §I Rank #3) + ADR-0009 §2:

Signal-Hierarchie (in Reihenfolge der Klarheit):

1. **Process exit** — `proc.returncode is not None`. Eindeutigstes Signal.
   `returncode == 0` -> DONE_OK, sonst DONE_ERR.
2. **`result`-Event empfangen** (Claude) bzw. `turn.completed/turn.failed/error`
   (Codex) — logisches End-Signal selbst wenn der Prozess noch ausschwingt.
3. **`api_retry`-Event** — legitime Pause. Wir verlaengern die Idle-Deadline
   um `retry_delay_ms`, sonst klassifiziert die Idle-Heuristik faelschlich
   als 'stuck' waehrend Anthropic backoffs ausspielt.
4. **Idle-Timeout** — kein Event seit N Sekunden, kein process exit, kein
   api_retry. Default: 90 s fuer Sonnet-Tier, 300 s fuer extended-thinking
   (caller setzt das via Konstruktor-Argument).
5. **Hard wall-clock cap** — total_runtime > MAX. Default 900 s. Fail-safe
   gegen Worker die `result` nie emittieren.

Der Supervisor ist *passiv* — er klassifiziert nur den State, er killt nicht.
Ein Killer-Hook (z.B. `WorkerKilled`-Event-Emitter) gehoert in eine andere
Schicht (Mission-Manager-Stufe).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkerState(str, Enum):
    """Discrete States, in denen ein Worker stehen kann."""

    RUNNING = "RUNNING"
    WAITING = "WAITING"  # api_retry — legitimes Schweigen
    STUCK = "STUCK"  # idle ueber Schwelle ohne erkennbaren Grund
    DONE_OK = "DONE_OK"
    DONE_ERR = "DONE_ERR"
    TIMED_OUT = "TIMED_OUT"  # Hard wall-clock cap erreicht


@dataclass
class WorkerSupervisor:
    """Lifecycle-Klassifizierer fuer einen einzelnen Worker.

    Stateful — ruft die Methoden chronologisch auf:

        sup = WorkerSupervisor(idle_timeout_s=90, hard_cap_s=900)
        sup.start()
        async for event in worker.spawn(...):
            state = sup.observe_event(event)
            if state in (WorkerState.DONE_OK, WorkerState.DONE_ERR):
                break
        # nach EOF:
        final = sup.observe_exit(returncode=proc.returncode)

    Threading: nicht thread-safe. Pro Worker eine Instanz; Aufrufe aus dem
    selben Coroutine-Kontext.
    """

    idle_timeout_s: float = 90.0
    hard_cap_s: float = 900.0
    monotonic: Any = field(default_factory=lambda: time.monotonic)

    _started_at: float | None = None
    _last_event_at: float | None = None
    _waiting_until: float | None = None  # bei api_retry: kein Stuck bis hierher

    # --- Lifecycle ---

    def start(self) -> None:
        now = self.monotonic()
        self._started_at = now
        self._last_event_at = now
        self._waiting_until = None

    def observe_event(self, event: Any) -> WorkerState:
        """Klassifiziert den Worker-State nach einem empfangenen Event.

        Args:
            event: Pydantic-Event aus `parse_*_stream_json`.

        Returns:
            Aktueller WorkerState basierend auf dem Event-Typ. Terminale
            Events liefern DONE_OK/DONE_ERR. Sonst RUNNING (oder vorher
            WAITING wenn ein api_retry noch laeuft).
        """
        if self._started_at is None:
            raise RuntimeError("WorkerSupervisor.start() vor observe_event() aufrufen")

        now = self.monotonic()
        self._last_event_at = now

        etype = getattr(event, "type", None)
        subtype = getattr(event, "subtype", None)

        # Claude `result` — terminal.
        if etype == "result":
            is_error = bool(getattr(event, "is_error", False))
            return WorkerState.DONE_ERR if is_error else WorkerState.DONE_OK

        # Codex terminale Events.
        if etype == "turn.completed":
            return WorkerState.DONE_OK
        if etype in ("turn.failed", "error"):
            return WorkerState.DONE_ERR

        # api_retry verlaengert die Idle-Deadline um retry_delay_ms.
        if etype == "system" and subtype == "api_retry":
            delay_ms = getattr(event, "retry_delay_ms", None) or 0
            self._waiting_until = now + (delay_ms / 1000.0)
            return WorkerState.WAITING

        # Sonst: aktiv.
        return WorkerState.RUNNING

    def observe_exit(self, returncode: int | None) -> WorkerState:
        """Klassifiziert den finalen State nach Subprocess-Exit.

        Wird *nach* dem Stream-EOF gerufen. `returncode is None` waehrend
        wait() noch laeuft -> defensiv DONE_ERR (Caller hat gewartet aber
        es gibt keinen Code, also nicht-erfolgreich).
        """
        if returncode is None:
            return WorkerState.DONE_ERR
        return WorkerState.DONE_OK if returncode == 0 else WorkerState.DONE_ERR

    def check_idle(self) -> WorkerState:
        """Prueft Idle-Timeout + Hard-Cap.

        Wird typischerweise vom Caller in einer parallelen Watchdog-Schleife
        gerufen (`asyncio.sleep(5); state = sup.check_idle(); ...`).

        Reihenfolge: Hard-Cap zuerst (uebersteuert WAITING), dann Idle.
        """
        if self._started_at is None or self._last_event_at is None:
            return WorkerState.RUNNING

        now = self.monotonic()

        # Hard-Cap immer zuerst — gilt auch im WAITING-Zustand.
        if now - self._started_at >= self.hard_cap_s:
            return WorkerState.TIMED_OUT

        # WAITING-Toleranz: bis _waiting_until kein Stuck.
        if self._waiting_until is not None and now < self._waiting_until:
            return WorkerState.WAITING

        # Idle-Heuristik.
        if now - self._last_event_at >= self.idle_timeout_s:
            return WorkerState.STUCK

        return WorkerState.RUNNING


__all__ = ["WorkerState", "WorkerSupervisor"]
