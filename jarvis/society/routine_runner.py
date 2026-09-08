"""Run a scheduled task through the owner's canonical chat and current policy."""

from __future__ import annotations

import asyncio
from typing import Any

from .chat_binding import ensure_session
from .routines import agent_id_from_tags


async def run_owned_routine(
    runtime: Any, task_id: str, tags: tuple[str, ...], prompt: str, cancel_token: Any = None
) -> str | None:
    agent_id = agent_id_from_tags(tags)
    if agent_id is None:
        return None
    if await runtime.store.kill_switch():
        raise RuntimeError("The society is halted")
    agent = await runtime.roster.get(agent_id)
    if agent is None or str(agent.state) != "active":
        raise RuntimeError("The routine owner is unavailable or paused")
    service = runtime.chat_service()
    if service is None:
        raise RuntimeError("The canonical chat service is unavailable")
    session = ensure_session(service, runtime.config(), agent)
    if service.is_running(session.session_id):
        raise RuntimeError("The routine owner is busy; retry this scheduled run later")
    # Legacy tasks contain an identity snapshot. The live briefing owns identity now.
    _, separator, original = prompt.partition("\nRoutine:\n")
    task = original if separator else prompt
    task = (
        f"Scheduled routine {task_id}. Follow your CURRENT standing instructions and permissions.\n"
        "Use your memory and conversation archive for prior results. For information watches, "
        "check sources and dates, remember last-seen items, "
        "and report only meaningful new findings.\n\n" + task
    )
    queue = service.subscribe(session.session_id)
    answer = ""
    try:
        turn_id = await service.send(session.session_id, task, direct_user=False)
        while True:
            if cancel_token is not None and cancel_token.is_cancelled():
                await service.cancel(session.session_id)
                raise asyncio.CancelledError
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            payload = event.get("payload") or {}
            if payload.get("turn_id") not in (None, turn_id):
                continue
            if event.get("kind") == "assistant_text":
                answer = str(payload.get("text") or answer)
            if event.get("kind") == "turn_finished":
                if payload.get("status") not in {"done", "ok", "completed"}:
                    raise RuntimeError(str(payload.get("error") or "The routine failed"))
                return answer
    except asyncio.CancelledError:
        await service.cancel(session.session_id)
        raise
    finally:
        service.unsubscribe(session.session_id, queue)
