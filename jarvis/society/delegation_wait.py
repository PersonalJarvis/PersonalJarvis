"""Read correlated replies from the durable board, including very fast jobs."""

from __future__ import annotations

from typing import Any

from jarvis.core.delegated_work import DelegatedWork, register_delegated_work

from .communication import reply_policy
from .events import MsgType, SocietyEnvelope


async def result_for(store: Any, request: SocietyEnvelope) -> dict[str, Any] | None:
    events = await store.events_for_trace(request.trace_id)
    related = [row for row in events if row.parent_event_id == request.event_id]
    claims = [
        row
        for row in related
        if row.msg_type is MsgType.CLAIM and row.from_agent == request.to_agent
    ]
    for claim in claims:
        run_id = str(claim.payload.get("run_id") or "")
        if run_id and not run_id.startswith("turn:"):
            related.extend(
                row
                for row in await store.events_for_trace(f"mission:{run_id}")
                if row.msg_type is MsgType.RESULT
                and row.from_agent == request.to_agent
                and row.payload.get("run_id") == run_id
            )
    for row in reversed(related):
        if row.msg_type is MsgType.VETO and row.from_agent == "scheduler":
            return {"status": "blocked", "report": row.text}
        if row.from_agent != request.to_agent or row.to_agent not in (request.from_agent, None):
            continue
        if row.msg_type is MsgType.RESULT:
            return {
                "status": str(row.payload.get("status") or "unknown"),
                "report": str(row.payload.get("done") or row.text),
                "evidence": row.payload.get("evidence", []),
                "open": row.payload.get("open", []),
            }
        if row.msg_type in (MsgType.QUERY, MsgType.PROPOSE):
            return {"status": "needs_input", "report": row.text}
        if row.msg_type is MsgType.ANSWER and (
            request.msg_type is not MsgType.ASSIGN or row.payload.get("reply_status") == "blocked"
        ):
            return {"status": str(row.payload.get("reply_status") or "done"), "report": row.text}
    return None


async def track_request(runtime: Any, request: SocietyEnvelope) -> None:
    if request.msg_type not in (MsgType.ASSIGN, MsgType.QUERY, MsgType.SAY, MsgType.PROPOSE):
        return
    if reply_policy(request) != "always":
        return
    target = await runtime.roster.get(request.to_agent)
    register_delegated_work(
        DelegatedWork(
            key="society:" + request.event_id,
            name=target.name if target else str(request.to_agent),
            probe=lambda: result_for(runtime.store, request),
        )
    )
