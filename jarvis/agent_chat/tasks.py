"""Unattended subscription work with the same explicitly scoped Jarvis gateway."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from jarvis.core.task_agent import TaskToolScope, register_scope, release_scope, subscription_seat


async def run_subscription_task(*, selection, prompt, tool_names, trace_id=None) -> str:
    from jarvis.agent_chat.runner_api import TurnHandle
    from jarvis.agent_chat.runner_cli import run_cli_turn
    from jarvis.agent_chat.store import AgentChatSession
    from jarvis.core.paths import chat_workspace_dir
    from jarvis.core.protocols import ChatTurn, current_chat_turn

    seat = subscription_seat(selection.provider)
    if seat is None:
        raise RuntimeError("No subscription runner is registered for the selected agent.")
    provider, runner = seat
    # These CLI backends expose explicit isolation from native/user-installed tools.
    # Others are rejected before spawn instead of pretending the task grant is enforced.
    if runner not in {"codex-cli", "claude-cli"}:
        raise RuntimeError(
            "This subscription cannot enforce a task tool allowlist. "
            "Choose a supported agent in API Keys."
        )
    sid = "task-" + uuid4().hex
    tid = trace_id or uuid4()
    result = []
    failure = []

    async def emit(event):
        payload = event.get("payload", {})
        if event.get("kind") == "assistant_text":
            result.append(payload.get("text", ""))
        if event.get("kind") == "turn_finished" and payload.get("status") != "done":
            failure.append(payload.get("error") or payload.get("status"))

    async def deny(*_args):
        return "deny"

    folder = chat_workspace_dir()
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    session = AgentChatSession(
        sid,
        "Scheduled task",
        provider,
        selection.model or "",
        selection.reasoning_effort,
        str(folder),
        "read-only",
        None,
        0,
        0,
        0,
        "",
        surface="jarvis",
    )
    handle = TurnHandle(
        session=session,
        turn_id=str(tid),
        emit=emit,
        request_approval=deny,
        cancel=asyncio.Event(),
        trace_id=tid,
        surface="jarvis",
        gateway_only=True,
    )
    register_scope(sid, TaskToolScope(frozenset(tool_names), tid, prompt))
    origin = current_chat_turn.set(ChatTurn(sid, str(tid), prompt, False, str(tid)))
    try:
        await run_cli_turn(handle, prompt, runner, identity=True)
        if failure:
            raise RuntimeError("Subscription task failed: " + str(failure[-1]))
        if not result:
            raise RuntimeError("Subscription task returned no result.")
        return "\n".join(result)
    finally:
        current_chat_turn.reset(origin)
        release_scope(sid)
