"""Authenticated live browser view and automatic session preparation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, WebSocket

from .society_routes import _runtime
from .surface_security import credentials_valid

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/society", tags=["society"])
OPERATIONS = {
    "takeover",
    "cancel",
    "navigate",
    "back",
    "forward",
    "reload",
    "tab",
    "click",
    "move",
    "scroll",
    "text",
    "key",
    "dialog",
}


async def _subscribe_with_progress(live: Any, agent: Any, send: Any) -> Any:
    """Keep the viewer alive during provisioning and cold Chromium startup."""
    await send({"kind": "starting"})
    task = asyncio.create_task(live.subscribe(agent))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=2)
            if not done:
                await send({"kind": "starting"})
        return await task
    except BaseException:
        task.cancel()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        if isinstance(result, tuple):
            session, queue = result
            await live.unsubscribe(session, queue, "")
        raise


def validate_control(value: Any) -> tuple[str, dict]:
    if not isinstance(value, dict) or value.get("op") not in OPERATIONS:
        raise ValueError("Unsupported browser control")
    op = value["op"]
    args = value.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("Invalid browser control arguments")
    if op == "takeover" and not isinstance(args.get("enabled"), bool):
        raise ValueError("Browser control needs an explicit enabled state")
    if "login" in args and (
        op != "takeover"
        or type(args["login"]) is not bool
        or args.get("enabled") is not args["login"]
    ):
        raise ValueError("Chrome sign-in needs an explicit manual takeover")
    for key in ("x", "y", "dx", "dy"):
        if key in args and (
            not isinstance(args[key], (int, float))
            or not math.isfinite(args[key])
            or abs(args[key]) > 10000
        ):
            raise ValueError("Invalid browser coordinates")
    if op in {"click", "move"} and not all(k in args for k in ("x", "y")):
        raise ValueError("Click needs coordinates")
    if "button" in args and (
        not isinstance(args["button"], str) or args["button"] not in {"left", "right", "middle"}
    ):
        raise ValueError("Unsupported browser mouse button")
    if "move_only" in args and type(args["move_only"]) is not bool:
        raise ValueError("Invalid browser pointer motion")
    if "count" in args and (type(args["count"]) is not int or args["count"] not in {1, 2}):
        raise ValueError("Unsupported browser click count")
    if "geometry_id" in args and (
        not isinstance(args["geometry_id"], str) or len(args["geometry_id"]) > 128
    ):
        raise ValueError("Invalid browser frame geometry")
    for key in ("text", "key", "url", "target", "generation"):
        if key in args and (not isinstance(args[key], str) or len(args[key]) > 8192):
            raise ValueError("Browser input is too large")
    return op, args


@router.post("/agents/{agent_id}/browser/session", openapi_extra={"x-jarvis-dangerous": True})
async def ensure_agent_browser(agent_id: str, request: Request) -> dict[str, Any]:
    """Prepare the managed environment automatically when opening an agent."""
    from jarvis.society.browser import install

    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    status = await asyncio.to_thread(rt.browser.status_for, agent)
    if status.get("mode") in {"chrome", "unavailable"}:
        return status
    install.start_install(rt.data_dir)
    return install.snapshot(rt.data_dir)


@router.get("/agents/{agent_id}/browser/open")
async def agent_browser_open(agent_id: str, request: Request) -> dict[str, Any]:
    """Whether this agent's browser is running — a read that never launches one."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    session = rt.browser.live.sessions.get(agent.agent_id)
    is_open = session is not None and not session.closed
    status = await asyncio.to_thread(rt.browser.status_for, agent)
    return {**status, "open": is_open, "running": is_open and session.run_lock.locked()}


@router.post("/browser/repair", openapi_extra={"x-jarvis-dangerous": True})
async def repair_browser(request: Request) -> dict[str, Any]:
    """Rebuild and verify the managed browser environment."""
    from jarvis.society.browser import install

    rt = await _runtime(request)
    install.start_install(rt.data_dir, repair=True)
    return install.snapshot(rt.data_dir)


@router.post("/agents/{agent_id}/browser/restart", openapi_extra={"x-jarvis-dangerous": True})
async def restart_agent_browser(agent_id: str, request: Request) -> dict[str, bool]:
    """Restart this agent's managed browser with its saved profile."""
    from jarvis.society.browser.recovery import restart_browser

    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    try:
        await restart_browser(rt.browser.live, agent)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except (RuntimeError, TimeoutError):
        log.warning("Browser restart failed for %s", agent_id, exc_info=True)
        raise HTTPException(
            503, "Browser restart failed. Try again in the browser panel."
        ) from None
    return {"restarted": True}


