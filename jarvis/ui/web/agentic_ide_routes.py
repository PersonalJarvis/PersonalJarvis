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
* ``GET    /resume``                     → the last workspace, offered back
* ``POST   /resume``                     → reopen it (same panes, same places)
* ``DELETE /resume``                     → forget it and start fresh
* ``PUT    /mode``                       → focused coding mode on/off
* ``POST   /terminals``                  → open one more pane (split)
* ``POST   /terminals/batch``            → open N more panes at once
* ``GET    /terminals/{name}/report``    → what one named terminal is doing
* ``POST   /terminals/{name}/prompt``    → type a prompt into it and press Enter
* ``POST   /terminals/{name}/attach``    → drop/paste files onto a pane
* ``WS     /pty/{name}``                 → the pane's live terminal stream

``{name}`` accepts the call-sign ("mika", "Mika") or a spoken phrase containing
it, so a voice command reaches the right pane without exact spelling.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from jarvis.agentic_ide import recents, resume_store
from jarvis.agentic_ide.agent_sessions import has_conversation
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
    terminals_added_event,
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
        description=(
            "'right' opens a new column beside the anchor, 'down' splits the "
            "anchor's own column and stacks the new pane under it."
        ),
    )


class AddTerminalsRequest(BaseModel):
    """Open several panes at once (the spoken "five more terminals")."""

    count: int = Field(
        default=1,
        ge=1,
        le=MAX_TERMINALS,
        description="How many terminals to open, capped by the workspace maximum.",
    )
    agent: str | None = Field(
        default=None,
        description="Coding agent to run in all of them; defaults to the last pane's.",
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


class ResumeTerminal(BaseModel):
    """One pane of the workspace being offered back."""

    key: str
    name: str
    agent: str
    display_name: str
    column: int
    slot: int
    available: bool = Field(
        description=(
            "Can this pane be opened at all? False when its coding CLI is no "
            "longer installed on this machine."
        )
    )
    resumable: bool = Field(
        description=(
            "Does its CONVERSATION come back, or only its call-sign? False "
            "means the pane reopens empty — say so before anyone clicks."
        )
    )
    prompts_sent: int = 0


class ResumeOffer(BaseModel):
    """The last workspace, re-checked against this machine as it is now."""

    available: bool = Field(
        description="False when there is nothing to reopen, or nothing that could run."
    )
    folder: str = ""
    folder_name: str = ""
    folder_exists: bool = False
    saved_at: float = 0.0
    session_id: str = ""
    resumable_count: int = Field(
        default=0,
        description="How many panes bring their conversation back with them.",
    )
    terminals: list[ResumeTerminal] = Field(default_factory=list)


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


async def _installed_agents() -> set[str]:
    """Coding CLIs this machine can actually launch, right now.

    The same two-step check ``GET /agents`` makes: detected AND resolvable the
    way the terminal will resolve it, because a GUI process starts with a
    minimal PATH and "installed" is not the same question as "launchable from
    here". A probe that fails answers "none installed" rather than raising —
    an offer screen must never 500.
    """
    try:
        from jarvis.workspace.agents import detect_agents

        return {
            info.name
            for info in await detect_agents()
            if info.installed and agent_argv(info.name) is not None
        }
    except Exception:  # noqa: BLE001 - the offer is more useful than an error
        log.warning("Agentic IDE: could not detect coding agents", exc_info=True)
        return set()


@router.get(
    "/resume",
    response_model=ResumeOffer,
    summary="The last Agentic-IDE workspace, offered back",
)
async def get_resume_offer() -> ResumeOffer:
    """What reopening the last workspace would bring back.

    Answered against the machine as it is NOW, not as it was when the workspace
    was saved: the folder may have been deleted, a coding CLI uninstalled, a
    pane may never have been given a prompt. Each pane therefore reports two
    different things — whether it can be opened at all, and whether its
    conversation comes back with it. Both belong on screen before the user
    clicks, because the alternative is finding out by asking a resumed agent a
    follow-up question and getting a blank stare.

    ``available: false`` is a normal answer, not an error: a fresh install has
    nothing to resume.
    """
    snapshot = await asyncio.to_thread(resume_store.load)
    installed = await _installed_agents()
    return ResumeOffer(**resume_store.offer(snapshot, installed=installed))


@router.post("/resume", summary="Reopen the last Agentic-IDE workspace")
async def resume_workspace() -> dict:
    """Reopen the last workspace: same panes, same places, same coding CLIs.

    Wherever the coding CLI supports it, each pane also continues the
    conversation it was having. No agent is started here — the panes connect the
    way they always do, and that connection is what continues them, so a resumed
    workspace takes exactly the same path as a freshly opened one.

    ``409`` when there is nothing to resume, ``422`` when the folder is gone.
    """
    registry = get_registry()
    snapshot = await asyncio.to_thread(resume_store.load)
    if snapshot is None:
        raise HTTPException(
            status_code=409, detail="There is no previous workspace to reopen."
        )
    try:
        session = await registry.restore(snapshot)
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
    except Exception:  # noqa: BLE001 - history must never block reopening
        log.warning("Agentic IDE recent-folder history was not updated", exc_info=True)

    # Counted by asking the coding CLI's history, not by counting handles a pane
    # happens to hold: a pane that was opened and never used holds an id that
    # points at nothing, and reporting it as continued would be the same lie the
    # offer screen exists to prevent. One thread hop for the whole list, since
    # each check is a filename lookup.
    def _count_conversations(panes: list[tuple[str, object]]) -> int:
        return sum(1 for agent, handle in panes if has_conversation(agent, handle))

    resumable = await asyncio.to_thread(
        _count_conversations, [(t.agent, t.resume) for t in session.terminals]
    )
    return {
        "ok": True,
        "session": session.to_dict(),
        # The honest part: the rest of the panes reopen empty, and a caller that
        # reports "everything is back" without checking this is lying.
        "resumable_count": resumable,
        "started_fresh": len(session.terminals) - resumable,
    }


@router.delete(
    "/resume",
    summary="Forget the last Agentic-IDE workspace",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def forget_resume_offer() -> dict:
    """Discard the restore point, so the IDE opens to a clean wizard.

    Nothing on disk is touched and no agent is stopped — this only throws away
    the note saying which workspace could be reopened. It cannot be undone: the
    call-signs, the grid positions and the links to each pane's conversation are
    gone with it.
    """
    removed = await asyncio.to_thread(resume_store.clear)
    return {"ok": True, "removed": removed}


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

    ``direction="right"`` opens a new column beside ``anchor``; ``"down"``
    splits the anchor's own column and stacks the new pane underneath it,
    leaving every other column untouched. The agent defaults to the anchor's,
    since splitting a pane usually means "another one of these" — pass ``agent``
    to run a different coding CLI in the new pane.
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


@router.post("/terminals/batch", summary="Open several terminals at once")
async def add_terminals(request: Request, req: AddTerminalsRequest) -> dict:
    """Open ``count`` more terminals in the running workspace.

    The batch behind a spoken "open five more Claude Code terminals", and the
    same call the CLI makes. Placement, call-signs and the agent default are the
    single-terminal endpoint's — this only repeats it and reports honestly when
    the workspace cap cut the request short.

    ``capped`` is true when fewer panes were opened than asked for. A client MUST
    surface that: five requested with three opened is not a plain success.
    """
    registry = get_registry()
    try:
        created, capped = await registry.add_terminals(req.count, agent=req.agent)
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Tell every connected client, so a workspace view that is already open shows
    # the new panes instead of a stale grid. Best-effort: the panes exist whether
    # or not a bus is attached (it is not, in tests).
    session = registry.session
    bus = getattr(request.app.state, "bus", None)
    if session is not None and bus is not None and created:
        try:
            await bus.publish(
                terminals_added_event(
                    session, created, source_layer="agentic_ide_routes"
                )
            )
        except Exception as exc:  # noqa: BLE001 - notification is not the work
            log.debug("AgenticIdeTerminalsAdded publish failed: %s", exc)

    return {
        "ok": True,
        "requested": req.count,
        "capped": capped,
        "terminals": [t.to_dict() for t in created],
        "state": registry.state(),
    }


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


@router.post("/terminals/{name}/attach", summary="Drop or paste files onto a terminal")
async def terminal_attach(
    name: str,
    files: list[UploadFile] | None = File(default=None),  # noqa: B008
    paths: str | None = Form(default=None),  # noqa: B008
    submit: bool = Form(default=False),  # noqa: B008
    note: str | None = Form(default=None),  # noqa: B008
) -> dict:
    """Put dropped or pasted files in front of the agent in terminal ``name``.

    Two inputs, either or both:

    * ``paths`` — newline-separated real paths the browser DID manage to hand
      over (an Explorer/Finder drag usually carries them). Nothing is copied;
      one inside the workspace is referenced where it lies, one outside is
      copied in, because an agent's access to unrelated parts of the disk is
      neither guaranteed nor silent.
    * ``files`` — raw bytes, for everything with no path at all: a screenshot
      pasted from the clipboard, an image dragged off a web page.

    The resulting references are TYPED into the pane, not submitted, so the user
    can add a sentence before pressing Enter — which is what someone dropping a
    screenshot almost always wants to do. ``submit=true`` sends it as-is.
    """
    from jarvis.agentic_ide import drops

    registry = get_registry()
    session = registry.session
    if session is None:
        raise HTTPException(
            status_code=409, detail="No Agentic-IDE session is running."
        )
    term = session.find(name)
    if term is None:
        known = ", ".join(t.name for t in session.terminals) or "none"
        raise HTTPException(
            status_code=404, detail=f"No terminal called {name!r}. Running: {known}."
        )

    references: list[str] = []
    stored_names: list[str] = []
    copied = 0

    # 1. Paths that came with the drag. A path already inside the workspace is
    #    used as it lies; anything else is copied in.
    to_copy: list[tuple[str, bytes]] = []
    for raw in (paths or "").splitlines():
        candidate = raw.strip()
        if not candidate:
            continue
        inside = drops.within_workspace(candidate, session.folder)
        if inside is not None:
            references.append(drops.reference(inside, agent=term.agent))
            stored_names.append(Path(inside).name)
            continue
        # expanduser() is string/env work, not a filesystem call; the read
        # itself goes to a worker thread (a dropped file may live on a slow
        # network share).
        resolved = Path(candidate).expanduser()  # noqa: ASYNC240
        try:
            data = await asyncio.to_thread(resolved.read_bytes)
        except OSError as exc:
            log.info("Agentic IDE attach: unreadable dropped path %r (%s)", candidate, exc)
            continue
        to_copy.append((resolved.name, data))

    # 2. Bytes the browser handed over directly.
    total = 0
    for upload in files or []:
        data = await upload.read()
        total += len(data)
        if total > drops.MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"That drop is too large (max "
                    f"{drops.MAX_TOTAL_BYTES // (1024 * 1024)} MB in total)."
                ),
            )
        if data:
            to_copy.append((upload.filename or "file", data))

    if to_copy:
        try:
            stored = await asyncio.to_thread(drops.store, session.folder, to_copy)
        except drops.DropError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        copied = len(stored)
        for item in stored:
            references.append(drops.reference(item.relative_path, agent=term.agent))
            stored_names.append(item.name)

    if not references:
        raise HTTPException(
            status_code=422,
            detail="That drop carried nothing this pane could use.",
        )

    payload = " ".join(references)
    if note and note.strip():
        payload = f"{payload} {note.strip()}"

    try:
        if submit:
            await registry.send_prompt(term.name, payload)
        else:
            # Typed, not submitted: the user still wants to say what to DO with
            # the file. A leading space keeps it off whatever they already typed.
            if not registry.write(term.key, f"{payload} "):
                raise HTTPException(
                    status_code=409,
                    detail=f"{term.name} is not accepting input right now.",
                )
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "ok": True,
        "terminal": term.name,
        "references": references,
        "files": stored_names,
        "copied": copied,
        "submitted": bool(submit),
    }


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

    await ws.send_json(
        {
            "t": "ready",
            "name": term.name,
            "agent": term.agent,
            # Did this pane pick up its previous conversation, or start empty?
            # The pane looks identical either way, so it has to be told.
            "resumed": term.resumed,
        }
    )

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
