"""Run each scheduled execution in its own persisted, unattended owner chat."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from .chat_binding import SURFACE, _workspace, pair_for
from .routines import agent_id_from_tags


async def guard_owned_routine(runtime: Any, tags: tuple[str, ...]) -> Any:
    """Check live owner availability for both chat and native workflow actions."""
    agent_id = agent_id_from_tags(tags)
    if agent_id is None:
        return None
    if await runtime.store.kill_switch():
        raise RuntimeError("The society is halted")
    agent = await runtime.roster.get(agent_id)
    if agent is None or str(agent.state) != "active":
        raise RuntimeError("The routine owner is unavailable or paused")
    return agent


async def run_owned_routine(
    runtime: Any,
    task_id: str,
    tags: tuple[str, ...],
    prompt: str,
    cancel_token: Any = None,
) -> str | None:
    agent = await guard_owned_routine(runtime, tags)
    if agent is None:
        return None
    service = runtime.chat_service()
    if service is None:
        raise RuntimeError("The canonical chat service is unavailable")
    from jarvis.agent_chat.effort import default_effort

    cfg = runtime.config()
    provider, model, effort = pair_for(cfg, agent)
    session = service.store.create_session(
        session_id=f"{agent.session_id}:routine:{task_id}:{uuid4().hex}",
        surface=SURFACE,
        provider=provider,
        model=model,
        effort=effort or default_effort(provider),
        account_id=agent.account_id,
        cwd=_workspace(cfg, agent),
        permission_mode="bypass",
        title=f"{agent.name} · Routine {task_id}",
    )
    # Persist the link before starting work, including runs that fail or are cancelled.
    task_store, _ = runtime.task_services()
    if task_store is not None:
        await task_store.append_step(
            task_id, "log", {"event": "routine_chat", "session_id": session.session_id}
        )
    # Legacy tasks contain an identity snapshot. The live briefing owns identity now.
    _, separator, original = prompt.partition("\nRoutine:\n")
    task = original if separator else prompt
    task = (
        f"Scheduled routine {task_id}. Follow your CURRENT standing instructions.\n"
        "This execution has its own background chat with bypass permissions.\n"
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
                if payload.get("status") == "cancelled":
                    raise asyncio.CancelledError
                if payload.get("status") not in {"done", "ok", "completed"}:
                    raise RuntimeError(str(payload.get("error") or "The routine failed"))
                return answer
    except asyncio.CancelledError:
        await service.cancel(session.session_id)
        raise
    except Exception as exc:
        # A started execution must not silently move to another agent/context.
        raise RuntimeError(f"The routine chat failed: {exc}") from exc
    finally:
        service.unsubscribe(session.session_id, queue)
