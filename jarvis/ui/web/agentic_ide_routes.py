"""REST + WebSocket API for the Agentic IDE.

Every action of the feature is an endpoint (the CLI-first contract): the wizard,
the terminal grid, the voice commands and the ``jarvis`` CLI all drive the same
routes, so a pane's behaviour and a spoken command can never drift apart.

Endpoints (prefix ``/api/agentic-ide``):

* ``GET    /state``                      → current session (or none) + limits
* ``GET    /agents``                     → Claude Code / Codex install status
* ``GET    /folders``                    → browse folders (no path = start points)
* ``POST   /session``                    → open a workspace (folder + terminals)
* ``DELETE /session``                    → close it and stop every agent
* ``PUT    /mode``                       → focused coding mode on/off
* ``GET    /terminals/{name}/report``    → what one named terminal is doing
* ``POST   /terminals/{name}/prompt``    → type a prompt into it and press Enter
* ``WS     /pty/{name}``                 → the pane's live terminal stream

``{name}`` accepts the call-sign ("mika", "Mika") or a spoken phrase containing
it, so a voice command reaches the right pane without exact spelling.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from jarvis.agentic_ide.folders import list_dir, start_points
from jarvis.agentic_ide.names import default_names
from jarvis.agentic_ide.session import (
    AGENT_DISPLAY,
    MAX_PROMPT_CHARS,
    MAX_TERMINALS,
    SessionError,
    agent_argv,
    get_registry,
)

from .surface_security import credentials_valid

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agentic-ide", tags=["agentic-ide"])


# --------------------------------------------------------------------------- #
# Models                                                                      #
# --------------------------------------------------------------------------- #
class TerminalRequest(BaseModel):
    agent: str = Field(description="Coding agent to run: 'claude' or 'codex'.")
    name: str | None = Field(
        default=None,
        description="Call-sign for this terminal; auto-assigned when omitted.",
    )


class StartSessionRequest(BaseModel):
    folder: str = Field(description="Absolute path of the folder to work in.")
    terminals: list[TerminalRequest] = Field(
        default_factory=list,
        description="One entry per terminal, in grid order.",
    )


class ModeRequest(BaseModel):
    enabled: bool = Field(
        description="True narrows Jarvis to this workspace; False returns to normal."
    )


class PromptRequest(BaseModel):
    prompt: str = Field(
        max_length=MAX_PROMPT_CHARS,
        description="Text to type into the terminal, followed by Enter.",
    )


class AgentStatus(BaseModel):
    name: str
    display_name: str
    installed: bool
    version: str | None = None
    install_command: str | None = None


class AgentsResponse(BaseModel):
    terminal_available: bool
    max_terminals: int
    suggested_names: list[str]
    agents: list[AgentStatus]


class FolderItem(BaseModel):
    name: str
    path: str
    is_project: bool
    is_repo: bool


class FoldersResponse(BaseModel):
    path: str | None
    parent: str | None
    entries: list[FolderItem]
    error: str | None = None


# --------------------------------------------------------------------------- #
# REST                                                                        #
# --------------------------------------------------------------------------- #
@router.get("/state", summary="Agentic-IDE workspace state")
async def get_state() -> dict:
    """The open workspace, its terminals, and whether focus mode is on."""
    return get_registry().state()


@router.get("/agents", response_model=AgentsResponse, summary="Coding agents available")
async def get_agents() -> AgentsResponse:
    """Which coding-agent CLIs this machine can run, and how to install them.

    Detection reuses the shared CLI prober, then re-checks that the binary is
    resolvable the way the PTY will resolve it — a GUI process starts with a
    minimal PATH, so "installed" and "launchable from here" are not the same
    question.
    """
    from jarvis.workspace.agents import detect_agents, pty_available

    infos = await detect_agents()
    agents = [
        AgentStatus(
            name=info.name,
            display_name=info.display_name,
            installed=bool(info.installed and agent_argv(info.name) is not None),
            version=info.version,
            install_command=info.install_command,
        )
        for info in infos
    ]
    return AgentsResponse(
        terminal_available=pty_available(),
        max_terminals=MAX_TERMINALS,
        suggested_names=default_names(MAX_TERMINALS),
        agents=agents,
    )


@router.get("/folders", response_model=FoldersResponse, summary="Browse folders")
async def get_folders(path: str | None = None, include_hidden: bool = False) -> FoldersResponse:
    """Sub-folders of ``path``; without ``path``, this machine's start points."""
    if not path:
        entries = await asyncio.to_thread(start_points)
        return FoldersResponse(
            path=None,
            parent=None,
            entries=[FolderItem(**asdict(e)) for e in entries],
        )

    found, error = await asyncio.to_thread(list_dir, path, include_hidden=include_hidden)
    resolved = Path(path).expanduser()  # noqa: ASYNC240 - no filesystem call
    parent = str(resolved.parent) if resolved.parent != resolved else None
    return FoldersResponse(
        path=str(resolved),
        parent=parent,
        entries=[FolderItem(**asdict(e)) for e in found],
        error=error,
    )