@router.post("/agents/{agent_id}/browser/cancel", openapi_extra={"x-jarvis-dangerous": True})
async def cancel_agent_browser(agent_id: str, request: Request) -> dict[str, Any]:
    """Stop a browser task even while a viewer is waiting to take control."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    session = rt.browser.live.sessions.get(agent.agent_id)
    if session is None or session.closed:
        return {"cancelled": False}
    running = session.run_lock.locked()
    # The viewer Stop button ends the planner too. A bare POST only releases
    # the browser: the agent calls that URL when a tool client has already
    # given up, and ending the chat there is the turn dying mid-task.
    # The desktop fetch sets Sec-Fetch-Site; a shell call does not.
    headers = getattr(request, "headers", None)
    stop_chat = bool(
        headers
        and (
            headers.get("x-jarvis-stop-chat") == "1"
            or str(headers.get("sec-fetch-site") or "").lower() == "same-origin"
        )
    )
    await rt.browser.live.cancel(session, end_turn=stop_chat)
    chat = getattr(request.app.state, "agent_chat", None)
    if running and stop_chat and chat is not None:
        chat_session_id = getattr(session, "active_chat", "") or f"society:{agent.agent_id}"
        await chat.cancel(chat_session_id)
    return {"cancelled": running}


@router.websocket("/agents/{agent_id}/browser/live")
async def agent_browser_live(websocket: WebSocket, agent_id: str) -> None:
    if not credentials_valid(websocket.scope):
        await websocket.close(code=4401)
        return
    rt = await _runtime(websocket)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    owner = uuid4().hex
    live = rt.browser.live
    session = None
    queue = None
    sender = None
    receive = None
    pending = None
    write_lock = asyncio.Lock()

    async def send(value: dict) -> None:
        if "manual" in value and session is not None:
            value = {**value, "manual": session.control_owner == owner}
        async with write_lock:
            async with asyncio.timeout(10):
                await websocket.send_json(value)

    try:
        session, queue = await _subscribe_with_progress(live, agent, send)

        async def frames() -> None:
            while True:
                event = await queue.get()
                await send(event)
                if event["kind"] == "disconnected":
                    await websocket.close(code=1012)
                    return

        sender = asyncio.create_task(frames())
        commands: asyncio.Queue[tuple[str, dict, str]] = asyncio.Queue(maxsize=256)
        # Hover motion arrives ~30 times a second. Queued one by one behind a
        # slower worker round trip it delayed every click by seconds and then
        # overflowed the queue, so only the newest motion waits, behind input.
        hover: list[tuple[str, dict, str]] = []
        hover_ready = asyncio.Event()

        async def next_control() -> tuple[str, dict, str]:
            while True:
                if not commands.empty():
                    return commands.get_nowait()
                if hover:
                    hover_ready.clear()
                    return hover.pop()
                getter = asyncio.ensure_future(commands.get())
                waiter = asyncio.ensure_future(hover_ready.wait())
                try:
                    done, _ = await asyncio.wait(
                        {getter, waiter}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    waiter.cancel()
                    if getter not in done:
                        getter.cancel()
                if getter in done:
                    return getter.result()

        async def controls() -> None:
            while True:
                op, args, generation = await next_control()
                motion = op == "click" and args.get("move_only") is True
                try:
                    current = session.state.get("generation", session.generation)
                    if op not in {"takeover", "cancel"} and generation != current:
                        raise ValueError("The browser changed; wait for its new image")
                    result = await live.control(session, owner, op, args)
                    if not motion:
                        await send({"kind": "control", "op": op, "ok": True, **result})
                except (ValueError, RuntimeError) as exc:
                    if motion:
                        # The next motion replaces a stale one; a hover error
                        # must not overwrite the viewer's visible state.
                        log.debug("Browser hover motion skipped: %s", exc)
                        continue
                    # Control errors are returned to the requesting viewer.
                    await send({"kind": "control", "op": op, "ok": False, "error": str(exc)[:500]})

        pending = asyncio.create_task(controls())
        receive = asyncio.create_task(websocket.receive_json())
        while True:
            done, _ = await asyncio.wait(
                {receive, sender, pending}, return_when=asyncio.FIRST_COMPLETED
            )
            if sender in done:
                receive.cancel()
                await asyncio.gather(receive, return_exceptions=True)
                await sender
                break
            if pending in done:
                await pending
                break
            value = receive.result()  # any receive error terminates this socket
            receive = asyncio.create_task(websocket.receive_json())
            try:
                op, args = validate_control(value)
                item = (op, args, session.state.get("generation", session.generation))
                if op == "click" and args.get("move_only") is True:
                    hover[:] = [item]
                    hover_ready.set()
                else:
                    # Motion recorded before this input is older than it.
                    hover.clear()
                    commands.put_nowait(item)
            except asyncio.QueueFull:  # the client is told below that the input was dropped
                await send(
                    {
                        "kind": "control",
                        "ok": False,
                        "error": "The browser is still catching up; try that again in a moment.",
                    }
                )
            except (ValueError, RuntimeError) as exc:
                # Invalid controls are visibly rejected without closing a healthy stream.
                await send({"kind": "control", "ok": False, "error": str(exc)[:500]})
    except Exception as exc:
        log.debug("Browser view disconnected for %s", agent_id, exc_info=True)
        with contextlib.suppress(Exception):
            if session is None:
                await send(
                    {
                        "kind": "error",
                        "error": str(exc)[:300]
                        if isinstance(exc, (RuntimeError, ValueError))
                        else "Browser startup failed. Retry or repair the installation.",
                    }
                )
            await websocket.close(code=1011)
    finally:
        for task in (receive, pending):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (receive, pending) if task is not None), return_exceptions=True
        )
        if sender:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if session and queue is not None:
            try:
                await live.unsubscribe(session, queue, owner)
            except Exception:
                log.debug("Browser viewer cleanup after disconnect", exc_info=True)
