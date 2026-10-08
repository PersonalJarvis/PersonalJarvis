"""Run each scheduled execution in its own persisted, unattended owner chat."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from jarvis.core.protocols import RoutineDeferred

from .chat_binding import SURFACE, _workspace, pair_for
from .routines import agent_id_from_tags, routine_seat

log = logging.getLogger(__name__)

#: Marks a per-execution routine chat: ``society:<agent>:routine:<task>:<run>``.
ROUTINE_SESSION_MARKER = ":routine:"


def is_routine_session(session_id: str) -> bool:
    """Whether ``session_id`` is an unattended routine execution's own chat."""
    return ROUTINE_SESSION_MARKER in (session_id or "")


async def guard_owned_routine(runtime: Any, tags: tuple[str, ...]) -> Any:
    """Check live owner availability for both chat and native workflow actions."""
    agent_id = agent_id_from_tags(tags)
    if agent_id is None:
        return None
    from .cloud_admission import assert_local_owner

    if await runtime.store.kill_switch():
        raise RuntimeError("The society is halted")
    agent = await runtime.roster.get(agent_id)
    if agent is None or str(agent.state) != "active":
        raise RuntimeError("The routine owner is unavailable or paused")
    try:
        assert_local_owner(runtime, agent_id)
    except PermissionError as exc:
        from jarvis.core.protocols import RoutineDeferred

        raise RoutineDeferred(str(exc)) from exc
    return agent


def _billed_via_api(provider: str, account_id: str = "") -> bool:
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
    return resolve_runner(provider, surface=SURFACE, account_id=account_id) in (
        "brain", "api", "unknown",
    )


async def _subscription_seat(cfg: Any) -> tuple[str, str, str] | None:
    """The Jarvis chat's current subscription seat, if it has one.

    Mirrors what a typed turn on the front page resolves to
    (``AgentChatService.send``): the global worker pick mapped through the
    subscription aliases. ``None`` when the front page itself runs on an API
    key or is unconfigured.
    """
    try:
        from jarvis.core.model_selection import worker_selection
        from jarvis.core.task_agent import subscription_seat_off_loop
    except Exception:  # noqa: BLE001 — no selection layer: no subscription seat
        return None
    try:
        selection = worker_selection(cfg)
    except Exception:  # noqa: BLE001 — unreadable config reads as no seat
        return None
    if selection is None or not selection.provider:
        return None
    mapped = await subscription_seat_off_loop(selection.provider)
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
    subscription = await _subscription_seat(cfg)
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


def _run_runtime(agent: Any, provider: str) -> str:
    """Which runtime a routine run uses.

    The run keeps its owner's Hermes / OpenClaw runtime on the owner's own
    model — the seat the person chose for that agent. A run pinned to another
    seat keeps the runtime only when that seat is an API key or local server:
    a subscription seat (Claude Code's dual row included) runs on Jarvis' own
    runtime, so a scheduled run never quietly moves onto an API key.
    """
    runtime = str(getattr(agent, "runtime", "") or "jarvis")
    if runtime == "jarvis":
        return ""
    from jarvis.agent_chat.service import resolve_runner
    from jarvis.agent_runtimes.model_map import supports

    if not supports(provider):
        return ""
    if provider == str(getattr(agent, "provider", "") or ""):
        return runtime
    return "" if resolve_runner(provider, surface=SURFACE).endswith("-cli") else runtime


