"""Run each scheduled execution in its own persisted, unattended owner chat."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from .chat_binding import SURFACE, _workspace, pair_for
from .routines import agent_id_from_tags, routine_seat


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


def _billed_via_api(provider: str) -> bool:
    """Whether ``provider`` answers through an API key on the society surface.

    CLI seats (subscriptions) and keyless local providers never touch an API
    key; ``brain``/``api`` runners do. Decided by asking the runner and the
    row, never by matching a provider name (AP-21).
    """
    try:
        from jarvis.agent_chat.catalog import provider_row
        from jarvis.agent_chat.service import resolve_runner
    except Exception:  # noqa: BLE001 — without the catalog every seat counts as billed
        return True
    row = provider_row(provider)
    if row is not None and bool(getattr(row, "keyless", False)):
        return False
    return resolve_runner(provider, surface=SURFACE) in ("brain", "api", "unknown")


def _subscription_seat(cfg: Any) -> tuple[str, str, str] | None:
    """The Jarvis chat's current subscription seat, if it has one.

    Mirrors what a typed turn on the front page resolves to
    (``AgentChatService.send``): the global worker pick mapped through the
    subscription aliases. ``None`` when the front page itself runs on an API
    key or is unconfigured.
    """
    try:
        from jarvis.core.model_selection import worker_selection
        from jarvis.core.task_agent import subscription_seat
    except Exception:  # noqa: BLE001 — no selection layer: no subscription seat
        return None
    try:
        selection = worker_selection(cfg)
    except Exception:  # noqa: BLE001 — unreadable config reads as no seat
        return None
    if selection is None or not selection.provider:
        return None
    mapped = subscription_seat(selection.provider)
    if mapped is not None:
        return mapped[0], selection.model or "", selection.reasoning_effort or ""
    if not _billed_via_api(selection.provider):
        return selection.provider, selection.model or "", selection.reasoning_effort or ""
    return None


async def _seat_for_run(runtime: Any, agent: Any, task_id: str) -> tuple[str, str, str, str]:
    """The ``(provider, model, effort, account_id)`` this run answers on.

    A pinned routine seat wins (the model the owner ran on when the routine
    was created, or what the person later picked for it — an explicit choice,
    billed as chosen). Unpinned legacy rows follow the owner's live seat, but
    never slide silently onto an API-key chain: an owner without an explicit
    provider takes the Jarvis chat's subscription seat, and when there is no
    usable subscription seat the run fails honestly instead of billing a key.
    """
    cfg = runtime.config()
    pinned = {"provider": "", "model": "", "effort": "", "account_id": ""}
    try:
        task_store, _ = runtime.task_services()
        if task_store is not None:
            spec = await task_store.get_spec(task_id)
            if spec is not None:
                pinned = routine_seat(spec)
    except Exception:  # noqa: BLE001 — an unreadable spec falls back to the live seat
        pinned = {"provider": "", "model": "", "effort": "", "account_id": ""}
    if pinned["provider"]:
        return pinned["provider"], pinned["model"], pinned["effort"], pinned["account_id"]
    account_id = str(getattr(agent, "account_id", "") or "")
    if getattr(agent, "provider", ""):
        _provider, _model, _effort = pair_for(cfg, agent)
        return _provider, _model, _effort, account_id
    subscription = _subscription_seat(cfg)
    if subscription is not None:
        provider, model, effort = subscription
        return provider, model, effort, account_id
    try:
        provider, model, effort = pair_for(cfg, agent)
    except PermissionError as exc:
        raise RuntimeError(
            "The routine has no model seat: the owner names no provider and no "
            f"subscription seat is available ({exc}). Pick a model for the agent "
            "or for this routine."
        ) from exc
    if _billed_via_api(provider):
        raise RuntimeError(
            "The routine stays on its owner's model and was not rerouted: the owner "
            "names no provider and the only fallback would bill an API key. Pick a "
            "subscription model for the agent or for this routine."
        )
    return provider, model, effort, account_id


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
    provider, model, effort, account_id = await _seat_for_run(runtime, agent, task_id)
    session = service.store.create_session(
        session_id=f"{agent.session_id}:routine:{task_id}:{uuid4().hex}",
        surface=SURFACE,
        provider=provider,
        model=model,
        effort=effort or default_effort(provider),
        account_id=account_id,
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
            except TimeoutError:  # An empty poll window simply waits for the next event.
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
