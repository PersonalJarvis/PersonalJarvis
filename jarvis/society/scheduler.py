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

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

from .events import SCHEDULER_ACTOR as _SCHEDULER
from .events import USER_ACTOR as _USER
from .events import MsgType, SocietyEnvelope, Tier
from .failure_reasons import FailureReason, classify_error, retry_action
from .roster import AgentRecord, AgentState, Roster
from .store import SocietyStore, day_start_ms

log = logging.getLogger(__name__)

__all__ = ["DispatchHook", "DeliverHook", "SocietyScheduler", "validate_result"]

#: ``dispatch(target, assign_envelope) -> run_id`` — starts real work under
#: the target's identity and returns the mission/run id it started.
DispatchHook = Callable[[AgentRecord, SocietyEnvelope], Awaitable[str]]
#: ``deliver(target, envelope)`` — wakes the target's canonical chat.
DeliverHook = Callable[[AgentRecord, SocietyEnvelope], Awaitable[None]]

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
        budget_tracker: Any | None = None,
        trace_message_cap: int = DEFAULT_TRACE_MESSAGE_CAP,
    ) -> None:
        self._store = store
        self._roster = roster
        self._dispatch = dispatch
        self._deliver = deliver
        self._budget = budget_tracker
        self._trace_cap = trace_message_cap
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

    # ------------------------------------------------------------ handler

    async def on_envelope(self, env: SocietyEnvelope) -> None:
        if env.from_agent == _SCHEDULER:
            return
        if env.msg_type is MsgType.ASSIGN:
            await self._on_assign(env)
        elif env.msg_type is MsgType.RESULT:
            await self._on_result(env)
        elif env.msg_type in _DELIVERED and env.to_agent and env.to_agent != _USER:
            # Envelopes for the person (HOLD/RELEASE on approvals) are read by
            # the UI, the bar and voice — never delivered to a roster row.
            await self._on_deliver(env)

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
        if self._budget is not None:
            try:
                self._budget.assert_under_limit(env.trace_id)
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
        next_owner = env.payload.get("next_owner")
        if isinstance(next_owner, str) and next_owner and self._deliver is not None:
            target = await self._resolve_target(env.model_copy(update={"to_agent": next_owner}))
            if isinstance(target, AgentRecord):
                await self._deliver(target, env)

    async def _on_deliver(self, env: SocietyEnvelope) -> None:
        if await self._store.kill_switch():
            await self._veto(env, FailureReason.KILL_SWITCH, "the society is halted")
            return
        if await self._store.count_in_trace(env.trace_id) > self._trace_cap:
            await self._veto(
                env, FailureReason.MESSAGE_CAP, f"trace exceeded {self._trace_cap} messages"
            )
            return
        target = await self._resolve_target(env)
        if isinstance(target, FailureReason):
            await self._veto(env, target, f"target {env.to_agent!r} cannot receive")
            return
        if self._deliver is None:
            log.debug("society scheduler: no deliverer wired; %s stays in the inbox", env.msg_type)
            return
        try:
            await self._deliver(target, env)
        except Exception as exc:  # noqa: BLE001 — delivery failure is a typed veto
            await self._veto(env, classify_error(exc), f"delivery failed: {exc}")