@router.post("/session", summary="Open an Agentic-IDE workspace")
async def start_session(req: StartSessionRequest) -> dict:
    """Open ``folder`` with one named terminal per requested agent."""
    try:
        session = await get_registry().start(
            req.folder, [t.model_dump() for t in req.terminals]
        )
    except SessionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "session": session.to_dict()}


@router.delete(
    "/session",
    summary="Close the Agentic-IDE workspace",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def end_session() -> dict:
    """Close the workspace and stop every agent running in it."""
    closed = await get_registry().end()
    return {"ok": True, "closed": closed}


@router.put("/mode", summary="Toggle focused coding mode")
async def set_mode(req: ModeRequest) -> dict:
    """Turn the focused coding mode on or off.

    While on, Jarvis answers inside the open workspace — it knows the folder,
    the codebase, and what each named terminal is doing. Turning it off returns
    the assistant to its normal, whole-machine behaviour; the terminals keep
    running either way.
    """
    try:
        enabled = get_registry().set_focus_mode(req.enabled)
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "focus_mode": enabled}


@router.get("/terminals/{name}/report", summary="What one terminal is doing")
async def terminal_report(name: str, lines: int = 40) -> dict:
    """Status plus the recent readable output of the terminal called ``name``."""
    try:
        return get_registry().report(name, lines)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/terminals/{name}/prompt", summary="Send a prompt to one terminal")
async def terminal_prompt(name: str, req: PromptRequest) -> dict:
    """Type ``prompt`` into the terminal called ``name`` and press Enter."""
    try:
        term = await get_registry().send_prompt(name, req.prompt)
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "terminal": term.name,
        "agent": AGENT_DISPLAY.get(term.agent, term.agent),
        "sent": term.last_prompt,
        "prompts_sent": term.prompts_sent,
    }


# --------------------------------------------------------------------------- #
# PTY WebSocket                                                               #
# --------------------------------------------------------------------------- #
@router.websocket("/pty/{name}")
async def agentic_pty(ws: WebSocket, name: str) -> None:
    """Bidirectional bridge between one xterm pane and its agent's PTY.

    Wire protocol (JSON both ways) — client: ``{t:"i",d}`` input,
    ``{t:"r",cols,rows}`` resize; server: ``{t:"o",d}`` output, ``{t:"ready"}``,
    ``{t:"exit",code}``, ``{t:"error",message}``.
    """
    await ws.accept()
    if not credentials_valid(ws.scope):
        await ws.close(code=4401, reason="unauthorized")
        return

    qp = ws.query_params
    cols = _safe_int(qp.get("cols"), 80)
    rows = _safe_int(qp.get("rows"), 24)

    registry = get_registry()
    send_lock = asyncio.Lock()

    async def on_output(text: str) -> None:
        async with send_lock:
            try:
                await ws.send_json({"t": "o", "d": text})
            except Exception:  # noqa: BLE001, S110 - viewer gone; transcript keeps filling
                pass

    async def on_exit(code: int) -> None:
        async with send_lock:
            try:
                await ws.send_json({"t": "exit", "code": code})
            except Exception:  # noqa: BLE001, S110 - viewer gone
                pass

    try:
        term = await registry.attach(name, cols, rows, on_output, on_exit)
    except SessionError as exc:
        await ws.send_json({"t": "error", "message": str(exc)})
        await ws.close(code=4404, reason="attach failed")
        return

    await ws.send_json({"t": "ready", "name": term.name, "agent": term.agent})

    try:
        while True:
            try:
                msg = await ws.receive_json()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # AP-20: an unclean teardown raises RuntimeError, not
                # WebSocketDisconnect — any read error is terminal.
                break
            except Exception:  # noqa: BLE001, S112 - malformed frame; keep the PTY alive
                continue
            kind = msg.get("t")
            if kind == "i":
                registry.write(term.key, str(msg.get("d", "")))
            elif kind == "r":
                registry.resize(
                    term.key,
                    _safe_int(msg.get("cols"), cols),
                    _safe_int(msg.get("rows"), rows),
                )
    finally:
        # The viewer closed the pane: stop the agent with it. Leaving an
        # orphaned agent running with nobody watching would burn tokens
        # invisibly, which is worse than having to restart the pane.
        registry.detach(term.key)


def _safe_int(value: object, default: int) -> int:
    try:
        n = int(str(value))
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


__all__ = ["router"]
