"""REST + WebSocket API for the "Make It Yours" multi-agent workspace.

The terminals are embedded in the desktop app (xterm panes), so launching does
not open OS windows — it pre-trusts the folder and returns a per-slot plan; each
slot then connects to the PTY WebSocket below, which runs the agent in a real
PTY inside the Jarvis project folder.

Endpoints (prefix ``/api/workspace``):
- ``GET  /agents``        → detect Claude Code + Codex (installed? version?)
- ``POST /launch``        → validate + pre-trust + return the grid plan (slots)
- ``WS   /pty/{key}``     → interactive PTY running one agent (or its installer)
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from jarvis.core.paths import repo_root
from jarvis.terminal.pty_manager import PtyManager
from jarvis.workspace.agents import (
    AGENT_NAMES,
    build_agent_argv,
    build_install_argv,
    detect_agents,
    pty_available,
)
from jarvis.workspace.launcher import LAYOUT_CHOICES, plan_workspace, validate_split
from jarvis.workspace.trust import ensure_trusted

from .surface_security import credentials_valid

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


# --------------------------------------------------------------------------- #
# REST                                                                         #
# --------------------------------------------------------------------------- #
class AgentStatusModel(BaseModel):
    name: str
    display_name: str
    installed: bool
    version: str | None
    install_command: str | None
    launch_command: str


class AgentsResponse(BaseModel):
    cwd: str
    terminal_available: bool
    layout_choices: list[int]
    agents: list[AgentStatusModel]


class LaunchRequest(BaseModel):
    layout: int
    split: dict[str, int] = Field(default_factory=dict)


@router.get("/agents", response_model=AgentsResponse)
async def get_agents() -> AgentsResponse:
    infos = await detect_agents()
    return AgentsResponse(
        cwd=str(repo_root()),
        terminal_available=pty_available(),
        layout_choices=list(LAYOUT_CHOICES),
        agents=[
            AgentStatusModel(
                name=i.name,
                display_name=i.display_name,
                installed=i.installed,
                version=i.version,
                install_command=i.install_command,
                launch_command=i.launch_command,
            )
            for i in infos
        ],
    )


@router.post("/launch")
async def launch(req: LaunchRequest) -> dict:
    try:
        validate_split(req.layout, req.split)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return plan_workspace(req.layout, req.split).to_dict()


# --------------------------------------------------------------------------- #
# PTY WebSocket                                                                #
# --------------------------------------------------------------------------- #
def _pty_manager(app) -> PtyManager:  # noqa: ANN001
    mgr = getattr(app.state, "workspace_pty", None)
    if mgr is None:
        mgr = PtyManager()
        app.state.workspace_pty = mgr
    return mgr


@router.websocket("/pty/{key}")
async def workspace_pty(ws: WebSocket, key: str) -> None:
    """Run one agent (``?agent=claude``) or its installer (``?install=codex``) in
    a PTY, bridged bidirectionally to an xterm pane.

    Wire protocol (JSON both ways): client → ``{t:"i",d}`` input,
    ``{t:"r",cols,rows}`` resize; server → ``{t:"o",d}`` output, ``{t:"ready"}``,
    ``{t:"exit",code}``, ``{t:"error",message}``."""
    await ws.accept()
    if not credentials_valid(ws.scope):
        await ws.close(code=4401, reason="unauthorized")
        return

    qp = ws.query_params

    agent = qp.get("agent")
    install = qp.get("install")
    cols = _safe_int(qp.get("cols"), 80)
    rows = _safe_int(qp.get("rows"), 24)
    cwd = str(repo_root())

    if agent:
        if agent not in AGENT_NAMES:
            await ws.close(code=4400, reason="unknown agent")
            return
        ensure_trusted(repo_root(), [agent])  # idempotent, skips the trust dialog
        argv = build_agent_argv(agent)
    elif install:
        if install not in AGENT_NAMES:
            await ws.close(code=4400, reason="unknown agent")
            return
        argv = build_install_argv(install)
    else:
        await ws.close(code=4400, reason="missing agent/install")
        return

    if argv is None:
        await ws.send_json({"t": "error", "message": "No shell available on this host."})
        await ws.close(code=4404, reason="no shell")
        return

    mgr = _pty_manager(ws.scope["app"])
    send_lock = asyncio.Lock()

    async def on_output(_tid: str, text: str) -> None:
        async with send_lock:
            try:
                await ws.send_json({"t": "o", "d": text})
            except Exception:  # noqa: BLE001 - client gone; reader will stop
                pass

    async def on_closed(_tid: str, code: int) -> None:
        async with send_lock:
            try:
                await ws.send_json({"t": "exit", "code": code})
            except Exception:  # noqa: BLE001
                pass

    try:
        session = await mgr.spawn(
            shell_argv=argv,
            shell_id="workspace",
            cwd=cwd,
            cols=cols,
            rows=rows,
            on_output=on_output,
            on_closed=on_closed,
        )
    except Exception as exc:  # noqa: BLE001
        await ws.send_json({"t": "error", "message": str(exc)})
        await ws.close(code=4500, reason="spawn failed")
        return

    await ws.send_json({"t": "ready"})

    try:
        while True:
            try:
                msg = await ws.receive_json()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # AP-20: an unclean teardown raises RuntimeError, not
                # WebSocketDisconnect — treat any read error as terminal.
                break
            except Exception:  # noqa: BLE001 - malformed frame; keep the PTY alive
                continue
            kind = msg.get("t")
            if kind == "i":
                mgr.write(session.terminal_id, str(msg.get("d", "")))
            elif kind == "r":
                mgr.resize(
                    session.terminal_id,
                    _safe_int(msg.get("cols"), cols),
                    _safe_int(msg.get("rows"), rows),
                )
            # other frames are ignored
    finally:
        mgr.close(session.terminal_id)


def _safe_int(value: object, default: int) -> int:
    try:
        n = int(str(value))
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


__all__ = ["router"]
