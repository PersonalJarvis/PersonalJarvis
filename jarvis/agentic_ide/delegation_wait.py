"""Return coding jobs to the calling chat using recorded lifecycle evidence.

An idle terminal or a delivery acknowledgement is not task completion. Only a
fresh CLI end marker plus the answer to this submission can finish a wait.
Unsupported/remote transcript formats produce an honest unverified result.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.delegated_work import DelegatedWork, register_delegated_work

from . import agent_transcript


def _identity(term: Any) -> tuple:
    return term.process_generation, term.last_submit_at


def _read(term: Any, identity: tuple) -> dict[str, Any] | None:
    from .session import account_home

    if getattr(term, "computer_id", "") or not agent_transcript.can_read(term.agent):
        return {
            "status": "unverified",
            "report": (
                "This coding session has no supported local completion record. "
                "Inspect its context; delivery alone does not prove completion."
            ),
        }
    if term.resume is None:
        return None
    events = (
        agent_transcript.read_events(
            term.agent,
            term.resume.id,
            home=account_home(term.agent, term.account),
            live=True,
        )
        or []
    )
    start = next(
        (i for i in range(len(events) - 1, -1, -1) if events[i]["kind"] == "user_message"), None
    )
    if start is None:
        return None
    user = events[start]
    expected = agent_transcript._clip(agent_transcript._spoken(term.last_prompt))
    if int(user.get("ts_ms") or 0) < int(float(identity[1] or 0) * 1000) or " ".join(
        user["payload"].get("text", "").split()
    ) != " ".join(expected.split()):
        return None
    own = events[start + 1 :]
    calls = {e["payload"].get("call_id"): e["payload"] for e in own if e["kind"] == "tool_call"}
    for event in own:
        if event["kind"] == "tool_result":
            calls.pop(event["payload"].get("call_id"), None)
    for call in calls.values():
        if str(call.get("name", "")).rsplit(".", 1)[-1] in {
            "request_user_input",
            "request_user_input_async",
            "AskUserQuestion",
        }:
            return {"status": "needs_input", "report": str(call.get("input") or "")}
    if not own or own[-1]["kind"] != "turn_finished":
        return (
            {
                "status": "running",
                "progress": str(own[-1].get("ts_ms")) + ":" + str(own[-1].get("seq")),
            }
            if own
            else None
        )
    finish = own[-1]["payload"]
    status = finish.get("status")
    report = next(
        (
            str(e["payload"].get("text") or "")
            for e in reversed(own)
            if e["kind"] == "assistant_text"
            and e["payload"].get("turn_id") == finish.get("turn_id")
        ),
        "",
    )
    if status in {"cancelled", "error"}:
        return {"status": status, "report": report or str(finish.get("error") or status)}
    if status != "done" or not report.strip():
        return None
    return {
        "status": "completed",
        "report": report,
        "evidence": "Recorded CLI completion and matching report; not independent verification",
    }


def track_submission(registry: Any, owner: Any, term: Any) -> None:
    if term.submitted is False or term.last_submit_at is None:
        return
    identity = _identity(term)

    async def probe() -> dict[str, Any] | None:
        def current() -> bool:
            return registry.find_terminal("pane:" + term.history_id, owner.id) == (owner, term)

        if not current():
            return {"status": "stopped", "report": "The delegated coding pane was closed."}
        if identity != _identity(term):
            return {
                "status": "superseded",
                "report": (
                    "The pane received another submission or restarted. "
                    "Completion of the original task is unverified."
                ),
            }
        if term.status in {"failed", "exited"}:
            return {"status": term.status, "report": f"Coding process ended: {term.status}."}
        result = await asyncio.to_thread(_read, term, identity)
        if not current() or identity != _identity(term):
            return {
                "status": "superseded",
                "report": "The coding session changed while reading its result.",
            }
        return result

    register_delegated_work(
        DelegatedWork(
            key=f"coding:{term.history_id}:{identity[0]}:{identity[1]}",
            name=str(term.name),
            probe=probe,
        )
    )
