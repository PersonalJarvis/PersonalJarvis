"""REST + WebSocket API for the Agentic IDE.

Every action of the feature is an endpoint (the CLI-first contract): the wizard,
the terminal grid, the voice commands and the ``jarvis`` CLI all drive the same
routes, so a pane's behaviour and a spoken command can never drift apart.

Endpoints (prefix ``/api/agentic-ide``):

* ``GET    /state``                      → front workspace + every open one + limits
* ``GET    /agents``                     → Claude Code / Codex install status
* ``GET    /folders``                    → browse folders (no path = start points)
* ``GET    /folders/native``             → can this machine show the OS folder window?
* ``POST   /folders/native``             → open it and return what was picked
* ``GET    /workspaces``                 → every open workspace, in tab order
* ``PUT    /workspaces/active``          → bring one to the front (null = wizard)
* ``PATCH  /workspaces/{workspace_id}``  → rename one workspace tab
* ``DELETE /workspaces/{workspace_id}``  → close that one and stop its agents
* ``POST   /session``                    → open ANOTHER workspace (folder + terminals)
* ``DELETE /session``                    → close the front one and stop every agent
* ``GET    /resume``                     → the last workspace, offered back
* ``POST   /resume``                     → reopen it (same panes, same places)
* ``DELETE /resume``                     → forget it and start fresh
* ``PUT    /mode``                       → focused coding mode on/off
* ``POST   /terminals``                  → open one more pane (split)
* ``POST   /terminals/batch``            → open N more panes at once
* ``POST   /fanout``                     → run ONE task across several agents
  (open the panes, divide the work, brief each one, report who was reached)
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

from jarvis.agentic_ide import native_picker, recap, recents, resume_store
from jarvis.agentic_ide.agent_sessions import has_conversation
from jarvis.agentic_ide.device import device_name
from jarvis.agentic_ide.folders import list_dir, search_folders, start_points
from jarvis.agentic_ide.names import default_names
from jarvis.agentic_ide.session import (
    AGENT_DISPLAY,
    MAX_PROMPT_CHARS,
    MAX_TERMINALS,
    MAX_WORKSPACES,
    SessionError,
    account_home,
    agent_argv,
    get_registry,
    terminals_added_event,
)

from .surface_security import credentials_valid, is_loopback_request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agentic-ide", tags=["agentic-ide"])

# One system folder window at a time — see ``open_native_picker``.
from jarvis.agentic_ide.terminal_input import is_terminal_report_only
_native_picker_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Models                                                                      #
# --------------------------------------------------------------------------- #
#: Reused wherever a caller may pick which subscription a pane runs on.
_ACCOUNT_FIELD_DESCRIPTION = (
    "Which stored subscription of that agent to run on (see /api/agent-accounts). "
    "Defaults to the active account; an unknown id falls back to it."
)


class TerminalRequest(BaseModel):
    agent: str = Field(description="Coding agent to run: 'claude' or 'codex'.")
    name: str | None = Field(
        default=None,
        description="Call-sign for this terminal; auto-assigned when omitted.",
    )
    account: str | None = Field(default=None, description=_ACCOUNT_FIELD_DESCRIPTION)


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
    account: str | None = Field(
        default=None,
        description=(
            "Which stored subscription to run on; defaults to the anchor pane's, "
            "then to the active account."
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
    account: str | None = Field(default=None, description=_ACCOUNT_FIELD_DESCRIPTION)


class CloseTerminalsRequest(BaseModel):
    """The terminal panes one destructive batch action should stop."""

    names: list[str] = Field(
        min_length=1,
        max_length=MAX_TERMINALS,
        description="Call-signs of the terminals to close.",
    )


class ModeRequest(BaseModel):
    enabled: bool = Field(
        description="True narrows Jarvis to this workspace; False returns to normal."
    )


class ActivateWorkspaceRequest(BaseModel):
    """Which workspace should be on screen."""

    id: str | None = Field(
        default=None,
        description=(
            "Workspace to bring to the front. Null means no workspace is shown "
            "— what the UI is in while the wizard opens an additional one. "
            "Nothing is closed either way."
        ),
    )


class RenameWorkspaceRequest(BaseModel):
    """A new label for one workspace tab."""

    name: str = Field(
        min_length=1,
        max_length=80,
        description="New workspace tab name; the folder itself is unchanged.",
    )


class WorkspaceCard(BaseModel):
    """One open workspace, as the workspace bar shows it."""

    id: str
    folder: str
    name: str
    branch: str | None = None
    terminals: int
    live_terminals: int = Field(
        description="Panes whose agent is running right now, not just placed."
    )
    focus_mode: bool
    created_at: float
    last_active_at: float
    active: bool


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceCard]
    active_id: str | None = None
    max_workspaces: int


class SpawnGroupRequest(BaseModel):
    """One "N panes running agent X" part of a fleet request."""

    count: int = Field(
        ge=1,
        le=MAX_TERMINALS,
        description="How many terminals to open in this group.",
    )
    agent: str | None = Field(
        default=None,
        description="Coding agent for this group; defaults to the last pane's.",
    )
    account: str | None = Field(default=None, description=_ACCOUNT_FIELD_DESCRIPTION)


class FanOutRequest(BaseModel):
    """Run ONE task across several coding agents at once."""

    instruction: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_CHARS,
        description="What the fleet should do, in plain words.",
    )
    terminals: list[str] = Field(
        default_factory=list,
        description=(
            "Call-signs of existing terminals to brief. Combine with 'spawn' "
            "or leave empty to brief only the newly opened panes."
        ),
    )
    spawn: list[SpawnGroupRequest] = Field(
        default_factory=list,
        description=(
            "Panes to open before briefing, group by group — this is how a "
            "mixed fleet (five Codex plus three Claude Code) is requested. "
            "Only the newly opened panes are briefed, so agents already "
            "working on something else are not interrupted."
        ),
    )
    split: bool = Field(
        default=False,
        description=(
            "Divide the instruction into one distinct assignment per agent "
            "instead of giving all of them the same brief. Use it when the "
            "agents should cover different areas; leave it off when they "
            "should all do the same thing."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Plan the division of labour and return it WITHOUT typing anything into any agent."
        ),
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


class NativePickerSupport(BaseModel):
    available: bool = Field(
        description="Whether this machine can show the operating system's folder window."
    )
    backend: str | None = Field(
        default=None, description="Which program would draw it (powershell, osascript, zenity, …)."
    )
    reason: str | None = Field(
        default=None, description="Plain-language explanation when it is not available."
    )


class NativePickRequest(BaseModel):
    start: str | None = Field(
        default=None,
        description="Folder the window should open at; ignored when it no longer exists.",
    )


class NativePickResponse(BaseModel):
    path: str | None = Field(default=None, description="The chosen folder, if one was chosen.")
    cancelled: bool = Field(
        default=False, description="True when the user closed the window without choosing."
    )
    error: str | None = Field(default=None, description="Why no folder came back.")


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


class ResumeWorkspace(BaseModel):
    """One workspace being offered back, with the panes it held."""

    session_id: str = ""
    folder: str = ""
    folder_name: str = ""
    name: str = Field(
        default="", description="The label the user gave this tab, if any."
    )
    folder_exists: bool = False
    available: bool = Field(
        description="False when the folder is gone or none of its CLIs are installed."
    )
    resumable_count: int = Field(
        default=0,
        description="How many of its panes bring their conversation back.",
    )
    saved_at: float = Field(
        default=0.0,
        description="When this workspace was last open, which is NOT the file's stamp.",
    )
    in_last_session: bool = Field(
        default=True,
        description=(
            "True when this workspace was open at the last save, so resuming reopens it. "
            "False for a folder that is only remembered from an earlier session."
        ),
    )
    terminals: list[ResumeTerminal] = Field(default_factory=list)


class ResumeOffer(BaseModel):
    """Everything that was open, re-checked against this machine as it is now.

    The counts describe what resuming will actually reopen — the LAST session,
    not every folder the store still remembers. ``workspaces`` lists both, each
    flagged with ``in_last_session``.
    """

    available: bool = Field(
        description="False when there is nothing to reopen, or nothing that could run."
    )
    saved_at: float = 0.0
    workspace_count: int = 0
    terminal_count: int = 0
    resumable_count: int = Field(
        default=0,
        description="How many panes across all workspaces continue their conversation.",
    )
    earlier_count: int = Field(
        default=0,
        description="Remembered folders from earlier sessions, which resuming does NOT reopen.",
    )
    workspaces: list[ResumeWorkspace] = Field(default_factory=list)


class TerminalRecap(BaseModel):
    """What one pane is doing, in the two lengths its header renders."""

    key: str
    name: str
    status: str
    recap: str = Field(description="One clause for the pane header; the pane's width clips it.")
    recap_detail: str = Field(description="The one-or-two-sentence version, shown on hover.")


class RecapsResponse(BaseModel):
    workspace_id: str | None = None
    terminals: list[TerminalRecap] = Field(default_factory=list)


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


@router.get(
    "/folders/native",
    response_model=NativePickerSupport,
    summary="Can this machine show the system folder window?",
)
async def native_picker_support(request: Request) -> NativePickerSupport:
    """Whether ``POST /folders/native`` would actually put a window on a screen.

    Asked before the button is offered, because a button that cannot work is
    worse than no button. Two independent conditions, and the caller is told
    which one failed:

    * the machine has a desktop session and a program that can draw a dialog,
    * the request came from this machine — the window opens where the SERVER
      runs, so from another device it would appear on a screen nobody is
      looking at and quietly wait there.
    """
    if not is_loopback_request(request.scope):
        return NativePickerSupport(
            available=False,
            reason=(
                "The folder window would open on the computer running Jarvis, not on "
                "this one. Browse or paste a path instead."
            ),
        )
    probe = await asyncio.to_thread(native_picker.support)
    return NativePickerSupport(
        available=probe.available, backend=probe.backend, reason=probe.reason
    )


@router.post(
    "/folders/native",
    response_model=NativePickResponse,
    summary="Open the system folder window",
)
async def open_native_picker(request: Request, req: NativePickRequest) -> NativePickResponse:
    """Show the operating system's own folder dialog and return what was picked.

    Cancelling is a normal outcome, not an error — the response says
    ``cancelled`` and the wizard simply keeps whatever was selected before.

    Only one window at a time: the dialog is modal to the person, not to the
    process, so a second request while one is open would stack invisible windows
    on the desktop and leave the user clicking through dialogs they never asked
    for. The second caller is told to finish the first one (409).
    """
    if not is_loopback_request(request.scope):
        raise HTTPException(
            status_code=403,
            detail=(
                "The folder window can only be opened from the computer running Jarvis. "
                "Browse to the folder or paste its path instead."
            ),
        )
    if _native_picker_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="A folder window is already open — finish that one first.",
        )
    async with _native_picker_lock:
        result = await asyncio.to_thread(native_picker.choose_folder, start=req.start)
    return NativePickResponse(path=result.path, cancelled=result.cancelled, error=result.error)


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
        return ResolveResponse(detail=f'No folder called "{wanted}" was found on this machine.')
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


@router.get(
    "/workspaces",
    response_model=WorkspacesResponse,
    summary="Open Agentic-IDE workspaces",
)
async def get_workspaces() -> WorkspacesResponse:
    """Every open workspace, in tab order, with the front one marked.

    Several can be open at once — a workspace is a folder plus its running
    coding agents, and switching between them neither stops nor restarts
    anything. This is the list the workspace bar renders; the full contents of
    the front one come from ``GET /state``.
    """
    registry = get_registry()
    return WorkspacesResponse(
        workspaces=[WorkspaceCard(**card) for card in registry.workspaces()],
        active_id=registry.active_id,
        max_workspaces=MAX_WORKSPACES,
    )


@router.put("/workspaces/active", summary="Switch to another workspace")
async def activate_workspace(req: ActivateWorkspaceRequest) -> dict:
    """Bring one workspace to the front, or clear the front entirely.

    Nothing starts, stops or restarts: the agents in every open workspace keep
    working, and the one that comes forward reconnects its panes to the
    processes that were running all along.

    ``id: null`` means "show no workspace" — the state the UI is in while the
    wizard opens an ADDITIONAL one. It is not a close, and the workspaces stay
    in the bar.

    ``404`` when that workspace is not open (any more).
    """
    try:
        session = await get_registry().activate(req.id)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "active_id": session.id if session else None,
        "state": get_registry().state(),
    }


@router.patch("/workspaces/{workspace_id}", summary="Rename a workspace")
async def rename_workspace(workspace_id: str, req: RenameWorkspaceRequest) -> dict:
    """Change only a workspace's tab label; its folder and agents keep running."""
    registry = get_registry()
    try:
        session = await registry.rename(workspace_id, req.name)
    except SessionError as exc:
        status = 404 if registry.get(workspace_id) is None else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {
        "ok": True,
        "workspace": session.to_card(active=session.id == registry.active_id),
        "state": registry.state(),
    }


