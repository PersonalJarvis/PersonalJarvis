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
    "scroll",
    "text",
    "key",
    "dialog",
}


def validate_control(value: Any) -> tuple[str, dict]:
    if not isinstance(value, dict) or value.get("op") not in OPERATIONS:
        raise ValueError("Unsupported browser control")
    op = value["op"]
    args = value.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("Invalid browser control arguments")
    if op == "takeover" and not isinstance(args.get("enabled"), bool):
        raise ValueError("Browser control needs an explicit enabled state")
    for key in ("x", "y", "dx", "dy"):
        if key in args and (
            not isinstance(args[key], (int, float))
            or not math.isfinite(args[key])
            or abs(args[key]) > 10000
        ):
            raise ValueError("Invalid browser coordinates")
    if op == "click" and not all(k in args for k in ("x", "y")):
        raise ValueError("Click needs coordinates")
    for key in ("text", "key", "url", "target"):
        if key in args and (not isinstance(args[key], str) or len(args[key]) > 8192):
            raise ValueError("Browser input is too large")
    return op, args


@router.post("/agents/{agent_id}/browser/session", openapi_extra={"x-jarvis-dangerous": True})
async def ensure_agent_browser(agent_id: str, request: Request) -> dict[str, Any]:
    """Prepare the managed environment automatically when opening an agent."""
    from jarvis.society.browser import install

    rt = await _runtime(request)
    if await rt.roster.resolve(agent_id) is None:
        raise HTTPException(404, "Agent not found")
    install.start_install(rt.data_dir)
    return install.snapshot(rt.data_dir)


@router.post("/browser/repair", openapi_extra={"x-jarvis-dangerous": True})
async def repair_browser(request: Request) -> dict[str, Any]:
    """Rebuild and verify the managed browser environment."""
    from jarvis.society.browser import install

    rt = await _runtime(request)
    install.start_install(rt.data_dir, repair=True)
    return install.snapshot(rt.data_dir)


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
    await rt.browser.live.cancel(session)
    # The owning chat must also stop; otherwise its planner may try another tool.
    chat = getattr(request.app.state, "agent_chat", None)
    if running and chat is not None:
        await chat.cancel(f"society:{agent.agent_id}")
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
        async with write_lock:
            async with asyncio.timeout(10):
                await websocket.send_json(value)

    try:
        session, queue = await live.subscribe(agent)

        async def frames() -> None:
            while True:
                event = await queue.get()
                await send(event)
                if event["kind"] == "disconnected":
                    await websocket.close(code=1012)
                    return

        sender = asyncio.create_task(frames())
        commands: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=256)

        async def controls() -> None:
            while True:
                op, args = await commands.get()
                try:
                    result = await live.control(session, owner, op, args)
                    await send({"kind": "control", "ok": True, **result})
                except (ValueError, RuntimeError) as exc:
                    # Control errors are returned to the requesting viewer.
                    await send({"kind": "control", "ok": False, "error": str(exc)[:500]})

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
                commands.put_nowait((op, args))
            except (ValueError, RuntimeError) as exc:
                # Invalid controls are visibly rejected without closing a healthy stream.
                await send({"kind": "control", "ok": False, "error": str(exc)[:500]})
    except Exception:
        log.debug("Browser view disconnected for %s", agent_id, exc_info=True)
        with contextlib.suppress(Exception):
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
