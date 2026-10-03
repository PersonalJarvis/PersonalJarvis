"""The scheduler — trusted Python that turns envelopes into activity.

Nothing else in the society may start work. The scheduler consumes the
board and applies, in order, for every ``ASSIGN``:

1. the master kill switch;
2. the tier wall — only ``lead`` and ``orchestrator`` may assign;
3. the delegation depth — an ASSIGN whose parent chain already holds two
   ASSIGNs is refused (depth ≤ 2, no recursion);
4. the target — must resolve to exactly one active agent;
5. the budgets — the global ``BudgetTracker`` pre-spawn check and the
   target's own ``daily_budget_usd`` against today's spend;
6. the target's concurrency cap;
7. the per-trace message cap.

A refusal is itself an event: a ``VETO`` from ``scheduler`` on the same
trace, carrying the typed reason — so the chat card, the ledger and the
voice path all learn why, from the board.

``SAY`` / ``QUERY`` / ``ANSWER`` / ``PROPOSE`` addressed to an agent are
delivered by waking the target's canonical chat (the ``deliver`` hook; the
chat binding provides it in M2). ``RESULT`` is validated against the
handoff record (agent-definition §4.3) and releases the sender's run slot.

The two hooks are injected so the scheduler is testable with fakes and so
the mission machinery is imported only when it is actually used.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final
from uuid import uuid4

from .delivery import DeliveryBusy
from .events import SCHEDULER_ACTOR as _SCHEDULER
from .events import USER_ACTOR as _USER
from .events import MsgType, RoomState, SocietyEnvelope, Tier, now_ms
from .failure_reasons import FailureReason, classify_error, retry_action
from .rooms import Room, RoomError, Rooms
from .roster import AgentRecord, AgentState, Roster
from .store import SocietyStore, day_start_ms

log = logging.getLogger(__name__)

__all__ = [
    "CurateHook",
    "DispatchHook",
    "DeliverHook",
    "RoomSettledHook",
    "RoomTurnHook",
    "SocietyScheduler",
    "validate_result",
]

#: ``dispatch(target, assign_envelope) -> run_id`` — starts real work under
#: the target's identity and returns the mission/run id it started.
DispatchHook = Callable[[AgentRecord, SocietyEnvelope], Awaitable[str]]
#: ``deliver(target, envelope)`` — wakes the target's canonical chat.
DeliverHook = Callable[[AgentRecord, SocietyEnvelope], Awaitable[None]]
RoomTurnHook = Callable[[AgentRecord, Room, str], Awaitable[str]]
RoomSettledHook = Callable[[SocietyEnvelope], Awaitable[None]]
# Curating a RESULT stages knowledge behind the existing human review gate.
# This callback never dispatches work and is not a second orchestrator.
CurateHook = Callable[[SocietyEnvelope], Awaitable[None]]

MAX_DEPTH: Final[int] = 2
DEFAULT_TRACE_MESSAGE_CAP: Final[int] = 24
_DELIVERED: Final[frozenset[MsgType]] = frozenset(
    {MsgType.SAY, MsgType.QUERY, MsgType.ANSWER, MsgType.PROPOSE, MsgType.HOLD, MsgType.RELEASE}
)
_RESULT_REQUIRED: Final[tuple[str, ...]] = ("done",)


def validate_result(payload: dict[str, Any]) -> str | None:
    """``None`` when the handoff record is complete, else what is missing."""
    for key in _RESULT_REQUIRED:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return f"RESULT.{key} must be a non-empty string"
    output = payload.get("output")
    open_items = payload.get("open")
    if not output and not open_items:
        return "RESULT needs output (where the work is) or open (what remains)"
    status = payload.get("status", "done")
    if status not in ("done", "partial", "blocked"):
        return "RESULT.status must be done | partial | blocked"
    return None


class SocietyScheduler:
    def __init__(
        self,
        store: SocietyStore,
        roster: Roster,
        *,
        dispatch: DispatchHook | None = None,
        deliver: DeliverHook | None = None,
        rooms: Rooms | None = None,
        room_turn: RoomTurnHook | None = None,
        room_settled: RoomSettledHook | None = None,
        curate: CurateHook | None = None,
        budget_tracker: Any | None = None,
        budget_tracker_getter: Callable[[], Any | None] | None = None,
        trace_message_cap: int = DEFAULT_TRACE_MESSAGE_CAP,
    ) -> None:
        self._store = store
        self._roster = roster
        self._dispatch = dispatch
        self._deliver = deliver
        self._rooms = rooms
        self._room_turn = room_turn
        self._room_settled = room_settled
        self._curate = curate
        self._budget = budget_tracker
        self._budget_getter = budget_tracker_getter
        self._trace_cap = trace_message_cap
        self._delivery_lock = asyncio.Lock()
        #: run_id → agent_id of work the scheduler started and has not seen end.
        self._running: dict[str, str] = {}
        self._unsubscribe: Callable[[], None] | None = None

    # ------------------------------------------------------------ wiring

    def attach(self) -> SocietyScheduler:
        if self._unsubscribe is None:
            self._unsubscribe = self._store.bus.subscribe_all(self.on_envelope)
        return self

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def running(self) -> dict[str, str]:
        return dict(self._running)

    def active_runs(self, agent_id: str) -> int:
        return sum(1 for a in self._running.values() if a == agent_id)

    def note_run_started(self, run_id: str, agent_id: str) -> None:
        self._running[run_id] = agent_id

    def note_run_ended(self, run_id: str) -> str | None:
        return self._running.pop(run_id, None)

    async def halt_all(self) -> int:
        """Kill switch engaged: forget every run slot; returns how many."""
        count = len(self._running)
        self._running.clear()
        return count

    async def drive_rooms(self) -> None:
        """Resume every running room. Safe to call repeatedly from recovery."""
        if self._rooms is None or self._room_turn is None:
            return
        for room in await self._rooms.list(state=RoomState.RUNNING):
            await self.drive_room(room.room_id)

    async def drive_room(self, room_id: str) -> None:
        """Advance one bounded room by at most one canonical-chat turn."""
        rooms = self._rooms
        dispatch = self._room_turn
        if rooms is None or dispatch is None:
            return
        room = await rooms.get(room_id)
        if room is None or room.state is not RoomState.RUNNING or not room.live:
            return
        if await self._store.kill_switch():
            await rooms.settle(room_id, reason="kill_switch")
            return

        prefix = f"room:{room_id}:"
        if room.inflight_claim_id and any(run_id.startswith(prefix) for run_id in self._running):
            return

        target_id = room.inflight_member or room.next_speaker
        if not target_id:
            await rooms.fail(room_id, reason=str(FailureReason.INTERNAL_ERROR))
            return
        target = await self._roster.resolve(target_id)
        if target is None or target.state is AgentState.ARCHIVED:
            await rooms.fail(room_id, reason=str(FailureReason.TARGET_UNKNOWN))
            return
        if target.state is AgentState.PAUSED:
            await rooms.fail(room_id, reason=str(FailureReason.TARGET_PAUSED))
            return

        if not room.inflight_claim_id:
            if await self._store.count_in_trace(room.trace_id) > self._trace_cap:
                await rooms.fail(room_id, reason=str(FailureReason.MESSAGE_CAP))
                return
            budget = self._budget_getter() if self._budget_getter is not None else self._budget
            if budget is not None:
                try:
                    budget.assert_under_limit(room.trace_id)
                except Exception as exc:  # noqa: BLE001 — tracker owns its exception type
                    log.info("society room %s budget gate refused dispatch: %s", room_id, exc)
                    await rooms.fail(room_id, reason=str(FailureReason.BUDGET_EXHAUSTED))
                    return
            if target.daily_budget_usd > 0:
                spent = await self._store.cost_since(target.agent_id, day_start_ms(now_ms()))
                if spent >= target.daily_budget_usd:
                    await rooms.fail(room_id, reason=str(FailureReason.BUDGET_EXHAUSTED))
                    return
            if self.active_runs(target.agent_id) >= target.max_concurrent_runs:
                return

        had_claim = bool(room.inflight_claim_id)
        claim_id = room.inflight_claim_id or f"room-turn:{uuid4().hex}"
        if not had_claim:
            try:
                room = await rooms.claim_turn(room_id, claim_id)
            except RoomError as exc:
                log.debug("society room %s claim lost a concurrent race: %s", room_id, exc)
                return
        try:
            run_id = await dispatch(target, room, claim_id)
        except DeliveryBusy:
            if not had_claim:
                try:
                    await rooms.release_claim(room_id, claim_id)
                except RoomError:
                    log.debug("society room %s claim moved while releasing busy turn", room_id)
            return
        except Exception as exc:  # noqa: BLE001 — failed room turns are terminal and typed
            log.warning("society room %s dispatch failed", room_id, exc_info=True)
            await rooms.fail(room_id, reason=str(classify_error(exc)))
            return
        if run_id:
            self._running[run_id] = target.agent_id

    # ------------------------------------------------------------ handler

    async def on_envelope(self, env: SocietyEnvelope) -> None:
        if env.from_agent == _SCHEDULER and env.msg_type is not MsgType.ROOM_SETTLE:
            return
        if env.msg_type is MsgType.ASSIGN:
            await self._on_assign(env)
        elif (
            env.msg_type is MsgType.ROOM_OPEN
            and isinstance(env.payload.get("room_id"), str)
        ):
            await self.drive_room(str(env.payload["room_id"]))
        elif env.msg_type is MsgType.ROOM_SETTLE:
            if self._room_settled is not None:
                try:
                    await self._room_settled(env)
                except Exception:  # noqa: BLE001 — notification cannot invalidate terminal room state
                    log.warning(
                        "society scheduler: room notification failed for %s",
                        env.trace_id,
                        exc_info=True,
                    )
        elif env.msg_type is MsgType.RESULT:
            await self._on_result(env)
        elif env.msg_type in _DELIVERED and env.to_agent and env.to_agent != _USER:
            # Envelopes for the person (HOLD/RELEASE on approvals) are read by
            # the UI, the bar and voice — never delivered to a roster row.
            await self.drain_deliveries()

    async def _veto(self, env: SocietyEnvelope, reason: FailureReason, detail: str) -> None:
        log.info(
            "society scheduler: %s on %s from %s: %s", reason, env.msg_type, env.from_agent, detail
        )
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.VETO,
                from_agent=_SCHEDULER,
                to_agent=env.from_agent,
                trace_id=env.trace_id,
                parent_event_id=env.event_id,
                payload={
                    "reason": str(reason),
                    "retry": str(retry_action(reason)),
                    "text": detail,
                    "vetoed_event_id": env.event_id,
                },
            )
        )

    async def _depth(self, env: SocietyEnvelope) -> int:
        """How many ASSIGNs sit on the parent chain, this one included."""
        depth = 1
        parent_id = env.parent_event_id
        seen: set[str] = set()
        events = {e.event_id: e for e in await self._store.events_for_trace(env.trace_id)}
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = events.get(parent_id)
            if parent is None:
                break
            if parent.msg_type is MsgType.ASSIGN:
                depth += 1
            parent_id = parent.parent_event_id
        return depth

    async def _resolve_target(self, env: SocietyEnvelope) -> AgentRecord | FailureReason:
        if not env.to_agent:
            return FailureReason.TARGET_UNKNOWN
        target = await self._roster.resolve(env.to_agent)
        if target is None:
            return FailureReason.TARGET_UNKNOWN
        if target.state is AgentState.ARCHIVED:
            return FailureReason.TARGET_UNKNOWN
        if target.state is AgentState.PAUSED:
            return FailureReason.TARGET_PAUSED
        return target

    async def _on_assign(self, env: SocietyEnvelope) -> None:
        if await self._store.kill_switch():
            await self._veto(env, FailureReason.KILL_SWITCH, "the society is halted")
            return
        sender = await self._roster.resolve(env.from_agent)
        if env.from_agent != "user" and (sender is None or not sender.may_assign):
            await self._veto(
                env, FailureReason.TIER_NOT_ALLOWED, "only the lead and orchestrators may assign"
            )
            return
        if sender is not None and sender.tier is Tier.SPECIALIST:
            await self._veto(env, FailureReason.TIER_NOT_ALLOWED, "a specialist cannot assign")
            return
        depth = await self._depth(env)
        if depth > MAX_DEPTH:
            await self._veto(
                env, FailureReason.DEPTH_EXCEEDED, f"delegation depth {depth} > {MAX_DEPTH}"
            )
            return
        target = await self._resolve_target(env)
        if isinstance(target, FailureReason):
            await self._veto(env, target, f"target {env.to_agent!r} cannot take work")
            return
        if target.agent_id == env.from_agent:
            await self._veto(
                env, FailureReason.BLOCKED_BY_POLICY, "an agent cannot assign to itself"
            )
            return
        if await self._store.count_in_trace(env.trace_id) > self._trace_cap:
            await self._veto(
                env, FailureReason.MESSAGE_CAP, f"trace exceeded {self._trace_cap} messages"
            )
            return
        budget = self._budget_getter() if self._budget_getter is not None else self._budget
        if budget is not None:
            try:
                budget.assert_under_limit(env.trace_id)
            except Exception as exc:  # noqa: BLE001 — BudgetExceeded is the tracker's own type
                await self._veto(env, FailureReason.BUDGET_EXHAUSTED, str(exc))
                return
        if target.daily_budget_usd > 0:
            spent = await self._store.cost_since(target.agent_id, day_start_ms(env.ts_ms))
            if spent >= target.daily_budget_usd:
                await self._veto(
                    env,
                    FailureReason.BUDGET_EXHAUSTED,
                    f"{target.name} spent ${spent:.2f} of ${target.daily_budget_usd:.2f} today",
                )
                return
        if self.active_runs(target.agent_id) >= target.max_concurrent_runs:
            await self._veto(
                env,
                FailureReason.CONCURRENCY_CAP,
                f"{target.name} already runs {target.max_concurrent_runs} task(s)",
            )
            return
        if self._dispatch is None:
            await self._veto(env, FailureReason.INTERNAL_ERROR, "no dispatcher is wired")
            return
        try:
            run_id = await self._dispatch(target, env)
        except Exception as exc:  # noqa: BLE001 — a failed spawn is a typed veto, never a crash
            await self._veto(env, classify_error(exc), f"dispatch failed: {exc}")
            return
        self._running[run_id] = target.agent_id
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.CLAIM,
                from_agent=target.agent_id,
                to_agent=env.from_agent,
                trace_id=env.trace_id,
                parent_event_id=env.event_id,
                payload={"run_id": run_id, "text": f"{target.name} took the task"},
            )
        )

    async def _on_result(self, env: SocietyEnvelope) -> None:
        problem = validate_result(env.payload)
        if problem is not None:
            await self._veto(env, FailureReason.INVALID_RESULT, problem)
            return
        run_id = env.payload.get("run_id")
        if isinstance(run_id, str):
            self._running.pop(run_id, None)
        else:
            # No run id: release one slot of the sender, oldest first.
            for rid, agent in list(self._running.items()):
                if agent == env.from_agent:
                    self._running.pop(rid, None)
                    break
        if self._curate is not None:
            try:
                await self._curate(env)
            except Exception:  # noqa: BLE001 — curation cannot invalidate a durable RESULT
                log.warning(
                    "society scheduler: curator failed for RESULT %s",
                    env.event_id,
                    exc_info=True,
                )
        next_owner = env.payload.get("next_owner")
        if isinstance(next_owner, str) and next_owner and self._deliver is not None:
            target = await self._resolve_target(env.model_copy(update={"to_agent": next_owner}))
            if isinstance(target, AgentRecord):
                await self._deliver(target, env)

    async def drain_deliveries(self) -> None:
        """FIFO per recipient. Busy recipients never block other conversations."""
        if self._delivery_lock.locked():
            return
        async with self._delivery_lock:
            busy: set[str | None] = set()
            for env in await self._store.pending_deliveries():
                if await self._record_assignment_reply(env):
                    continue
                if env.to_agent in busy:
                    receive = getattr(self._deliver, "receive", None)
                    target = await self._resolve_target(env)
                    if receive is not None and isinstance(target, AgentRecord):
                        try:
                            await receive(target, env)
                        except DeliveryBusy:
                            pass  # No chat service yet; the durable queue will retry.
                        except Exception:
                            log.warning("society: queued receipt projection failed", exc_info=True)
                    continue
                if not await self._on_deliver(env):
                    busy.add(env.to_agent)

    async def _record_assignment_reply(self, env: SocietyEnvelope) -> bool:
        """Assignment outcomes belong to the watcher, not a second chat turn."""
        if env.msg_type is not MsgType.ANSWER or not env.parent_event_id:
            return False
        parent = await self._store.get_event(env.parent_event_id)
        if (
            parent is None
            or parent.msg_type is not MsgType.ASSIGN
            or "reply_policy" not in parent.payload
            or parent.trace_id != env.trace_id
            or parent.from_agent != env.to_agent
            or parent.to_agent != env.from_agent
        ):
            return False
        await self._store.mark_delivery(env.event_id, "delivered")
        return True

    async def _on_deliver(self, env: SocietyEnvelope) -> bool:
        if await self._store.delivery_status(env.event_id) != "queued":
            return True
        reason = None
        if await self._store.kill_switch():
            reason = FailureReason.KILL_SWITCH
        elif await self._store.count_in_trace(env.trace_id) > self._trace_cap:
            reason = FailureReason.MESSAGE_CAP
        target = await self._resolve_target(env)
        if isinstance(target, FailureReason):
            reason = reason or target
        if reason is not None:
            await self._store.mark_delivery(env.event_id, "failed", str(reason))
            await self._veto(env, reason, f"message could not reach {env.to_agent}")
            return True
        if self._deliver is None:
            return False
        assert isinstance(target, AgentRecord)
        try:
            await self._deliver(target, env)
        except DeliveryBusy:  # A busy destination keeps the delivery pending for retry.
            return False
        except Exception as exc:  # noqa: BLE001 — persist the failure and report it
            await self._store.mark_delivery(env.event_id, "failed", str(exc))
            await self._veto(env, classify_error(exc), f"delivery failed: {exc}")
            return True
        await self._store.mark_delivery(env.event_id, "delivered")
        return True