@router.post("/session", summary="Open an Agentic-IDE workspace")
async def start_session(req: StartSessionRequest) -> dict:
    """Open ``folder`` as another workspace, with one terminal per request entry.

    Whatever is already open STAYS open with its agents running — this adds a
    workspace and brings it to the front. The same folder may be opened more
    than once; each workspace keeps its own panes, conversations, and tab name.

    The recent-folder history is updated here, at the user-facing open action,
    rather than inside ``Registry.start``. Internal callers (especially unit
    tests using temporary directories) therefore cannot pollute the user's real
    history with folders the user never selected.
    """
    try:
        session = await get_registry().start(req.folder, [t.model_dump() for t in req.terminals])
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
    return {"ok": True, "session": session.to_dict(), "state": get_registry().state()}


@router.delete(
    "/session",
    summary="Close the Agentic-IDE workspace",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def end_session() -> dict:
    """Close the workspace on screen and stop every agent running in it.

    Other open workspaces are untouched; the most recently used of them takes
    the front. Use ``DELETE /workspaces/{workspace_id}`` to close a specific
    one instead of whichever is showing.
    """
    closed = await get_registry().end()
    return {"ok": True, "closed": closed, "state": get_registry().state()}


@router.delete(
    "/workspaces/{workspace_id}",
    summary="Close one Agentic-IDE workspace",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def close_workspace(workspace_id: str) -> dict:
    """Close the workspace with this id and stop every agent inside it.

    The only action that stops an agent on the user's behalf. Switching away
    from a workspace, reloading the page or closing the browser do NOT — an
    agent runs until its workspace is closed, which is what makes the panes
    still be there (still working) when you come back.

    ``404`` when no workspace has that id.
    """
    registry = get_registry()
    if registry.get(workspace_id) is None:
        raise HTTPException(status_code=404, detail="That workspace is not open.")
    await registry.end(workspace_id)
    return {"ok": True, "closed": workspace_id, "state": registry.state()}


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
    """Reopen everything that was open: same panes, same places, same coding CLIs.

    Every workspace in the restore point, not just whichever was on screen —
    somebody who had four folders open had four. Wherever the coding CLI supports
    it, each pane also continues the conversation it was having.

    No agent is started here. The panes connect the way they always do, and that
    connection is what continues them, so a resumed workspace takes exactly the
    same path as a freshly opened one — and restoring five workspaces launches
    nothing until their panes are looked at.

    ``409`` when there is nothing to resume, ``422`` when nothing could be
    reopened at all. A workspace that individually could not come back (folder
    deleted, workspace limit reached) does not fail the request: it is reported
    in ``skipped`` so the caller can say which ones are missing.
    """
    registry = get_registry()
    snapshot = await asyncio.to_thread(resume_store.load)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="There is no previous workspace to reopen.")
    try:
        result = await registry.restore(snapshot)
    except SessionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    for session in result.sessions:
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
    def _count_conversations(panes: list[tuple[str, object, object]]) -> int:
        # The history to ask is the PANE's account, not the machine default:
        # each subscription keeps its conversations in its own directory.
        return sum(1 for agent, handle, home in panes if has_conversation(agent, handle, home))

    resumable = await asyncio.to_thread(
        _count_conversations,
        [
            (t.agent, t.resume, account_home(t.agent, t.account))
            for session in result.sessions
            for t in session.terminals
        ],
    )
    total_panes = result.terminal_count
    return {
        "ok": True,
        "state": registry.state(),
        "workspace_count": len(result.sessions),
        "terminal_count": total_panes,
        # The honest part: the rest of the panes reopen empty, and a caller that
        # reports "everything is back" without checking this is lying.
        "resumable_count": resumable,
        "started_fresh": total_panes - resumable,
        # Workspaces that could not come back, with the reason for each.
        "skipped": [{"folder": folder, "detail": detail} for folder, detail in result.skipped],
    }


@router.post(
    "/terminals/close-batch",
    summary="Close several terminals",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def close_terminals(req: CloseTerminalsRequest) -> dict:
    """Stop selected coding agents in one locked batch and return canonical state."""
    registry = get_registry()
    try:
        closed, failed = await registry.close_terminals(req.names)
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": not failed,
        "closed": [term.name for term in closed],
        "failed": failed,
        "state": registry.state(),
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
            account=req.account,
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
        created, capped = await registry.add_terminals(
            req.count, agent=req.agent, account=req.account
        )
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
                terminals_added_event(session, created, source_layer="agentic_ide_routes")
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


@router.get("/recaps", response_model=RecapsResponse, summary="What every terminal is doing")
async def get_recaps(workspace_id: str | None = None) -> RecapsResponse:
    """A short recap per pane — the header line and its hover tooltip.

    Separate from ``/state`` because the two change at completely different
    rates. The workspace state changes when a pane is opened, closed or moved,
    which is rare; a recap changes whenever an agent prints something, which is
    constantly. Polling ``/state`` fast enough for a live recap would re-send
    every project profile, resume flag and account label several times a minute
    to update one sentence — so the cheap half is its own read.

    Without ``workspace_id`` the workspace on screen answers. An unknown or
    closed one comes back empty rather than as an error: a poll that outlives
    the workspace it was started for is normal, not a failure worth a red pane.
    """
    session = get_registry().get(workspace_id)
    if session is None:
        return RecapsResponse(workspace_id=None, terminals=[])
    rows: list[TerminalRecap] = []
    for term in session.terminals:
        summary = recap.summarize(term)
        rows.append(
            TerminalRecap(
                key=term.key,
                name=term.name,
                status=term.status,
                recap=summary.headline,
                recap_detail=summary.detail,
            )
        )
    return RecapsResponse(workspace_id=session.id, terminals=rows)


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
    # Resolved across every open workspace, not just the front one: call-signs
    # are unique across them, and a file dropped on a pane belongs to THAT
    # pane's folder — which is also where a copied file has to land.
    found = registry.find_terminal(name)
    if found is None:
        if not registry.sessions:
            raise HTTPException(status_code=409, detail="No Agentic-IDE session is running.")
        known = ", ".join(t.name for s in registry.sessions for t in s.terminals) or "none"
        raise HTTPException(
            status_code=404, detail=f"No terminal called {name!r}. Running: {known}."
        )
    session, term = found

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


@router.post("/fanout", summary="Run one task across several coding agents")
async def fanout(request: Request, req: FanOutRequest) -> dict:
    """Brief a fleet: open the panes if asked, divide the work, deliver it.

    The one place a multi-agent run is started from, so voice, CLI and UI cannot
    grow three different ideas of what "split the work between you" means.

    Three independent decisions, each optional:

    * ``spawn`` opens panes first and briefs ONLY those. Panes that were already
      working on something else are not interrupted by a fleet request.
    * ``split`` divides the instruction into one assignment per agent instead of
      handing all of them the same sentence. Without it every agent gets the
      same brief, which is right for "all of you run the tests" and wrong for
      "audit the codebase between you".
    * ``dry_run`` plans and returns the division of labour without typing
      anything, so eight agents can be reviewed before they start.

    ``ok`` is true only when EVERY addressed agent was briefed. A partial
    fan-out is not a success: it is the failure this endpoint's honesty rules
    exist for, so ``undelivered`` names every agent that was not reached and
    why.
    """
    from jarvis.agentic_ide import fanout as fanout_mod
    from jarvis.agentic_ide import work_split

    registry = get_registry()
    if registry.session is None:
        raise HTTPException(status_code=409, detail="No Agentic-IDE session is running.")

    created: list = []
    if req.spawn:
        for group in req.spawn:
            try:
                opened, capped = await registry.add_terminals(
                    group.count, agent=group.agent, account=group.account
                )
            except SessionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            created.extend(opened)
            if capped:
                break
        bus = getattr(request.app.state, "bus", None)
        session = registry.session
        if session is not None and bus is not None and created:
            try:
                await bus.publish(
                    terminals_added_event(session, created, source_layer="agentic_ide_routes")
                )
            except Exception as exc:  # noqa: BLE001 - notification is not the work
                log.debug("AgenticIdeTerminalsAdded publish failed: %s", exc)

    names = list(req.terminals or []) + [t.name for t in created]
    if not names:
        raise HTTPException(
            status_code=400,
            detail=("Name the terminals to brief, or ask for panes to be opened with 'spawn'."),
        )

    session = registry.session
    plan = None
    assignments: dict[str, str] | None = None
    if req.split and len(names) > 1:
        plan = await work_split.split(req.instruction, session=session, count=len(names))
        assignments = {name: item.task for name, item in zip(names, plan.assignments, strict=False)}

    if req.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "terminals": names,
            "opened": [t.to_dict() for t in created],
            "split": _split_payload(plan),
            "delivered": [],
            "undelivered": [],
            "state": registry.state(),
        }

    result = await fanout_mod.deliver(
        session=session,
        terminals=names,
        utterance=req.instruction,
        instruction=req.instruction,
        assignments=assignments,
    )
    return {
        "ok": result.all_delivered,
        "dry_run": False,
        "terminals": names,
        "opened": [t.to_dict() for t in created],
        "split": _split_payload(plan),
        "delivered": [_delivery_payload(d) for d in result.delivered],
        "undelivered": [_delivery_payload(d) for d in result.undelivered],
        "state": registry.state(),
    }


def _split_payload(plan: object | None) -> dict | None:
    """The division of labour, or None when the fleet shares one brief."""
    if plan is None:
        return None
    return {
        "split_by": plan.split_by,  # type: ignore[attr-defined]
        "note": plan.note,  # type: ignore[attr-defined]
        "assignments": [
            {
                "area": a.area,
                "task": a.task,
                "files": list(a.files),
                "done_when": a.done_when,
            }
            for a in plan.assignments  # type: ignore[attr-defined]
        ],
    }


def _delivery_payload(delivery: object) -> dict:
    """One agent's verdict, with the machine-readable failure kind kept."""
    return {
        "terminal": delivery.terminal,  # type: ignore[attr-defined]
        "delivered": delivery.delivered,  # type: ignore[attr-defined]
        "submitted": delivery.submitted,  # type: ignore[attr-defined]
        "files": list(delivery.files),  # type: ignore[attr-defined]
        "composed_by": delivery.composed_by,  # type: ignore[attr-defined]
        "reason_code": delivery.reason_code,  # type: ignore[attr-defined]
        "reason": delivery.reason,  # type: ignore[attr-defined]
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
    if not registry.sessions:
        raise HTTPException(status_code=409, detail="No Agentic-IDE session is running.")

    text = req.prompt
    composed_by = "raw"
    files: list[str] = []
    if req.compose:
        from jarvis.agentic_ide.prompt_composer import compose as compose_prompt

        # The pane decides which workspace composes the prompt. Composition
        # reads the codebase to attach `@file` references, and taking those
        # from the front workspace while sending to a pane in another one would
        # point the agent at files that are not in its tree.
        found = registry.find_terminal(name)
        if found is None:
            known = ", ".join(t.name for s in registry.sessions for t in s.terminals) or "none"
            raise HTTPException(
                status_code=404,
                detail=f"No terminal called {name!r}. Running: {known}.",
            )
        session, term_for_compose = found
        result = await compose_prompt(
            req.prompt,
            session=session,
            terminal_name=term_for_compose.name,
            agent_display=AGENT_DISPLAY.get(term_for_compose.agent, term_for_compose.agent),
        )
        text, composed_by, files = result.text, result.composed_by, result.files
        if not text:
            raise HTTPException(status_code=422, detail="The prompt was empty after composition.")

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
    # `submitted` is the honest part of this answer, and it has THREE states:
    # True the agent accepted the prompt and started, False the text is provably
    # still in its input box, null the pane never visibly took it and no claim
    # can be made either way. Collapsing null into False used to report "the
    # text is sitting in its input box" about a pane where it demonstrably was
    # not — a confident answer nobody had. A caller that reports "sent to Mika"
    # on anything but True is lying to the user.
    return {
        "ok": True,
        "terminal": term.name,
        "agent": AGENT_DISPLAY.get(term.agent, term.agent),
        "sent": term.last_prompt,
        "composed_by": composed_by,
        "files": files,
        "prompts_sent": term.prompts_sent,
        "submitted": term.submitted,
        "detail": (
            ""
            if term.submitted is True
            else (
                f"{term.name} did not accept the prompt — the text is sitting in "
                "its input box. Tell the user, and let them press Enter there."
            )
            if term.submitted is False
            else (
                f"{term.name} never showed the prompt arriving, so it may have "
                "started or may have missed it entirely. Tell the user to check "
                "that pane."
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

    The optional ``workspace`` query parameter names the workspace this pane
    belongs to. It matters because several can be open and the front one changes
    while sockets are alive: without it a keystroke arriving mid-switch would be
    resolved against whichever workspace happens to be showing, and could land
    in a pane in a different folder. Omitted, the front workspace answers (the
    single-workspace case, and what older clients send).
    """
    await ws.accept()
    if not credentials_valid(ws.scope):
        # Logged because from the pane's side a refused handshake and a terminal
        # that will not start look identical — both are a red pane — and until
        # this line the whole failure path wrote nothing anywhere, so a grid of
        # dead panes could not be explained after the fact. Expected once per
        # socket on WebKit engines, which withhold the session cookie from a WS
        # handshake (BUG-065); the client answers by proving the session over
        # plain HTTP and retrying with a one-time ticket.
        log.warning("Agentic IDE: refused an unauthorized terminal socket for %r", name)
        await ws.close(code=4401, reason="unauthorized")
        return

    qp = ws.query_params
    cols = _safe_int(qp.get("cols"), 80)
    rows = _safe_int(qp.get("rows"), 24)
    workspace_id = (qp.get("workspace") or "").strip() or None

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

    # Pin the workspace ONCE, before anything is attached. A client that sent no
    # id means "the one showing right now", and that has to be resolved here
    # rather than on every later message: the front workspace can change while
    # this socket is open, and a keystroke must keep going to the pane it was
    # typed into.
    pinned = registry.get(workspace_id)
    pane_workspace = pinned.id if pinned is not None else None

    try:
        term = await registry.attach(
            name,
            cols,
            rows,
            on_output,
            on_exit,
            workspace_id=pane_workspace,
            appearance=appearance,
        )
    except SessionError as exc:
        # The reason used to travel to the browser and nowhere else, where it
        # ended up as a tooltip on a red badge. A spawn that fails for every
        # pane at once is exactly when nobody is hovering — so it is recorded
        # here as well, with the pane and the workspace it was resolved against.
        log.warning(
            "Agentic IDE: %r could not attach (workspace=%s): %s",
            name,
            pane_workspace or "front",
            exc,
        )
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
            # Or did nothing restart at all? A pane that re-joined an agent that
            # never stopped is a third state, and the most common one once
            # workspaces are switched between — saying "started a new
            # conversation" there would be plainly false.
            "reattached": term.reattached,
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
                data = str(msg.get("d", ""))
                # A browser that answers the terminal's protocol queries itself
                # is answering a question this process already answered, at PTY
                # speed and correctly — so its copy is a duplicate whenever it
                # arrives, and lands in whatever the agent has open by then.
                # Current clients suppress those replies at the source; this
                # holds the line for a stale cached bundle and for the replayed
                # screen, which re-triggers a query from minutes ago. Nobody
                # types these sequences, so no keystroke is at risk.
                if is_terminal_report_only(data):
                    log.debug(
                        "Agentic IDE: dropped a terminal protocol reply echoed by the pane for %s",
                        term.name,
                    )
                else:
                    registry.write(term.key, data, pane_workspace)
            elif kind == "r":
                registry.resize(
                    term.key,
                    _safe_int(msg.get("cols"), cols),
                    _safe_int(msg.get("rows"), rows),
                    pane_workspace,
                )
    finally:
        # The viewer went away — switched tab, reloaded, closed the browser.
        # None of those mean "stop working", so the agent keeps running and
        # only the viewer is released; the next viewer re-joins it. What stops
        # an agent is closing its workspace (DELETE /workspaces/{id}).
        registry.detach(term.key, pane_workspace)


def _safe_int(value: object, default: int) -> int:
    try:
        n = int(str(value))
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Who writes the task briefs                                                   #
# --------------------------------------------------------------------------- #
# Composing a brief is the one Agentic IDE step that bills a per-token API key,
# and most users running coding agents already pay for a subscription that can
# do the same work. This lets them spend that instead — and, for a downloader
# whose ONLY credential is a coding subscription, it is the difference between a
# written brief and the deterministic regex one on every single instruction.


class PromptWriterOption(BaseModel):
    id: str = Field(description="Value to send back to PUT /prompt-writer.")
    label: str = Field(description="Human-readable name for the picker.")
    connected: bool = Field(
        description=(
            "Whether this option can actually write right now. False on a "
            "subscription whose CLI is not signed in on this machine — the "
            "usual reason a plan sits unused while an API key is billed."
        )
    )


class PromptWriterState(BaseModel):
    prompt_writer: str = Field(description="The currently configured choice.")
    options: list[PromptWriterOption] = Field(
        description="Every value this install accepts, with its live state."
    )


class PromptWriterRequest(BaseModel):
    prompt_writer: str = Field(
        description=(
            "'auto', 'subscription', 'api', or a specific brain provider id "
            "from the options list."
        )
    )


def _writer_candidates() -> list[tuple[str, bool]]:
    """The subscription providers this install knows, with their live state.

    Separated from the routes so the tests can pin a candidate set without a
    signed-in CLI on the test machine — and so a failure to probe degrades to an
    empty list rather than a 500 on a settings page.
    """
    try:
        from jarvis.brain import resolver
        from jarvis.core.config import load_config

        config = load_config()
        return [
            (provider, resolver._subscription_connected(provider))
            for provider in resolver._subscription_candidates(config)
        ]
    except Exception:  # noqa: BLE001 - a settings page must still render
        log.info("prompt-writer: candidate probe failed", exc_info=True)
        return []


def _persist_prompt_writer(value: str) -> None:
    """Write the choice to jarvis.toml through the atomic writer (AP-7)."""
    from jarvis.core.config_writer import set_agentic_ide_prompt_writer

    set_agentic_ide_prompt_writer(value)


def _current_prompt_writer() -> str:
    try:
        from jarvis.core.config import load_config

        return str(load_config().agentic_ide.prompt_writer or "auto")
    except Exception:  # noqa: BLE001 - report the default rather than failing
        return "auto"


def _writer_options() -> list[PromptWriterOption]:
    options = [
        PromptWriterOption(
            id="auto",
            label="Automatic (a connected subscription, else the API model)",
            connected=True,
        ),
        PromptWriterOption(
            id="subscription",
            label="Any connected subscription (never an API key)",
            connected=any(connected for _id, connected in _writer_candidates()),
        ),
        PromptWriterOption(
            id="api",
            label="API model (billed per token)",
            connected=True,
        ),
    ]
    for provider, connected in _writer_candidates():
        options.append(
            PromptWriterOption(id=provider, label=provider, connected=connected)
        )
    return options


@router.get(
    "/prompt-writer",
    response_model=PromptWriterState,
    summary="Who writes Agentic-IDE task briefs",
)
async def prompt_writer_state() -> PromptWriterState:
    """Report the configured brief writer and every option, with live state."""
    return PromptWriterState(
        prompt_writer=_current_prompt_writer(), options=_writer_options()
    )


@router.put(
    "/prompt-writer",
    response_model=PromptWriterState,
    summary="Choose who writes Agentic-IDE task briefs",
)
async def set_prompt_writer(payload: PromptWriterRequest) -> PromptWriterState:
    """Persist the brief writer.

    Refuses a provider this install does not offer, and refuses one whose CLI is
    not signed in: pinning either would leave every instruction falling back to
    the deterministic prompt while the UI claimed the subscription was chosen.
    The moment of choosing is the moment the user can still fix it.
    """
    requested = (payload.prompt_writer or "").strip()
    options = _writer_options()
    match = next((option for option in options if option.id == requested), None)
    if match is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown prompt writer '{requested}'. Pick one of: "
                + ", ".join(option.id for option in options)
            ),
        )
    if not match.connected:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{requested}' is not signed in on this machine. Connect it "
                "first, or choose 'auto'."
            ),
        )
    _persist_prompt_writer(requested)
    return PromptWriterState(prompt_writer=requested, options=_writer_options())


__all__ = ["router"]

    The optional ``appearance`` parameter (``light`` / ``dark``) is the ground
    this pane is drawn on. It is what the agent's CLI is told when it asks the
    terminal for its colours — a question answered in the backend rather than
    by xterm, so the reply cannot arrive after the CLI stopped waiting for it
    (see ``jarvis.agentic_ide.terminal_input``).
    appearance = (qp.get("appearance") or "").strip().lower() or None
