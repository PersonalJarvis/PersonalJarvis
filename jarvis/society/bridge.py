"""Mission envelopes → society events: one world feed (MASTERPLAN §2.6).

Every mission the app runs — society-dispatched or not — appears on the
board, so the world, the ledger and the cost HUD read ONE stream. Work the
scheduler started is attributed to the agent that owns it; everything else
belongs to the lead (Jarvis), because that is who dispatched it.

Only the envelopes that mean something to the society are mirrored; worker
chatter (progress ticks, bus stats, critic verdicts) stays in the mission
log where the Missions view reads it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .events import MsgType, SocietyEnvelope
from .roster import LEAD_AGENT_ID
from .store import SocietyStore

log = logging.getLogger(__name__)

__all__ = ["MissionBridge"]

_TERMINAL_OK = frozenset({"MissionApproved"})
_TERMINAL_BAD = {
    "MissionFailed": "blocked",
    "MissionCancelled": "blocked",
    "MissionTimedOut": "blocked",
}


class MissionBridge:
    def __init__(
        self,
        store: SocietyStore,
        *,
        owner_of: Callable[[str], str | None],
        on_run_ended: Callable[[str], Any] | None = None,
    ) -> None:
        self._store = store
        self._owner_of = owner_of
        self._on_run_ended = on_run_ended
        self._unsubscribe: Callable[[], None] | None = None
        self._cost: dict[str, float] = {}

    def attach(self, mission_bus: Any) -> MissionBridge:
        if self._unsubscribe is None:
            self._unsubscribe = mission_bus.subscribe_all(self.on_mission_envelope)
        return self

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def on_mission_envelope(self, env: Any) -> None:
        payload = getattr(env, "payload", None)
        event_type = str(getattr(payload, "event_type", "") or "")
        mission_id = str(getattr(env, "mission_id", "") or "")
        if not event_type or not mission_id:
            return
        agent_id = self._owner_of(mission_id) or LEAD_AGENT_ID
        trace_id = f"mission:{mission_id}"
        try:
            await self._mirror(event_type, mission_id, agent_id, trace_id, env, payload)
        except Exception:  # noqa: BLE001 — a mirror failure must not reach the mission bus
            log.warning(
                "society bridge: could not mirror %s for %s", event_type, mission_id, exc_info=True
            )

    async def _mirror(
        self, event_type: str, mission_id: str, agent_id: str, trace_id: str, env: Any, payload: Any
    ) -> None:
        if event_type == "MissionDispatched":
            await self._append(
                MsgType.CLAIM,
                agent_id,
                trace_id,
                {"run_id": mission_id, "text": str(getattr(payload, "prompt", "") or "")[:500]},
            )
        elif event_type == "WorkerSpawned":
            await self._append(
                MsgType.DIGEST,
                agent_id,
                trace_id,
                {
                    "run_id": mission_id,
                    "kind": "worker_spawned",
                    "worker": str(getattr(payload, "cli", "") or ""),
                    "model": str(getattr(payload, "model", "") or ""),
                    "text": "worker started",
                },
            )
        elif event_type == "WorkerDraftReady":
            cost = float(getattr(payload, "cost_usd", 0.0) or 0.0)
            self._cost[mission_id] = self._cost.get(mission_id, 0.0) + cost
            await self._append(
                MsgType.DIGEST,
                agent_id,
                trace_id,
                {"run_id": mission_id, "kind": "draft_ready", "text": "draft ready"},
                cost_usd=cost,
            )
        elif event_type == "WorkerKilled":
            await self._append(
                MsgType.DIGEST,
                agent_id,
                trace_id,
                {
                    "run_id": mission_id,
                    "kind": "worker_killed",
                    "text": str(getattr(payload, "reason", "") or "worker killed"),
                },
            )
        elif event_type in _TERMINAL_OK or event_type in _TERMINAL_BAD:
            status = "done" if event_type in _TERMINAL_OK else _TERMINAL_BAD[event_type]
            summary = str(
                getattr(payload, "summary", None)
                or getattr(payload, "reason", None)
                or getattr(payload, "error", None)
                or event_type
            )
            self._cost.pop(mission_id, None)
            await self._append(
                MsgType.RESULT,
                agent_id,
                trace_id,
                {
                    "run_id": mission_id,
                    "status": status,
                    "done": summary[:2000],
                    "output": [f"mission:{mission_id}"],
                    "evidence": [],
                    "open": [] if status == "done" else [summary[:500]],
                    "next_owner": None,
                    "text": summary[:500],
                },
                to_agent=LEAD_AGENT_ID if agent_id != LEAD_AGENT_ID else None,
            )
            if self._on_run_ended is not None:
                self._on_run_ended(mission_id)

    async def _append(
        self,
        msg_type: MsgType,
        agent_id: str,
        trace_id: str,
        payload: dict[str, Any],
        *,
        cost_usd: float = 0.0,
        to_agent: str | None = None,
    ) -> None:
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=msg_type,
                from_agent=agent_id,
                to_agent=to_agent,
                trace_id=trace_id,
                cost_usd=cost_usd,
                payload=payload,
            )
        )