def _left_runtime(agent: Any, run_runtime: str) -> str:
    """The owner's Hermes / OpenClaw runtime this run does NOT use, or ""."""
    owner = str(getattr(agent, "runtime", "") or "jarvis")
    return owner if owner != "jarvis" and not run_runtime else ""


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
    autonomous = "autonomous" in tags
    if autonomous and _billed_via_api(provider, account_id):
        raise RoutineDeferred(
            "Background work is waiting for a subscription or local model on its owner's seat"
        )
    permission_mode = "bypass"
    if autonomous:
        from .autonomous_routines import ORIGIN_PERMISSION_TAG, ORIGIN_SESSION_TAG

        origin_id = next((t[len(ORIGIN_SESSION_TAG):] for t in tags
                          if t.startswith(ORIGIN_SESSION_TAG)), "")
        origin = service.store.get_session(origin_id)
        saved_mode = next((t[len(ORIGIN_PERMISSION_TAG):] for t in tags
                           if t.startswith(ORIGIN_PERMISSION_TAG)), "ask")
        if origin is None or origin.permission_mode in {"plan", "read-only"}:
            raise RoutineDeferred("Background work is waiting for its originating chat permissions")
        # Society chats share ask / accept-edits / bypass. Unknown modes are
        # conservative; later permission expansion never widens the saved grant.
        order = {"ask": 0, "accept-edits": 1, "bypass": 2}
        permission_mode = min((saved_mode, origin.permission_mode), key=lambda m: order.get(m, -1))
    run_runtime = _run_runtime(agent, provider)
    session = service.store.create_session(
        session_id=f"{agent.session_id}{ROUTINE_SESSION_MARKER}{task_id}:{uuid4().hex}",
        surface=SURFACE,
        provider=provider,
        model=model,
        effort=effort or default_effort(provider),
        account_id=account_id,
        cwd=_workspace(cfg, agent),
        permission_mode=permission_mode,
        title=f"{agent.name} · Routine {task_id}",
        runtime=run_runtime,
    )
    # Persist the link before starting work, including runs that fail or are cancelled.
    task_store, _ = runtime.task_services()
    if task_store is not None:
        await task_store.append_step(
            task_id, "log", {"event": "routine_chat", "session_id": session.session_id}
        )
    left = _left_runtime(agent, run_runtime)
    if left:
        # A run pinned to a seat its owner's runtime cannot drive runs on
        # Jarvis' own loop; the run log and its chat say so instead of silently.
        note = (
            f"This run uses Jarvis' own runtime, not {left}: its model "
            f"({provider}) is a seat {left} cannot drive."
        )
        if task_store is not None:
            await task_store.append_step(
                task_id,
                "log",
                {"event": "routine_runtime_fallback", "runtime": left, "provider": provider,
                 "text": note},
            )
        try:
            await service.post_notice(
                session.session_id,
                {"kind": "routine_runtime_fallback", "runtime": left, "text": note},
            )
        except Exception:  # noqa: BLE001 — the run log above already holds the note
            log.warning("society: runtime note not posted for %s", task_id, exc_info=True)
    # Legacy tasks contain an identity snapshot. The live briefing owns identity now.
    _, separator, original = prompt.partition("\nRoutine:\n")
    task = original if separator else prompt
    # The run keeps its own seat and chat; the agent's one chat shows a card
    # that opens it (MASTERPLAN §2.10).
    title = next((line.strip() for line in task.splitlines() if line.strip()), task_id)
    try:
        await runtime.post_chat_notice(
            agent,
            {
                "kind": "routine_run",
                "task_id": task_id,
                "session_id": session.session_id,
                "agent_id": agent.agent_id,
                "agent_name": agent.name,
                "text": title[:200],
            },
        )
    except Exception:  # noqa: BLE001 — the card is a projection; the run itself goes on
        log.warning("society: routine card not posted for %s", agent.agent_id, exc_info=True)
    task = (
        f"Scheduled routine {task_id}. Follow your CURRENT standing instructions.\n"
        "This execution has its own background chat and must honor its permission limits.\n"
        "Use your memory and conversation archive for prior results. For information watches, "
        "check sources and dates, remember last-seen items, "
        "and report only meaningful new findings.\n\n" + task
    )
    queue = service.subscribe(session.session_id)
    answer = ""
    read_failures = 0
    try:
        turn_id = await service.send(session.session_id, task, direct_user=False, routine_run=True)
        while True:
            if cancel_token is not None and cancel_token.is_cancelled():
                raise asyncio.CancelledError
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except TimeoutError:
                # A subscriber queue is a projection; the durable terminal
                # remains authoritative after a dropped notification.
                reader = getattr(service.store, "turn_terminal", None)
                if reader is None:
                    continue
                try:
                    event = reader(session.session_id, turn_id)
                    if event is None:
                        read_failures = 0
                        continue
                    texts = [
                        str(e["payload"].get("text") or "")
                        for e in service.store.list_events(session.session_id)
                        if e["kind"] == "assistant_text"
                        and e["payload"].get("turn_id") == turn_id
                    ]
                    answer = "\n\n".join(texts) or answer
                    read_failures = 0
                except Exception:
                    log.warning("routine: durable completion read failed", exc_info=True)
                    read_failures += 1
                    if read_failures < 3:
                        continue
                    await service.cancel(session.session_id)
                    raise RuntimeError("The routine completion store is unavailable") from None
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
                if autonomous:
                    await _report_autonomous_result(runtime, agent, tags, task_id, answer)
                return answer
    except asyncio.CancelledError:
        await service.cancel(session.session_id)
        raise
    except Exception as exc:
        # A started execution must not silently move to another agent/context.
        raise RuntimeError(f"The routine chat failed: {exc}") from exc
    finally:
        service.unsubscribe(session.session_id, queue)


async def _report_autonomous_result(
    runtime: Any, agent: Any, tags: tuple[str, ...], task_id: str, answer: str,
) -> None:
    """Deliver the result to the originating chat without another model turn."""
    from .autonomous_routines import ORIGIN_SESSION_TAG
    from .surface import agent_id_of

    origin = next((tag[len(ORIGIN_SESSION_TAG):] for tag in tags
                   if tag.startswith(ORIGIN_SESSION_TAG)), agent.session_id)
    payload = {
        "kind": "society_result", "status": "done", "text": answer,
        "agent_id": agent.agent_id, "agent_name": agent.name,
        "source": "routine", "task_id": task_id,
    }
    try:
        service = runtime.chat_service()
        if agent_id_of(origin) == agent.agent_id and service.store.get_session(origin):
            await service.post_notice(origin, payload)
        else:
            await runtime.post_chat_notice(agent, payload)
    except Exception:
        # The task runner persists the answer independently of this projection.
        log.warning("routine: result delivery failed for %s", task_id, exc_info=True)
