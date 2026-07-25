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

from jarvis.agentic_ide import recents
from jarvis.agentic_ide.device import device_name
from jarvis.agentic_ide.folders import list_dir, search_folders, start_points
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


class AddTerminalRequest(BaseModel):
    agent: str | None = Field(
        default=None,
        description="Coding agent to run; defaults to the anchor terminal's.",
    )
    name: str | None = Field(
        default=None,
        description="Call-sign for the new terminal; auto-assigned when omitted.",
    )
    anchor: str | None = Field(
        default=None,
        description="Call-sign of the terminal to split; defaults to the last one.",
    )
    direction: str = Field(
        default="right",
        description="'right' places it beside the anchor, 'down' below it.",
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
    compose: bool = Field(
        default=False,
        description=(
            "Rewrite the text into a briefed prompt for the coding agent and "
            "attach @file references from this workspace before sending. Meant "
            "for spoken/rough instructions; the typed prompt bar sends as-is."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description="Compose and return the prompt WITHOUT sending it.",
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
    # Human-facing name of this machine ("Ruben's MacBook"), so the picker can
    # label the start points with the device rather than the account folder
    # ("Administrator" tells the user nothing about which computer this is).
    device_name: str | None = None


class SearchResponse(BaseModel):
    query: str
    entries: list[FolderItem]
    truncated: bool = False


class RecentItem(BaseModel):
    path: str
    name: str
    terminals: int
    agents: dict[str, int]
    last_used: float
    exists: bool = True


class RecentsResponse(BaseModel):
    device_name: str
    recents: list[RecentItem]


class ResolveRequest(BaseModel):
    path: str | None = Field(
        default=None,
        description="A full path from a drop (folder, file, or file:// URI).",
    )
    name: str | None = Field(
        default=None,
        description="Folder name only — used when the drop carried no path.",
    )


class ResolveResponse(BaseModel):
    resolved: str | None = None
    candidates: list[FolderItem] = Field(default_factory=list)
    detail: str = ""


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
            device_name=device_name(),
        )

    found, error = await asyncio.to_thread(list_dir, path, include_hidden=include_hidden)
    resolved = Path(path).expanduser()  # noqa: ASYNC240 - no filesystem call
    parent = str(resolved.parent) if resolved.parent != resolved else None
    return FoldersResponse(
        path=str(resolved),
        parent=parent,
        entries=[FolderItem(**asdict(e)) for e in found],
        error=error,
        device_name=device_name(),
    )


@router.get("/folders/search", response_model=SearchResponse, summary="Search folders by name")
async def search(q: str, limit: int = 40) -> SearchResponse:
    """Find folders by name across the home and conventional code directories.

    Bounded by depth and a visit budget, so this stays a fast interactive search
    rather than a full-disk crawl (see ``folders.search_folders``).
    """
    capped = max(1, min(limit, 100))
    hits = await asyncio.to_thread(search_folders, q, limit=capped)
    return SearchResponse(
        query=q,
        entries=[FolderItem(**asdict(e)) for e in hits],
        truncated=len(hits) >= capped,
    )


@router.get("/recents", response_model=RecentsResponse, summary="Recently opened workspaces")
async def get_recents() -> RecentsResponse:
    """Workspaces opened before, newest first, with their previous layout."""
    entries = await asyncio.to_thread(recents.load)
    return RecentsResponse(
        device_name=device_name(),
        recents=[
            RecentItem(
                path=r.path,
                name=r.name,
                terminals=r.terminals,
                agents=r.agents,
                last_used=r.last_used,
                exists=True,
            )
            for r in entries
        ],
    )


@router.delete("/recents", summary="Forget a recent workspace")
async def delete_recent(path: str) -> dict:
    """Remove one entry from the recents list (the folder itself is untouched)."""
    removed = await asyncio.to_thread(recents.forget, path)
    return {"ok": True, "removed": removed}


@router.post("/folders/resolve", response_model=ResolveResponse, summary="Resolve a dropped folder")
async def resolve_folder(req: ResolveRequest) -> ResolveResponse:
    """Turn a drag-and-drop payload into a usable folder path.

    Browsers do not hand a web page the real path of a dropped folder, so the
    frontend sends whatever it managed to extract and this route does the rest:

    * a ``file://`` URI or a plain path is unwrapped and used directly,
    * a path pointing at a FILE resolves to the folder containing it (dropping a
      file from inside a project is a normal way to mean "that project"),
    * with only a folder NAME available, the name is searched for — one hit is
      taken, several are returned for the user to pick from.
    """
    raw = (req.path or "").strip()
    if raw:
        candidate = _unwrap_file_uri(raw)
        try:
            # expanduser() is string/env work; the stats below run in threads.
            resolved_path = Path(candidate).expanduser()  # noqa: ASYNC240
            if await asyncio.to_thread(resolved_path.is_dir):
                return ResolveResponse(resolved=str(resolved_path))
            if await asyncio.to_thread(resolved_path.is_file):
                return ResolveResponse(
                    resolved=str(resolved_path.parent),
                    detail=f"Used the folder containing {resolved_path.name}.",
                )
        except OSError as exc:
            return ResolveResponse(detail=f"Could not read that path: {exc}")

    wanted = (req.name or "").strip()
    if not wanted:
        return ResolveResponse(
            detail="That drop carried no folder path — browse to the folder or paste its path."
        )

    hits = await asyncio.to_thread(search_folders, wanted, limit=12)
    items = [FolderItem(**asdict(e)) for e in hits]
    if len(items) == 1:
        return ResolveResponse(resolved=items[0].path, candidates=items)
    if not items:
        return ResolveResponse(
            detail=f'No folder called "{wanted}" was found on this machine.'
        )
    return ResolveResponse(
        candidates=items,
        detail=f'Several folders are called "{wanted}" — pick the right one.',
    )


def _unwrap_file_uri(value: str) -> str:
    """``file:///C:/x`` / ``file:///home/x`` -> a native path; anything else as-is."""
    if not value.lower().startswith("file:"):
        return value
    from urllib.parse import unquote, urlparse

    parsed = urlparse(value)
    path = unquote(parsed.path)
    # Windows URIs carry a leading slash before the drive letter.
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


@router.post("/session", summary="Open an Agentic-IDE workspace")
async def start_session(req: StartSessionRequest) -> dict:
    """Open ``folder`` with one named terminal per requested agent.

    The recent-folder history is updated here, at the user-facing open action,
    rather than inside ``Registry.start``. Internal callers (especially unit
    tests using temporary directories) therefore cannot pollute the user's real
    history with folders the user never selected.
    """
    try:
        session = await get_registry().start(
            req.folder, [t.model_dump() for t in req.terminals]
        )
    except SessionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    split: dict[str, int] = {}
    for terminal in session.terminals:
        split[terminal.agent] = split.get(terminal.agent, 0) + 1
    try:
        await asyncio.to_thread(
            recents.remember,
            session.folder,
            terminals=len(session.terminals),
            agents=split,
        )
    except Exception:  # noqa: BLE001 - history must never block opening a folder
        log.warning("Agentic IDE recent-folder history was not updated", exc_info=True)
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


@router.post("/terminals", summary="Open one more terminal")
async def add_terminal(req: AddTerminalRequest) -> dict:
    """Add a terminal to the running workspace, beside or below another one.

    ``direction="right"`` puts it in the same grid row as ``anchor`` (side by
    side); ``"down"`` opens a new row underneath. The agent defaults to the
    anchor's, since splitting a pane usually means "another one of these".
    """
    try:
        term = await get_registry().add_terminal(
            agent=req.agent,
            name=req.name,
            anchor=req.anchor,
            direction=req.direction,
        )
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "terminal": term.to_dict(), "state": get_registry().state()}


@router.delete(
    "/terminals/{name}",
    summary="Close one terminal",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def close_terminal(name: str) -> dict:
    """Stop the agent in the terminal called ``name`` and remove its pane."""
    try:
        term = await get_registry().close_terminal(name)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "closed": term.name, "state": get_registry().state()}


@router.get("/terminals/{name}/report", summary="What one terminal is doing")
async def terminal_report(name: str, lines: int = 40) -> dict:
    """Status plus the recent readable output of the terminal called ``name``."""
    try:
        return get_registry().report(name, lines)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/terminals/{name}/prompt", summary="Send a prompt to one terminal")
async def terminal_prompt(name: str, req: PromptRequest) -> dict:
    """Type ``prompt`` into the terminal called ``name`` and press Enter.

    With ``compose=true`` the text is first rewritten into a prompt worth
    running — speech artefacts removed, the task stated as an imperative, and
    the relevant files of this workspace attached as ``@path`` references. That
    is the path a spoken instruction takes; the UI's prompt bar sends verbatim,
    because someone who typed it already wrote what they meant.

    ``dry_run=true`` returns the composed prompt without sending it, so a caller
    can show it for approval first.
    """
    registry = get_registry()
    session = registry.session
    if session is None:
        raise HTTPException(
            status_code=409, detail="No Agentic-IDE session is running."
        )

    text = req.prompt
    composed_by = "raw"
    files: list[str] = []
    if req.compose:
        from jarvis.agentic_ide.prompt_composer import compose as compose_prompt

        term_for_compose = session.find(name)
        if term_for_compose is None:
            known = ", ".join(t.name for t in session.terminals) or "none"
            raise HTTPException(
                status_code=404,
                detail=f"No terminal called {name!r}. Running: {known}.",
            )
        result = await compose_prompt(
            req.prompt,
            session=session,
            terminal_name=term_for_compose.name,
            agent_display=AGENT_DISPLAY.get(
                term_for_compose.agent, term_for_compose.agent
            ),
        )
        text, composed_by, files = result.text, result.composed_by, result.files
        if not text:
            raise HTTPException(
                status_code=422, detail="The prompt was empty after composition."
            )

    if req.dry_run:
        return {
            "ok": True,
            "terminal": name,
            "sent": "",
            "composed": text,
            "composed_by": composed_by,
            "files": files,
            "dry_run": True,
        }

    try:
        term = await registry.send_prompt(name, text)
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # `submitted` is the honest part of this answer: the text was typed either
    # way, but only a True here means the agent actually accepted it and started.
    # A caller that reports "sent to Mika" on a False is lying to the user.
    return {
        "ok": True,
        "terminal": term.name,
        "agent": AGENT_DISPLAY.get(term.agent, term.agent),
        "sent": term.last_prompt,
        "composed_by": composed_by,
        "files": files,
        "prompts_sent": term.prompts_sent,
        "submitted": bool(term.submitted),
        "detail": (
            ""
            if term.submitted
            else (
                f"{term.name} did not accept the prompt — the text is sitting in "
                "its input box. Tell the user, and let them press Enter there."
            )
        ),
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
