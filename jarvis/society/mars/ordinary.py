"""Adapt a Mars draft station to the existing ordinary-agent task authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jarvis.society.events import AgentState, MsgType, SocietyEnvelope

from .models import reject_draft_credentials

if TYPE_CHECKING:
    from jarvis.society.runtime import SocietyRuntime


class OrdinaryStationExecutor:
    """No renderer callbacks, outgoing sends or alternative task scheduler."""

    def __init__(self, runtime: SocietyRuntime) -> None:
        self.runtime = runtime

    async def authorize(self, *, agent_id: str, capability_id: str) -> None:
        if capability_id != "communication-draft":
            raise PermissionError("station capability is unavailable")
        agent = await self.runtime.roster.get(agent_id)
        if agent is None or agent.state is not AgentState.ACTIVE:
            raise PermissionError("an active ordinary agent is required")
        if await self.runtime.store.kill_switch():
            raise PermissionError("agent execution is stopped")
        service = self.runtime.chat_service()
        if service is None:
            raise PermissionError("agent chat is unavailable")
        from jarvis.agent_chat.control import supports_restricted_turn
        from jarvis.society.chat_binding import ensure_session

        session = ensure_session(service, self.runtime.config(), agent)
        if not supports_restricted_turn(session):
            raise PermissionError("this agent runner cannot enforce a draft-only task")

    async def dispatch(
        self, *, agent_id: str, command_id: str, trace_id: str, draft: str
    ) -> dict[str, Any]:
        reject_draft_credentials(draft)
        await self.authorize(agent_id=agent_id, capability_id="communication-draft")
        event_id = f"mars-assign:{command_id}"
        previous = await self.runtime.store.get_event(event_id)
        if previous is None:
            await self.runtime.store.append_and_publish(
                SocietyEnvelope(
                    event_id=event_id,
                    msg_type=MsgType.ASSIGN,
                    from_agent="user",
                    to_agent=agent_id,
                    trace_id=trace_id,
                    payload={
                        "text": (
                            "Prepare a communication draft for review. "
                            "Do not send or publish it.\n\n" + draft
                        ),
                        "runner": "chat",
                        "read_only": True,
                        "mars_command_id": command_id,
                    },
                )
            )
        elif previous.to_agent != agent_id or previous.trace_id != trace_id:
            raise PermissionError("station command identity conflict")
        return await self.inspect(
            agent_id=agent_id, command_id=command_id, trace_id=trace_id, task_ref=None
        )

    async def inspect(
        self, *, agent_id: str, command_id: str, trace_id: str, task_ref: str | None
    ) -> dict[str, Any]:
        assignment_id = f"mars-assign:{command_id}"
        assignment = await self.runtime.store.get_event(assignment_id)
        if assignment is None or assignment.to_agent != agent_id or assignment.trace_id != trace_id:
            return {"state": "unknown"}
        events = await self.runtime.store.events_for_trace(trace_id)
        related = [event for event in events if event.parent_event_id == assignment_id]
        result = next(
            (
                event
                for event in reversed(related)
                if event.msg_type is MsgType.RESULT and event.from_agent == agent_id
            ),
            None,
        )
        claim = next(
            (
                event
                for event in related
                if event.msg_type is MsgType.CLAIM and event.from_agent == agent_id
            ),
            None,
        )
        # Ownership comes from the persisted assignment's own children, never
        # from a stale caller-supplied reference that could target a newer turn.
        source = result if result is not None else claim
        run_id = str(source.payload.get("run_id") or "") if source is not None else ""
        if task_ref and run_id and task_ref != run_id:
            return {"state": "unknown", "task_ref": task_ref}
        terminal = await self._chat_terminal(agent_id, run_id)
        if result is not None:
            state = "completed" if result.payload.get("status") == "done" else "failed"
            if terminal is not None and terminal["state"] == "canceled":
                state = "canceled"
            return {
                "state": state,
                "task_ref": result.payload.get("run_id"),
                "result_ref": result.event_id,
            }
        veto = next((event for event in related if event.msg_type is MsgType.VETO), None)
        if veto is not None:
            return {
                "state": "failed",
                "result_ref": veto.event_id,
            }
        # Chat commits its terminal event before the asynchronous Society
        # watcher writes RESULT. The gap cannot turn natural completion into a
        # cancellation, or lose a confirmed cancellation after a disconnect.
        if terminal is not None:
            return terminal
        if run_id and self.runtime.scheduler.running.get(run_id) == agent_id:
            return {"state": "active", "task_ref": run_id}
        return {
            "state": "unknown",
            "task_ref": run_id or task_ref,
        }

    async def _chat_terminal(self, agent_id: str, run_id: str) -> dict[str, Any] | None:
        if not run_id.startswith("turn:"):
            return None
        service = self.runtime.chat_service()
        agent = await self.runtime.roster.get(agent_id)
        if service is None or agent is None:
            return None
        terminal = service.store.turn_terminal(agent.session_id, run_id[5:])
        if terminal is None:
            return None
        status = str(terminal["payload"].get("status") or "")
        if status in {"ok", "done", "completed"}:
            state = "completed"
        elif status in {"canceled", "cancelled"}:
            state = "canceled"
        elif status in {"error", "failed", "blocked"}:
            state = "failed"
        else:
            return None
        return {
            "state": state,
            "task_ref": run_id,
            "result_ref": f"chat:{agent.session_id}#seq={terminal['seq']}",
        }

    async def cancel(
        self, *, agent_id: str, command_id: str, trace_id: str, task_ref: str | None
    ) -> dict[str, Any]:
        current = await self.inspect(
            agent_id=agent_id, command_id=command_id, trace_id=trace_id, task_ref=task_ref
        )
        if current["state"] in ("completed", "failed", "canceled"):
            return {**current, "cancel_attempt": "not_attempted"}
        run_id = current.get("task_ref")
        agent = await self.runtime.roster.get(agent_id)
        service = self.runtime.chat_service()
        if not agent or service is None or not str(run_id or "").startswith("turn:"):
            return {**current, "cancel_attempt": "not_attempted"}
        attempted = await service.cancel(agent.session_id, expected_turn_id=run_id[5:])
        # A True return means a stop was requested, not that the task ended by
        # cancellation: it may have finished naturally during the grace period.
        outcome = await self.inspect(
            agent_id=agent_id, command_id=command_id, trace_id=trace_id, task_ref=run_id
        )
        return {**outcome, "cancel_attempt": "attempted" if attempted else "not_attempted"}
