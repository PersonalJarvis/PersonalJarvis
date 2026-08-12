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
* ``POST   /terminal-target/open``       → open a path printed by one terminal
* ``GET    /workspaces``                 → every open workspace, in tab order
* ``GET    /workspaces/{id}/files``      → browse that workspace's file tree
* ``GET    /workspaces/{id}/file``       → stream one workspace file in-app
* ``GET    /workspaces/{id}/file-preview`` → readable text or a binary preview
* ``PUT    /workspaces/active``          → bring one to the front (null = wizard)
* ``PATCH  /workspaces/{workspace_id}``  → rename one workspace tab
* ``DELETE /workspaces/{workspace_id}``  → close that one and stop its agents
* ``POST   /session``                    → open ANOTHER workspace (folder + terminals)
* ``DELETE /session``                    → close the front one and stop every agent
* ``GET    /resume``                     → the last workspace, offered back
* ``POST   /resume``                     → reopen it (same panes, same places)
* ``DELETE /resume``                     → forget it and start fresh
* ``GET    /interrupted``                → panes that came back and were never restarted
* ``POST   /interrupted/continue``       → tell them to carry on ("continue")
* ``PUT    /mode``                       → focused coding mode on/off
* ``GET    /ui-preferences``             → remembered terminal text size
* ``PUT    /ui-preferences``             → remember another one (survives restarts)
* ``GET    /accounts``                   → which subscription new terminals use
* ``PUT    /accounts/active``            → switch it (open panes keep theirs)
* ``POST   /terminals``                  → open one more pane (split)
* ``POST   /terminals/batch``            → open N more panes at once
* ``DELETE /terminals/agent/{agent}``    → close every pane of one coding CLI
* ``POST   /fanout``                     → run ONE task across several agents
  (open the panes, divide the work, brief each one, report who was reached)
* ``GET    /terminals/{name}/report``    → what one named terminal is doing
* ``PATCH  /terminals/{terminal}``       → give that pane another call-sign
* ``GET    /terminals/{name}/prompts``   → every prompt handed to that pane
* ``POST   /terminals/{name}/prompt``    → type a prompt into it and press Enter
* ``POST   /terminals/{name}/attach``    → drop/paste files onto a pane
* ``GET    /voice-attachments``          → every pending orb-drop receipt
* ``GET    /terminals/{name}/voice-attachments`` → pending orb-drop receipts
* ``DELETE /terminals/{name}/voice-attachments/{batch_id}`` → cancel one drop
* ``WS     /pty/{name}``                 → the pane's live terminal stream

``{name}`` accepts the call-sign ("t2", "T2") or a spoken phrase containing it
in any of the ways a position is said ("terminal two", "the second terminal"),
so a voice command reaches the right pane without exact spelling.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal, get_args

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
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from jarvis.agentic_ide import (
    agent_transcript,
    drop_analysis,
    drops,
    interrupted,
    native_picker,
    notifications,
    prompt_attachments,
    prompt_history,
    recap_engine,
    recents,
    resume_store,
)
from jarvis.agentic_ide.activity import has_work_behind_it
from jarvis.agentic_ide.agent_sessions import has_conversation
from jarvis.agentic_ide.device import device_name
from jarvis.agentic_ide.fleet_actions import (
    close_agent_terminals,
    terminals_closed_event,
    wait_for_prompt_ready,
)
from jarvis.agentic_ide.folders import (
    list_dir,
    list_workspace_dir,
    search_folders,
    start_points,
)
from jarvis.agentic_ide.names import default_names
from jarvis.agentic_ide.session import (
    AGENT_DISPLAY,
    MAX_PROMPT_CHARS,
    MAX_TERMINAL_NAME,
    MAX_TERMINALS,
    MAX_WORKSPACES,
    PendingPromptAttachmentBatch,
    Session,
    SessionError,
    SessionNotReady,
    Terminal,
    account_home,
    agent_argv,
    coding_mode_event,
    get_registry,
    prompt_sent_event,
    sanitize_prompt,
    terminals_added_event,
    workspace_changed_event,
)
from jarvis.agentic_ide.terminal_input import is_terminal_report_only
from jarvis.agentic_ide.workspace_view import (
    WORKSPACE_VIEWS,
    view_from_legacy_chat_flag,
)

from .surface_security import credentials_valid, is_loopback_request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agentic-ide", tags=["agentic-ide"])

#: How long a fan-out waits for panes it just opened to start their agents.
#: Shorter than the shared fleet budget on purpose: this one is spent inside an
#: HTTP request, and the registry-command tool that a voice turn goes through
#: gives up at 30 s — a wait that outlives its own caller reports nothing to
#: anybody. Panes still coming up after this are reported as undelivered rather
#: than waited for indefinitely.
SPAWN_READY_TIMEOUT_S = 20.0


async def _announce_coding_mode(request: Request) -> None:
    """Tell every connected client what the coding mode is NOW.

    Called from each route that can change the answer — the explicit toggle, and
    the three workspace transitions that change it as a side effect (closing the
    front workspace, closing a specific one, switching to another). The mode is
    read back from the registry rather than passed in, so a caller cannot
    announce a mode that did not happen.

    Best-effort by design: the mode is already set, and no client notification is
    worth failing a request over (AP-18). A missed event costs a stale badge
    until the next navigation, never a wrong workspace state.
    """
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return
    try:
        await bus.publish(
            coding_mode_event(get_registry().session, source_layer="agentic_ide_routes")
        )
    except Exception as exc:  # noqa: BLE001 - notification is not the work
        log.debug("AgenticIdeCodingModeChanged publish failed: %s", exc)


async def _announce_workspace(request: Request, session: object | None, reason: str) -> None:
    """Tell every connected client that a workspace opened, moved or closed.

    The companion to ``_announce_coding_mode``, and it exists for a failure that
    one could not cover: the mode event drives a badge, while THIS drives the
    grid. A workspace opened by voice, restored after a restart, or closed in
    another window changed nothing on a screen that was not the one doing it —
    the panes on it went on knocking at a workspace that had been replaced, and
    the only way back was a reload.

    Same contract as its neighbour: best-effort (AP-18), read back from the
    registry, a trigger rather than a source of truth.
    """
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return
    try:
        await bus.publish(
            workspace_changed_event(
                session,  # type: ignore[arg-type]
                reason,
                source_layer="agentic_ide_routes",
            )
        )
    except Exception as exc:  # noqa: BLE001 - notification is not the work
        log.debug("AgenticIdeWorkspaceChanged publish failed: %s", exc)


# One system folder window at a time — see ``open_native_picker``.
_native_picker_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Models                                                                      #
# --------------------------------------------------------------------------- #
#: Bounds on the analysed drops one prompt may carry back. Both mirror the
#: analysis module's own caps rather than restating numbers, so a change there
#: cannot leave this schema rejecting payloads the analyser legitimately
#: produces.
DROP_DETAIL_MAX_CHARS = drop_analysis.MAX_TEXT_CHARS
MAX_PROMPT_ATTACHMENTS = drops.MAX_FILES

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


class MoveTerminalRequest(BaseModel):
    """Where an existing pane should be drawn from now on."""

    target: str = Field(
        min_length=1,
        description="Call-sign of the pane it was dropped on, e.g. 'Mika'.",
    )
    position: str = Field(
        default="swap",
        description=(
            "'swap' exchanges the two panes and keeps the workspace's shape; "
            "'left'/'right'/'above'/'below' put the moved pane on that side "
            "OF THE TARGET, splitting the target's own rectangle in half."
        ),
    )


class RefoldRequest(BaseModel):
    """How deep the workspace's columns should be from now on."""

    depth: int = Field(
        ge=1,
        le=MAX_TERMINALS,
        description=(
            "Panes stacked per column. 1 is the single row a workspace opens "
            "in; 2 folds it into two rows, which is what a row too narrow for "
            "the agents' own interface needs."
        ),
    )


class LayoutWeightsRequest(BaseModel):
    """The workspace tree as the dragging client saw it, weights included."""

    layout: dict = Field(
        description=(
            "The whole split tree in its wire form (the session state's "
            "'layout' field), carrying the dragged weights. Only the weights "
            "are adopted; the structure stays the server's."
        ),
    )


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


#: Layer 3 of the workspace-view enum (``docs/anti-drift-three-layer.md``).
#: Pydantic cannot take a tuple in ``Literal[...]``, so the values are spelled
#: again here and the assertion below makes that duplication safe: any drift
#: from layer 0 fails at import, which means pytest collection fails, rather
#: than producing an HTTP 422 for one specific body weeks later.
WorkspaceViewName = Literal["grid", "chat"]
if set(get_args(WorkspaceViewName)) != set(WORKSPACE_VIEWS):  # pragma: no cover - guard
    raise RuntimeError(
        "WorkspaceViewName drifted from WORKSPACE_VIEWS — update both "
        "(jarvis/agentic_ide/workspace_view.py is the source of truth)."
    )


class SurfaceContextRequest(BaseModel):
    """Which terminal the Agentic-IDE UI shows and targets for prompts."""

    workspace_id: str = Field(description="Workspace currently rendered by this view.")
    view: WorkspaceViewName | None = Field(
        default=None,
        description="Which reading mode is on screen: grid or chat.",
    )
    chat_view: bool | None = Field(
        default=None,
        description=(
            "Deprecated — superseded by `view`. Still read so a desktop WebView "
            "still holding an older bundle keeps reporting its surface "
            "correctly for the seconds before it reloads itself."
        ),
    )
    on_screen: bool = Field(description="Whether the Agentic-IDE section is visible.")
    terminal: str | None = Field(
        default=None,
        description="Visible pane call-sign; null when no single pane is shown.",
    )
    prompt_target: str | None = Field(
        default=None,
        description=(
            "Pane selected by the prompt bar and voice orb; null when no pane "
            "can accept an instruction."
        ),
    )


class TerminalTextSizeRequest(BaseModel):
    """How large the text in the workspace's terminals should be, in pixels."""

    terminal_font_size: int = Field(
        description=(
            "Terminal text size in pixels. Values outside the supported range "
            "are clamped to the nearest usable one rather than rejected; the "
            "response says what was actually stored."
        )
    )


class ActiveAccountRequest(BaseModel):
    """Which subscription new terminals of one coding CLI should open on."""

    agent: str = Field(description="Coding agent to switch: 'claude' or 'codex'.")
    account_id: str = Field(
        description=(
            "Id of a stored subscription of that agent (see /api/agent-accounts). "
            "Terminals that are already open keep the account they started with."
        )
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


class RenameTerminalRequest(BaseModel):
    """A new call-sign for one pane."""

    name: str = Field(
        min_length=1,
        max_length=MAX_TERMINAL_NAME,
        description=(
            "New call-sign for the pane. The agent running in it is not touched — "
            "only the name it is addressed by changes."
        ),
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
    max_workspaces: int | None


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
    force: bool = Field(
        default=False,
        description=(
            "Spawn a second fleet even though the same instruction recently "
            "went to panes that are still open. Set it ONLY when the user "
            "explicitly asked to run the same task again — a repeated order "
            "without it is refused with the call-signs already working on it."
        ),
    )


class PromptAttachment(BaseModel):
    """One analysed file the user dropped, on its way back into a prompt.

    Returned by ``POST /terminals/{name}/attach?analyze=true`` and handed back
    here unchanged. It travels through the caller rather than being held on the
    server because a drop and the sentence that explains it are one user action
    split across two requests: parking the analysis server-side would make two
    browser tabs, the CLI and a voice turn fight over whose attachment is
    "current".
    """

    name: str = Field(description="The dropped file's display name.")
    reference: str = Field(
        default="",
        description="How the agent should refer to the file (@path or a quoted path).",
    )
    kind: str = Field(
        default="other",
        description="What the file is: image, text, pdf, or other.",
    )
    detail: str = Field(
        default="",
        max_length=DROP_DETAIL_MAX_CHARS,
        description=(
            "The vision description of an image, or the extracted text of a "
            "document. Empty when neither could be produced."
        ),
    )
    described_by: str = Field(
        default="none",
        description="Which layer produced 'detail': vision, extraction, or none.",
    )
    note: str = Field(
        default="",
        description="Why 'detail' is empty or shortened. Empty on the happy path.",
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
    attachments: list[PromptAttachment] = Field(
        default_factory=list,
        max_length=MAX_PROMPT_ATTACHMENTS,
        description=(
            "Files the user dropped with this instruction, as returned by the "
            "attach endpoint with analyze=true. Their descriptions and extracted "
            "text are folded into the composed prompt so the coding agent gets "
            "the content of a screenshot it may not be able to open itself."
        ),
    )


class AgentStatus(BaseModel):
    name: str
    display_name: str
    installed: bool
    version: str | None = None
    install_command: str | None = None
    kind: str = Field(
        default="cli",
        description=(
            "'cli' for a coding agent, 'shell' for a plain terminal. A client uses "
            "this to say what a pane will be, without hardcoding entry names."
        ),
    )
    description: str = Field(default="", description="One line for a picker.")
    custom: bool = Field(
        default=False,
        description=(
            "True for a CLI the user added themselves. A surface uses this to "
            "offer editing or removing it, which makes no sense for a shipped "
            "entry."
        ),
    )
    logo_url: str = Field(
        default="",
        description=(
            "Where to fetch this entry's mark. Empty for a shipped entry, whose "
            "logo is a local asset the client already has, and for a custom one "
            "with no logo, which falls back to a monogram."
        ),
    )


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


class WorkspaceFileItem(BaseModel):
    """One workspace-relative entry in the IDE file explorer."""

    name: str
    path: str = Field(description="POSIX-style path relative to the workspace root.")
    is_directory: bool
    is_symlink: bool = False
    size: int | None = None


class WorkspaceFilesResponse(BaseModel):
    workspace_id: str
    root_name: str
    path: str
    entries: list[WorkspaceFileItem]
    truncated: bool = False
    error: str | None = None


class WorkspaceFilePreviewResponse(BaseModel):
    """A bounded in-app preview without exposing an absolute host path."""

    workspace_id: str
    path: str
    name: str
    size: int
    media_type: str
    text: str | None = None
    truncated: bool = False
    hex_preview: str | None = None


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


class OpenTerminalTargetRequest(BaseModel):
    """A path explicitly activated in one Agentic-IDE terminal."""

    workspace_id: str = Field(
        min_length=1,
        max_length=160,
        description="Workspace whose root relative paths are resolved against.",
    )
    target: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "Printed file/folder path or file URI, optionally followed by a line and column suffix."
        ),
    )


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
    name: str = Field(default="", description="The label the user gave this tab, if any.")
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


class InterruptedPane(BaseModel):
    """One pane that came back with its conversation and was never restarted."""

    workspace_id: str = ""
    workspace: str = Field(default="", description="The workspace tab this pane belongs to.")
    folder: str = ""
    key: str
    name: str
    agent: str
    display_name: str
    status: str
    continuable: bool = Field(
        description=(
            "Will a 'continue' reach it? False only when its agent is DEAD — an "
            "instruction cannot be typed into a terminal that exited. A pane "
            "that is merely still starting IS continuable; see 'starting'."
        )
    )
    starting: bool = Field(
        default=False,
        description=(
            "Its agent is still coming up (cold starts are staggered). Continuing "
            "it is a promise kept a few seconds later, not a refusal."
        ),
    )
    queued: bool = Field(
        default=False,
        description="A 'continue' is already waiting to be delivered to this pane.",
    )
    blocked_reason: str = Field(
        default="", description="Why not, in one sentence. Empty when it can."
    )
    last_task: str = Field(
        default="",
        description=(
            "What this pane was last asked to do, shortened. Empty when its last "
            "instruction was typed into the pane directly rather than sent by Jarvis."
        ),
    )
    prompts_sent: int = 0
    started_at: float | None = Field(
        default=None, description="When the resumed agent process started."
    )


class PaneNotification(BaseModel):
    """One thing that happened in one pane, as the bell lists it."""

    id: str
    kind: str = Field(
        description=(
            "What happened: 'completed' (it was working and stopped), "
            "'needs_input' (a question is on its screen), 'exited' (its process "
            "is gone) or 'failed' (its agent could not be started)."
        )
    )
    workspace_id: str
    workspace: str = Field(default="", description="What that workspace tab is called.")
    pane_key: str
    pane: str = Field(default="", description="The pane's call-sign — 'T3'.")
    agent: str = Field(default="", description="The coding CLI's id.")
    display_name: str = Field(default="", description="What the user reads — 'Claude Code'.")
    title: str = Field(
        default="",
        description=(
            "One clause saying what happened. 'Finished' means the pane went "
            "quiet, never that the work is correct — nothing here reads the "
            "agent's output to judge its result."
        ),
    )
    detail: str = Field(default="", description="What the pane was last asked to do.")
    created_at: float
    read: bool = False


class PaneNotificationsResponse(BaseModel):
    """The bell's whole state in one answer."""

    enabled: bool = Field(
        default=True,
        description=(
            "Is the background sweep collecting new entries? False when "
            "[agentic_ide].pane_notifications is off — the list then only holds "
            "what was collected before."
        ),
    )
    watching: bool = Field(
        default=False,
        description=(
            "Is the sweep task actually running? An empty list has two very "
            "different causes — nothing has happened, or nobody is looking — "
            "and this is what tells them apart."
        ),
    )
    panes_watched: int = Field(
        default=0,
        description=(
            "How many panes the sweep holds state for. Zero while 'watching' is "
            "true means it has not completed a pass yet."
        ),
    )
    unread: int = 0
    notifications: list[PaneNotification] = Field(default_factory=list)


class ReadNotificationsRequest(BaseModel):
    ids: list[str] = Field(
        default_factory=list,
        description="Entries to mark read. Empty marks every one of them.",
    )


class NotificationsChangedResponse(BaseModel):
    ok: bool = True
    changed: int = Field(default=0, description="How many entries this call actually altered.")
    unread: int = 0


class InterruptedResponse(BaseModel):
    """Every pane, in every open workspace, that is waiting to be told to carry on."""

    count: int = 0
    continuable_count: int = Field(
        default=0, description="How many of them can be continued right now."
    )
    prompt: str = Field(default="", description="The instruction the continue action would send.")
    panes: list[InterruptedPane] = Field(default_factory=list)


class ContinueInterruptedRequest(BaseModel):
    names: list[str] = Field(
        default_factory=list,
        description=(
            "Call-signs to continue. Empty continues every interrupted pane in "
            "every open workspace. A name that is not waiting to be continued is "
            "reported in 'failed' rather than silently ignored."
        ),
    )
    prompt: str = Field(
        default="",
        description=(
            "What to send instead of the default 'continue'. The agent still "
            "holds its whole conversation, so short beats elaborate."
        ),
    )


class ContinueInterruptedResponse(BaseModel):
    ok: bool = True
    continued: list[str] = Field(
        default_factory=list, description="Panes that accepted the instruction and started."
    )
    queued: list[str] = Field(
        default_factory=list,
        description=(
            "Panes whose agent had not started yet. The instruction is held and "
            "delivered the moment each one comes up — say 'shortly', not 'done'."
        ),
    )
    unconfirmed: list[str] = Field(
        default_factory=list,
        description=(
            "Panes the text was typed into without a confirmed submit. Their prompt "
            "may be sitting in the input box — say so rather than claiming they ran."
        ),
    )
    failed: list[dict] = Field(
        default_factory=list, description="Panes that could not be continued, with the reason."
    )
    remaining: int = Field(
        default=0, description="Interrupted panes still waiting after this call."
    )


class TerminalActivity(BaseModel):
    """What one pane is DOING, and nothing about what it is doing it about.

    The skinny base of :class:`TerminalRecap`, and the whole answer of the
    badge's fast poll: the recap poll also schedules a summarizer pass over
    every pane's transcript, which is why it runs on a relaxed clock — and why
    the status badge that rode on it always lagged the pane by several
    seconds. This row costs one look at the pane's stamped reading, so it can
    be polled fast enough for the badge to feel live. One declaration for both
    rows, so the two polls cannot drift apart about what these fields mean.
    """

    key: str
    name: str
    status: str = Field(
        description="The pane's process status: 'pending', 'live', 'exited' or 'error'."
    )
    activity: str = Field(
        default="",
        description=(
            "Whether this pane's agent is still on the job: 'working' (its "
            "screen is moving), 'waiting' (alive and standing still), 'asking' "
            "(standing still with a question on screen), 'starting', 'exited' "
            "or 'failed'. Read from the terminal itself rather than from "
            "anything a particular CLI prints, so it holds for every coding CLI "
            "a pane can run, including ones added later. Empty for a plain "
            "terminal, which runs no agent."
        ),
    )
    activity_since: float = Field(
        default=0.0,
        description=(
            "When the pane entered that state (epoch seconds), so 'waiting' can "
            "be shown with how long it has been waiting. 0 when unknown."
        ),
    )
    worked: bool = Field(
        default=False,
        description=(
            "Has anything ever been asked of this pane — an instruction sent to "
            "it, or the conversation it resumed? It is what separates an agent "
            "that FINISHED from a terminal nobody has asked for anything: the "
            "same still screen, and not the same news."
        ),
    )


class TerminalRecap(TerminalActivity):
    """What one pane is doing, in the two lengths its header renders."""

    recap: str = Field(description="One clause for the pane header; the pane's width clips it.")
    recap_detail: str = Field(description="The several-sentence version, shown in the recap card.")
    source: str = Field(
        default="heuristic",
        description=(
            "Who wrote this recap: 'user' when the user wrote it themselves, "
            "'model' when a brain summarized the pane, 'heuristic' when it was "
            "derived from the transcript by rule — which is what an install with "
            "no reachable provider always gets."
        ),
    )
    reason: str = Field(
        default="",
        description=(
            "Why this recap and not a better one: 'pinned', 'summarized', "
            "'disabled', 'not_started', 'warming', 'working', 'queued' or "
            "'unavailable'. The UI turns it into a sentence, so a thin recap "
            "explains itself instead of looking broken."
        ),
    )
    writer: str = Field(
        default="",
        description="The model that wrote it, when one did. Empty otherwise.",
    )
    note: str = Field(
        default="",
        description=(
            "What went wrong the last time this pane was summarized, scrubbed of "
            "anything credential-shaped. Empty whenever nothing has."
        ),
    )
    generated_at: float = Field(
        default=0.0,
        description=(
            "When the model wrote it, or when the user did. 0 for the "
            "transcript-derived recap, which is recomputed on every read."
        ),
    )


class RecapsResponse(BaseModel):
    workspace_id: str | None = None
    terminals: list[TerminalRecap] = Field(default_factory=list)


class ActivityResponse(BaseModel):
    workspace_id: str | None = None
    terminals: list[TerminalActivity] = Field(default_factory=list)


class LastPrompt(BaseModel):
    """The exact text a pane was last handed — the receipt, in full.

    Its own request rather than a field of the workspace state, and that split
    is deliberate: a composed brief runs to several thousand characters, and
    carrying every pane's copy in every poll was measured as waste worth
    removing. What the state keeps is the SHORT half (an excerpt, a length, a
    timestamp), which is enough to render the receipt; this is what the user
    gets when they click it open, and it is the only claim that can be checked
    word for word against what the agent was told.
    """

    name: str
    text: str = Field(description="The prompt as it was written into the pane, unabridged.")
    chars: int = Field(default=0, description="Length of ``text``.")
    at: float | None = Field(
        default=None, description="When it was delivered (epoch seconds); null if none was."
    )
    submitted: bool | None = Field(
        default=None,
        description=(
            "True when the agent accepted it and started, false when it is still "
            "sitting in the pane's input box, null when it could not be confirmed."
        ),
    )
    prompts_sent: int = Field(default=0, description="How many prompts this pane has been sent.")


class PromptHistoryItem(BaseModel):
    """One exact prompt in a pane's delivery history."""

    id: str = Field(description="Opaque stable id for this history entry.")
    sequence: int = Field(description="Delivery number within this pane.")
    text: str = Field(description="Exact prompt text, unabridged.")
    chars: int = Field(description="Length of ``text``.")
    at: float = Field(description="When it was delivered (epoch seconds).")
    submitted: bool | None = Field(
        description=(
            "True when the agent accepted it, false when it remained in the input "
            "box, null when delivery could not be confirmed."
        )
    )


class PromptHistoryResponse(BaseModel):
    """All prompt text still belonging to one pane, newest first."""

    name: str
    total: int = Field(description="How many prompts this pane is known to have received.")
    available: int = Field(description="How many exact prompt records are available here.")
    complete: bool = Field(
        description="False only when this pane predates durable prompt-history recording."
    )
    items: list[PromptHistoryItem] = Field(default_factory=list)


class RecapEdit(BaseModel):
    """A recap the user is writing for one pane themselves."""

    recap: str = Field(
        description=(
            "The header line. Empty clears a hand-written recap and hands the "
            "pane back to the automatic one."
        ),
        max_length=recap_engine.MAX_EDIT_HEADLINE * 4,
    )
    recap_detail: str = Field(
        default="",
        description="The longer version behind it. Optional; the header line stands alone.",
        max_length=recap_engine.MAX_EDIT_DETAIL * 2,
    )


# --------------------------------------------------------------------------- #
# REST                                                                        #
# --------------------------------------------------------------------------- #
@router.get("/state", summary="Agentic-IDE workspace state")
async def get_state() -> dict:
    """The open workspace, its terminals, and whether focus mode is on."""
    return get_registry().state()


@router.get("/state/brief", summary="Agentic-IDE workspace state, briefly")
async def get_state_brief() -> dict:
    """The workspace as a language model needs it to steer the panes.

    Pane names, agents, status, idleness, and one recap line each — never
    transcripts, prompts, or project profiles. This is what the
    ``agentic-ide-status`` voice command reads; the full ``/state`` payload
    is the UI's and runs ~25 000 characters, which a model-facing
    tool-result cap would slice mid-JSON.
    """
    return get_registry().brief_state()


@router.get("/agents", response_model=AgentsResponse, summary="Coding agents available")
async def get_agents() -> AgentsResponse:
    """What this machine can open in a terminal, and how to install it.

    Every registered entry (``jarvis.workspace.agents``): the coding-agent CLIs
    and the plain terminal, which is "installed" whenever this host has a shell.
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
            kind=info.kind,
            description=info.description,
            custom=info.custom,
            logo_url=info.logo_url,
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


@router.get(
    "/workspaces/{workspace_id}/files",
    response_model=WorkspaceFilesResponse,
    summary="Browse files in an open workspace",
)
async def get_workspace_files(workspace_id: str, path: str = "") -> WorkspaceFilesResponse:
    """One lazy-loaded level of the workspace's file tree.

    Paths on the wire are relative to the selected workspace. The filesystem
    helper resolves them again and rejects traversal and symlink escapes before
    touching a directory, so this endpoint cannot become a general host browser.
    """
    session = get_registry().get(workspace_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    try:
        listing = await asyncio.to_thread(list_workspace_dir, session.folder, path)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Workspace folder not found.") from None

    return WorkspaceFilesResponse(
        workspace_id=workspace_id,
        root_name=Path(session.folder).name or str(session.folder),
        path=listing.path,
        entries=[WorkspaceFileItem(**asdict(entry)) for entry in listing.entries],
        truncated=listing.truncated,
        error=listing.error,
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


_TERMINAL_LOCATION_SUFFIX = re.compile(
    r"(?:#L[1-9]\d*(?::[1-9]\d*)?|:[1-9]\d*(?::[1-9]\d*)?|"
    r"\([1-9]\d*(?:,[1-9]\d*)?\))$",
    re.IGNORECASE,
)


def _resolve_terminal_target(workspace_folder: str, printed: str) -> Path:
    """Resolve a printed path without letting terminal output escape its workspace.

    Coding-CLI output is untrusted. A modifier-click is deliberate, but it must
    still not turn an OSC-8 payload into an arbitrary local file launcher.
    Relative and absolute paths must therefore resolve inside the pane's own
    workspace, including after symlinks are followed. Common line/column
    locators are accepted because compilers and agents routinely print them.
    """
    value = printed.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        value = value[1:-1].strip()
    value = _TERMINAL_LOCATION_SUFFIX.sub("", value)
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError("invalid terminal target")

    if value.lower().startswith("file:"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(value)
        if parsed.scheme.lower() != "file" or parsed.query or parsed.fragment:
            raise ValueError("invalid file URI")
        if parsed.netloc and parsed.netloc.casefold() != "localhost":
            raise ValueError("remote file URI")
        value = unquote(parsed.path)
        # Local Windows file URIs are written as file:///C:/path.
        if len(value) > 2 and value[0] == "/" and value[2] == ":":
            value = value[1:]
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("invalid file URI")
    elif "://" in value:
        raise ValueError("unsupported URI scheme")

    root = Path(workspace_folder).expanduser().resolve(strict=True)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    target = candidate.resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("terminal target escapes workspace") from exc
    if not (target.is_file() or target.is_dir()):
        raise ValueError("terminal target is not openable")
    return target


_WORKSPACE_PREVIEW_MAX_CHARS = 2 * 1024 * 1024
_WORKSPACE_HEX_PREVIEW_BYTES = 256
_WORKSPACE_FILE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "media-src 'self'; sandbox"
)


def _resolve_workspace_file(workspace_id: str, path: str) -> Path:
    """Resolve one file inside an open workspace without leaking host paths."""
    session = get_registry().get(workspace_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That workspace is not open.")
    try:
        target = _resolve_terminal_target(session.folder, path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="That workspace file is unavailable.") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="That workspace file is unavailable.")
    return target


def _read_workspace_file_preview(target: Path) -> dict[str, object]:
    """Extract readable text, with a small hexadecimal fallback for any binary."""
    size = target.stat().st_size
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    text: str | None = None
    truncated = False

    # Lazy import keeps document parsers off the application boot path (AP-26).
    from jarvis.ultrawiki.extract import MAX_DOCUMENT_BYTES, extract_text

    if size <= MAX_DOCUMENT_BYTES:
        extracted = extract_text(target, filename=target.name, mime=media_type)
        if extracted.ok:
            text = extracted.text[:_WORKSPACE_PREVIEW_MAX_CHARS]
            truncated = len(extracted.text) > _WORKSPACE_PREVIEW_MAX_CHARS

    hex_preview: str | None = None
    if text is None and size > 0:
        with target.open("rb") as handle:
            head = handle.read(_WORKSPACE_HEX_PREVIEW_BYTES)
        hex_preview = " ".join(f"{byte:02X}" for byte in head)

    return {
        "name": target.name,
        "size": size,
        "media_type": media_type,
        "text": text,
        "truncated": truncated,
        "hex_preview": hex_preview,
    }


@router.get(
    "/workspaces/{workspace_id}/file",
    summary="View one file from an open workspace",
)
async def get_workspace_file(workspace_id: str, path: str) -> FileResponse:
    """Stream any workspace file for an in-app native viewer.

    Active document formats receive a script-blocking CSP. Resolution follows
    symlinks and rejects every target outside the selected workspace.
    """
    target = await asyncio.to_thread(_resolve_workspace_file, workspace_id, path)
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        target,
        media_type=media_type,
        filename=target.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": _WORKSPACE_FILE_CSP,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/workspaces/{workspace_id}/file-preview",
    response_model=WorkspaceFilePreviewResponse,
    summary="Preview one file from an open workspace",
)
async def get_workspace_file_preview(
    workspace_id: str, path: str
) -> WorkspaceFilePreviewResponse:
    """Return bounded readable text or a safe hexadecimal fallback."""
    target = await asyncio.to_thread(_resolve_workspace_file, workspace_id, path)
    try:
        preview = await asyncio.to_thread(_read_workspace_file_preview, target)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="That workspace file is unavailable.") from exc
    return WorkspaceFilePreviewResponse(
        workspace_id=workspace_id,
        path=path.replace("\\", "/"),
        **preview,
    )


@router.post(
    "/terminal-target/open",
    summary="Open a file or folder linked in an Agentic-IDE terminal",
)
async def open_terminal_target(
    request: Request, req: OpenTerminalTargetRequest
) -> dict[str, object]:
    """Open a modifier-clicked workspace path with the operating-system default.

    This is local-desktop-only: a remote browser would otherwise act on the
    server's screen. Resolution is confined to the addressed open workspace
    before the shared cross-platform opener sees the path.
    """
    if not is_loopback_request(request.scope):
        raise HTTPException(
            status_code=403,
            detail="Terminal paths can only be opened on the computer running Jarvis.",
        )
    if not getattr(request.app.state, "native_file_actions", False):
        raise HTTPException(status_code=404, detail="native file actions unavailable")

    session = get_registry().get(req.workspace_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That workspace is not open.")
    try:
        target = await asyncio.to_thread(_resolve_terminal_target, session.folder, req.target)
    except (OSError, RuntimeError, ValueError) as exc:
        # One answer for missing, malformed and outside-workspace paths: never
        # disclose through a crafted terminal payload which local file exists.
        raise HTTPException(status_code=404, detail="That workspace path is unavailable.") from exc

    from jarvis.platform import open_path

    opened = await asyncio.to_thread(open_path.open_file, target)
    kind = "directory" if target.is_dir() else "file"
    log.info(
        "Agentic IDE terminal target: workspace=%s kind=%s opened=%s path=%s",
        req.workspace_id,
        kind,
        opened,
        target,
    )
    return {"opened": bool(opened), "kind": kind, "path": str(target)}


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
async def activate_workspace(request: Request, req: ActivateWorkspaceRequest) -> dict:
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
    # Each workspace carries its own coding mode, so bringing a different one to
    # the front can change the answer without anyone touching the toggle.
    await _announce_coding_mode(request)
    await _announce_workspace(request, session, "activated")
    return {
        "ok": True,
        "active_id": session.id if session else None,
        "state": get_registry().state(),
    }


@router.patch("/workspaces/{workspace_id}", summary="Rename a workspace")
async def rename_workspace(
    request: Request, workspace_id: str, req: RenameWorkspaceRequest
) -> dict:
    """Change only a workspace's tab label; its folder and agents keep running."""
    registry = get_registry()
    try:
        session = await registry.rename(workspace_id, req.name)
    except SessionError as exc:
        status = 404 if registry.get(workspace_id) is None else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    # Other windows keep showing the old tab label until they re-fetch.
    await _announce_workspace(request, session, "renamed")
    return {
        "ok": True,
        "workspace": session.to_card(active=session.id == registry.active_id),
        "state": registry.state(),
    }


@router.post("/session", summary="Open an Agentic-IDE workspace")
async def start_session(request: Request, req: StartSessionRequest) -> dict:
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
    # Every other window has to hear this. One of them may be showing the grid
    # of a workspace this one just replaced at the front, and a grid nobody
    # corrects is a grid whose panes attach to the wrong workspace.
    await _announce_workspace(request, session, "opened")
    return {"ok": True, "session": session.to_dict(), "state": get_registry().state()}


@router.delete(
    "/session",
    summary="Close the Agentic-IDE workspace",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def end_session(request: Request) -> dict:
    """Close the workspace on screen and stop every agent running in it.

    Other open workspaces are untouched; the most recently used of them takes
    the front. Use ``DELETE /workspaces/{workspace_id}`` to close a specific
    one instead of whichever is showing.
    """
    closed = await get_registry().end()
    # Closing the front workspace ends its coding mode — or hands it to whichever
    # workspace takes the front, which may have a different one.
    await _announce_coding_mode(request)
    await _announce_workspace(request, get_registry().session, "closed")
    return {"ok": True, "closed": closed, "state": get_registry().state()}


@router.delete(
    "/workspaces/{workspace_id}",
    summary="Close one Agentic-IDE workspace",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def close_workspace(request: Request, workspace_id: str) -> dict:
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
    await _announce_coding_mode(request)
    await _announce_workspace(request, registry.session, "closed")
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
async def resume_workspace(request: Request) -> dict:
    """Reopen everything that was open: same panes, same places, same coding CLIs.

    Every workspace in the restore point, not just whichever was on screen —
    somebody who had four folders open had four. Wherever the coding CLI supports
    it, each pane also continues the conversation it was having.

    No agent is started here. The panes connect the way they always do, and that
    connection is what continues them, so a resumed workspace takes exactly the
    same path as a freshly opened one — and restoring five workspaces launches
    nothing until their panes are looked at.

    ``409`` when there is nothing to resume, ``422`` when nothing could be
    reopened at all. A workspace that individually could not come back (for
    example, its folder was deleted) does not fail the request: it is reported
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
    # A restore is the transition most likely to happen while nobody is looking
    # — it is what follows an app restart — so it is also the one a client is
    # most likely to miss. Reopened workspaces carry NEW ids, which means a view
    # that keeps the old ones has panes addressing workspaces that no longer
    # exist; saying so here is what turns that into a re-fetch.
    await _announce_workspace(request, registry.session, "restored")
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


@router.get(
    "/interrupted",
    response_model=InterruptedResponse,
    summary="Coding sessions that were interrupted and never restarted",
)
async def get_interrupted() -> InterruptedResponse:
    """Which panes came back holding a conversation and have been told nothing since.

    The state a restart leaves behind. Resuming reconnects each pane to the
    conversation it was having, but a coding CLI launched on an old transcript
    reads it and then waits — so an agent that was halfway through a job comes
    back knowing everything about it and doing nothing, looking exactly like a
    pane that finished.

    ``continuable`` is per pane and answered against the machine as it is now: a
    pane whose agent is not running cannot be typed into, and ``blocked_reason``
    says why. An empty list is the normal answer — nothing was interrupted.
    """
    panes = interrupted.scan(get_registry())
    return InterruptedResponse(
        count=len(panes),
        continuable_count=sum(1 for pane in panes if pane.continuable),
        prompt=interrupted.CONTINUE_PROMPT,
        panes=[InterruptedPane(**pane.to_dict()) for pane in panes],
    )


@router.post(
    "/interrupted/continue",
    response_model=ContinueInterruptedResponse,
    summary="Tell interrupted coding sessions to carry on",
)
async def continue_interrupted(req: ContinueInterruptedRequest) -> ContinueInterruptedResponse:
    """Type ``continue`` into the panes a restart left standing still.

    With no ``names``, every interrupted pane in every open workspace — which is
    the shape of the problem, since a restart interrupts them all at once. The
    instruction goes through the ordinary prompt path, so the same guarantees
    hold: control characters stripped, the submit verified against the pane's own
    screen, and three honest outcomes rather than two.

    **Read ``unconfirmed``.** Those panes were typed into without a confirmed
    submit: the text may be sitting in the input box waiting for a keypress.
    Reporting them as running is the one wrong thing to do with this answer.

    ``409`` only when no workspace is open at all. Nothing to continue is a
    normal, empty success.
    """
    registry = get_registry()
    if not registry.sessions:
        raise HTTPException(status_code=409, detail="No Agentic-IDE session is running.")
    report = await interrupted.continue_panes(
        registry, names=req.names, prompt=req.prompt or interrupted.CONTINUE_PROMPT
    )
    return ContinueInterruptedResponse(
        **report.to_dict(),
        # Re-scanned rather than subtracted: a pane the user drove themselves
        # while this call was in flight has left the list too.
        remaining=len(interrupted.scan(registry)),
    )


@router.get(
    "/notifications",
    response_model=PaneNotificationsResponse,
    summary="Terminals that finished, asked something, or stopped",
)
async def get_notifications() -> PaneNotificationsResponse:
    """What the bell in the Agentic-IDE header is showing.

    Newest first, across EVERY open workspace — a pane in a background tab is
    exactly the one whose finishing nobody would otherwise notice. Each entry
    names its workspace and its call-sign, which is what the client's "jump to
    pane" needs to switch tabs before it scrolls.

    A ``completed`` entry means the pane stopped drawing the interrupt hint its
    CLI draws while it is busy. That is a claim about the terminal, not about
    the work: nothing here reads the agent's output to decide whether the job
    went well.

    Entries live in memory and go with the workspace they belong to. A restart
    empties the list on purpose — the panes it pointed at are gone.
    """
    return PaneNotificationsResponse(**notifications.state())


@router.post(
    "/notifications/read",
    response_model=NotificationsChangedResponse,
    summary="Mark terminal notifications as read",
)
async def read_notifications(req: ReadNotificationsRequest) -> NotificationsChangedResponse:
    """Clear the unread count, for the given entries or for all of them.

    The entries stay in the list — this only stops the bell from counting them.
    ``changed`` is how many were actually unread, so "there was nothing to do"
    is distinguishable from "four were cleared".
    """
    center = notifications.center()
    changed = center.mark_read(req.ids or None)
    return NotificationsChangedResponse(changed=changed, unread=center.unread)


@router.delete(
    "/notifications",
    response_model=NotificationsChangedResponse,
    summary="Discard every terminal notification",
)
async def clear_notifications() -> NotificationsChangedResponse:
    """Throw the whole list away.

    Nothing about a pane changes: an entry is a note that something happened,
    and discarding it stops the note being shown. Every agent it came from
    keeps running exactly as it was.
    """
    center = notifications.center()
    dropped = center.clear()
    return NotificationsChangedResponse(changed=dropped, unread=center.unread)


@router.delete(
    "/notifications/{notification_id}",
    response_model=NotificationsChangedResponse,
    summary="Discard one terminal notification",
)
async def clear_notification(notification_id: str) -> NotificationsChangedResponse:
    """Throw one entry away. An id that is not in the list is a quiet no-op.

    Not a 404: the entry may have gone with its workspace between the client
    reading the list and pressing the button, and the outcome the caller wanted
    — that entry is not there — is the one they got.
    """
    center = notifications.center()
    dropped = center.clear([notification_id])
    return NotificationsChangedResponse(changed=dropped, unread=center.unread)


@router.post(
    "/terminals/close-batch",
    summary="Close several terminals",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def close_terminals(request: Request, req: CloseTerminalsRequest) -> dict:
    """Stop selected coding agents in one locked batch and return canonical state."""
    registry = get_registry()
    session = registry.session
    try:
        closed, failed = await registry.close_terminals(req.names)
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Voice and the CLI land here too: without the event, every OTHER client
    # keeps rendering panes whose agents are already gone (the same contract as
    # DELETE /terminals/agent/{agent} below).
    bus = getattr(request.app.state, "bus", None)
    if bus is not None and closed and session is not None:
        try:
            await bus.publish(
                terminals_closed_event(session, closed, source_layer="agentic_ide_routes")
            )
        except Exception as exc:  # noqa: BLE001 - the panes are already closed
            log.debug("AgenticIdeTerminalsClosed publish failed: %s", exc)
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
async def set_mode(request: Request, req: ModeRequest) -> dict:
    """Turn the focused coding mode on or off.

    While on, Jarvis answers inside the open workspace — it knows the folder,
    the codebase, and what each named terminal is doing. Turning it off returns
    the assistant to its normal, whole-machine behaviour; the terminals keep
    running either way.

    The switch is announced on the bus because coding mode is NOT local to this
    view: it changes how Jarvis answers on every screen, so the app-wide
    indicator has to hear about a switch made anywhere — including one made by
    voice or by the CLI while the user is looking at a different section.
    """
    registry = get_registry()
    try:
        enabled = registry.set_focus_mode(req.enabled)
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        try:
            await bus.publish(
                coding_mode_event(registry.session, source_layer="agentic_ide_routes")
            )
        except Exception as exc:  # noqa: BLE001 - the mode is set either way
            log.debug("AgenticIdeCodingModeChanged publish failed: %s", exc)

    return {"ok": True, "focus_mode": enabled}


@router.put("/surface-context", summary="Report the visible Agentic-IDE terminal")
async def set_surface_context(req: SurfaceContextRequest) -> dict:
    """Keep deictic voice/chat references aligned with the visible pane.

    The state is ephemeral and active-workspace scoped. Grid view clears it,
    because several terminals are visible there and guessing one would be less
    honest than asking for a call-sign.

    Two ways in, one meaning: `view` is the contract, `chat_view` is what an
    older bundle sends. A body carrying neither reports the grid, which is the
    view that promises the least — see `workspace_view.VIEW_DEFAULT`.
    """
    view = req.view if req.view is not None else view_from_legacy_chat_flag(req.chat_view)
    accepted = get_registry().set_surface_context(
        workspace_id=req.workspace_id,
        view=view,
        on_screen=req.on_screen,
        terminal=req.terminal,
        prompt_target=req.prompt_target,
    )
    return {"ok": True, "accepted": accepted}


@router.get("/ui-preferences", summary="Remembered workspace display preferences")
async def get_ui_preferences() -> dict:
    """How the workspace should look, as this machine last left it.

    Today that is one thing: the size of the terminal text. It is answered by
    the backend rather than read from the page's own storage because the desktop
    window is an embedded WebView that starts each run with an empty one — a
    preference kept only in the browser is forgotten on every restart.

    ``stored`` says whether a size was ever chosen here. A client that still
    holds an older, browser-only choice uses it to hand that choice over instead
    of letting the default overwrite it.
    """
    from jarvis.agentic_ide import ui_prefs

    return {
        "ok": True,
        "terminal_font_size": await asyncio.to_thread(ui_prefs.terminal_font_size),
        "stored": await asyncio.to_thread(ui_prefs.has_terminal_font_size),
        "min": ui_prefs.FONT_MIN,
        "max": ui_prefs.FONT_MAX,
        "default": ui_prefs.FONT_DEFAULT,
    }


@router.put("/ui-preferences", summary="Remember a workspace display preference")
async def set_ui_preferences(req: TerminalTextSizeRequest) -> dict:
    """Remember the terminal text size until it is changed again.

    Survives closing the workspace, closing the app and restarting the machine,
    and applies to every terminal in every workspace — one person reads at one
    size. Open panes are re-rendered by the UI immediately; nothing is restarted
    and no agent is interrupted by the change.
    """
    from jarvis.agentic_ide import ui_prefs

    size = await asyncio.to_thread(ui_prefs.set_terminal_font_size, req.terminal_font_size)
    return {
        "ok": True,
        "terminal_font_size": size,
        "stored": True,
        "min": ui_prefs.FONT_MIN,
        "max": ui_prefs.FONT_MAX,
        "default": ui_prefs.FONT_DEFAULT,
    }


@router.get("/accounts", summary="Which subscription new terminals use")
async def get_accounts() -> dict:
    """The active subscription of every coding CLI, with its display name.

    The workspace's own view of the account switcher: which plan the next
    terminal will spend, and how many are registered — enough for a surface to
    stay quiet for everyone holding a single login. The full list, its sign-in
    state and the CRUD around it live under ``/api/agent-accounts``.
    """
    registry = get_registry()
    return {"ok": True, "accounts": await asyncio.to_thread(registry.active_accounts)}


@router.put("/accounts/active", summary="Switch which subscription new terminals use")
async def set_active_account(req: ActiveAccountRequest) -> dict:
    """Point new terminals of one coding CLI at another subscription.

    Running panes are deliberately untouched: each carries the account it was
    opened with, so this can never move an agent mid-conversation onto a plan
    whose history does not contain that conversation. The choice also becomes
    the machine's stored default, so it survives a restart.
    """
    registry = get_registry()
    try:
        account = await registry.set_active_account(req.agent, req.account_id)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    display = AGENT_DISPLAY.get(req.agent, req.agent)
    return {
        "ok": True,
        "agent": req.agent,
        "active_account": account.id,
        "active_label": account.label,
        "message": f"New {display} terminals will use {account.label}.",
        "state": registry.state(),
    }


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
    the pane cap within that workspace cut the request short.

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


@router.post("/terminals/refold", summary="Re-fold the workspace into columns of a given depth")
async def refold_workspace(req: RefoldRequest) -> dict:
    """Re-deal every pane into columns ``depth`` deep, keeping their order.

    The workspace is always exactly one screenful, so a pane can only be given
    room taken from somewhere else. When a row holds so many panes that each is
    narrower than the terminal it draws, the agents' output is clipped at the
    tile edge — and the only room left to spend is height. Folding the row in
    two halves the column count and so doubles every pane's width, at the text
    size the user chose rather than a smaller one.

    Nothing is started, stopped, resized or remounted: only the two numbers
    saying where each pane is drawn change, exactly as for a move. Re-folding to
    the depth the workspace already has succeeds and changes nothing.
    """
    try:
        session = await get_registry().refold(req.depth)
    except SessionError as exc:
        message = str(exc)
        # A depth this grid cannot express is the caller's arithmetic; a
        # workspace that is not open is a conflict nothing here can fix.
        status = 409 if "No Agentic-IDE session" in message else 422
        raise HTTPException(status_code=status, detail=message) from exc
    return {
        "ok": True,
        "depth": req.depth,
        "terminals": [t.to_dict() for t in session.terminals],
        "state": get_registry().state(),
    }


@router.post("/terminals/{name}/move", summary="Move a terminal to another place in the grid")
async def move_terminal(name: str, req: MoveTerminalRequest) -> dict:
    """Rearrange the workspace: put the pane ``name`` at ``target``'s place.

    Nothing is started or stopped — this only changes where a running pane is
    drawn, which is what makes rearranging safe on a grid full of working
    agents. ``position="swap"`` exchanges the two panes and leaves the
    workspace's shape untouched; the four sides put the moved pane on that
    side OF THE TARGET, carving the target's own rectangle in half — the same
    local meaning the split buttons have.

    Dropping a pane onto itself succeeds and changes nothing.
    """
    try:
        term = await get_registry().move_terminal(name, target=req.target, position=req.position)
    except SessionError as exc:
        # Three different fixes on the caller's side, so three different codes:
        # a position this grid cannot express is a bad request, a workspace that
        # is not open is a conflict, and everything left is an unknown call-sign.
        message = str(exc)
        if "Position must be" in message:
            status = 422
        elif "No Agentic-IDE session" in message:
            status = 409
        else:
            status = 404
        raise HTTPException(status_code=status, detail=message) from exc
    return {
        "ok": True,
        "moved": term.name,
        "target": req.target,
        "position": req.position,
        "terminal": term.to_dict(),
        "state": get_registry().state(),
    }


@router.post("/layout/weights", summary="Apply dragged pane sizes to the workspace")
async def set_layout_weights(req: LayoutWeightsRequest) -> dict:
    """Persist the pane sizes a seam drag produced.

    The client sends back the whole tree it was looking at; the server adopts
    the WEIGHTS when the shape still matches its own and quietly declines when
    the workspace was reshaped mid-drag (a voice-opened pane, another client)
    — the returned state carries the authoritative tree either way, so the
    caller redraws from the answer rather than guessing.
    """
    try:
        await get_registry().set_layout_weights(req.layout)
    except SessionError as exc:
        message = str(exc)
        status = 409 if "No Agentic-IDE session" in message else 422
        raise HTTPException(status_code=status, detail=message) from exc
    return {"ok": True, "state": get_registry().state()}


@router.delete(
    "/terminals/agent/{agent}",
    summary="Close every terminal of one coding agent",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def close_terminals_by_agent(request: Request, agent: str) -> dict:
    """Stop every pane running one thing — a coding CLI, or the plain terminal."""
    chosen = agent.casefold().strip()
    if chosen not in AGENT_DISPLAY:
        known = ", ".join(f"'{name}'" for name in AGENT_DISPLAY)
        raise HTTPException(status_code=422, detail=f"Agent must be one of {known}.")
    registry = get_registry()
    session = registry.session
    if session is None:
        raise HTTPException(status_code=409, detail="No Agentic-IDE session is running.")
    closed = await close_agent_terminals(registry, chosen)
    bus = getattr(request.app.state, "bus", None)
    if bus is not None and closed:
        try:
            await bus.publish(
                terminals_closed_event(
                    session,
                    closed,
                    source_layer="agentic_ide_routes",
                )
            )
        except Exception as exc:  # noqa: BLE001 - the panes are already closed
            log.debug("AgenticIdeTerminalsClosed publish failed: %s", exc)
    return {
        "ok": True,
        "agent": chosen,
        "closed": [term.name for term in closed],
        "state": registry.state(),
    }


@router.patch("/terminals/{terminal}", summary="Rename one terminal")
async def rename_terminal(request: Request, terminal: str, req: RenameTerminalRequest) -> dict:
    """Give the pane ``terminal`` another call-sign.

    Nothing is started, stopped or restarted: the agent in the pane keeps
    working and keeps its conversation. Only the name it is addressed by
    changes — on screen, in a spoken instruction, and in every route that takes
    a ``{name}``.

    ``404`` when no pane answers to ``name``, ``409`` when another pane in the
    same workspace already carries the new one.
    """
    try:
        session, term = await get_registry().rename_terminal(terminal, req.name)
    except SessionError as exc:
        message = str(exc)
        if "already called" in message:
            status = 409
        elif message.startswith("No terminal called"):
            status = 404
        else:
            status = 422
        raise HTTPException(status_code=status, detail=message) from exc
    # A rename made by voice or the CLI is invisible to every open view until
    # it re-fetches — and a pane the UI addresses by its OLD call-sign routes
    # prompts to a name the registry no longer answers to. Same trigger-only
    # contract as the open/close announcements.
    await _announce_workspace(request, session, "renamed")
    return {
        "ok": True,
        "renamed": term.name,
        "previous": terminal,
        "terminal": term.to_dict(),
        "workspace_id": session.id,
        "state": get_registry().state(),
    }


@router.delete(
    "/terminals/{name}",
    summary="Close one terminal",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def close_terminal(request: Request, name: str) -> dict:
    """Stop the agent in the terminal called ``name`` and remove its pane."""
    registry = get_registry()
    session = registry.session
    try:
        term = await registry.close_terminal(name)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    bus = getattr(request.app.state, "bus", None)
    if bus is not None and session is not None:
        try:
            await bus.publish(
                terminals_closed_event(session, [term], source_layer="agentic_ide_routes")
            )
        except Exception as exc:  # noqa: BLE001 - the pane is already closed
            log.debug("AgenticIdeTerminalsClosed publish failed: %s", exc)
    return {"ok": True, "closed": term.name, "state": registry.state()}


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
        # The replayed screen, read ONCE per pane: the summarizer wants it and
        # so does the deterministic floor underneath it.
        lines = term.transcript.lines()
        # This poll is the app's only evidence that somebody is actually looking
        # at this workspace, which is why the refresh is scheduled here rather
        # than wherever a pane happens to be serialized. It returns immediately;
        # a summary that is due runs in the background and lands in the next
        # poll's answer.
        recap_engine.refresh_soon(term, lines=lines, folder=session.folder)
        rows.append(_recap_row(term, recap_engine.recap_for(term, lines=lines)))
    return RecapsResponse(workspace_id=session.id, terminals=rows)


@router.get(
    "/activity",
    response_model=ActivityResponse,
    summary="Whether each pane's agent is still working",
)
async def get_activity(workspace_id: str | None = None) -> ActivityResponse:
    """The status badge's own read — every pane's activity, and nothing else.

    Split from ``/recaps`` for the same reason ``/recaps`` is split from
    ``/state``: the two change on different clocks and cost different amounts.
    A recap is a sentence and scheduling one walks the transcript through the
    summarizer, so that poll is deliberately relaxed — which left the badge
    beside it reporting "working" seconds after a pane had visibly started.
    This read is one stamped word per pane, cheap enough to poll every second
    or two, so the badge can keep up with the pane it describes.

    Same conventions as ``/recaps``: without ``workspace_id`` the workspace on
    screen answers, and an unknown or closed one comes back empty rather than
    as an error — a poll that outlives its workspace is normal.
    """
    session = get_registry().get(workspace_id)
    if session is None:
        return ActivityResponse(workspace_id=None, terminals=[])
    return ActivityResponse(
        workspace_id=session.id,
        terminals=[TerminalActivity(**_activity_fields(term)) for term in session.terminals],
    )


def _activity_fields(term: Terminal) -> dict[str, object]:
    """The activity half of a pane row, from the pane's own stamped reading.

    One builder for the fast poll and the recap poll both, so "the two polls
    report the same reading" is a property of the code rather than a promise
    kept by two call sites staying in step.
    """
    reading = term.reading()
    return {
        "key": term.key,
        "name": term.name,
        "status": term.status,
        "activity": reading.activity,
        "activity_since": reading.since,
        "worked": has_work_behind_it(term),
    }


def _recap_row(term: Terminal, summary: recap_engine.SmartRecap) -> TerminalRecap:
    """One pane's recap as every recap route reports it.

    One builder for all four, so the poll and the three actions can never drift
    into describing the same pane differently — the drift class §5 calls out,
    on a payload that is read four times a minute. The activity half rides in
    from `_activity_fields`, the same builder the fast poll answers with.
    """
    return TerminalRecap(
        **_activity_fields(term),
        recap=summary.headline,
        recap_detail=summary.detail,
        source=summary.source,
        reason=summary.reason,
        writer=summary.writer,
        note=summary.note,
        generated_at=summary.generated_at,
    )


def _pane_for_recap(name: str, workspace_id: str | None) -> tuple[Terminal, Session]:
    """The pane called ``name`` and the workspace holding it.

    Without a ``workspace_id`` the search widens to every OPEN workspace rather
    than stopping at the one on screen: a recap is edited from a pane that is
    visible, and "visible" and "active" come apart the moment a second window is
    open on a second workspace.
    """
    registry = get_registry()
    if workspace_id is not None:
        session = registry.get(workspace_id)
        if session is None:
            raise HTTPException(status_code=404, detail="That workspace is not open.")
        term = session.find(name)
        if term is None:
            raise HTTPException(status_code=404, detail=f"No terminal called {name!r}.")
        return term, session
    for session in registry.sessions:
        term = session.find(name)
        if term is not None:
            return term, session
    raise HTTPException(status_code=404, detail=f"No terminal called {name!r}.")


@router.put(
    "/terminals/{name}/recap",
    response_model=TerminalRecap,
    summary="Write a terminal's recap yourself",
)
async def set_terminal_recap(
    name: str, payload: RecapEdit, workspace_id: str | None = None
) -> TerminalRecap:
    """Replace what the header of terminal ``name`` says with your own words.

    The one thing neither the model nor the string rules can know is what YOU
    are using a pane for — "the branch I'm about to demo", "leave this one
    alone", "the rewrite, second attempt". A recap written here wins over both,
    and the background summarizer stops re-describing that pane until it is
    cleared with ``DELETE``.

    An empty ``recap`` clears it, which is what selecting the text and deleting
    it plainly means.
    """
    term, _session = _pane_for_recap(name, workspace_id)
    summary = recap_engine.pin(term.key, payload.recap, payload.recap_detail)
    if not summary.headline:
        # Cleared rather than written: answer with whatever the pane says now.
        summary = recap_engine.recap_for(term, lines=term.transcript.lines())
    return _recap_row(term, summary)


@router.delete(
    "/terminals/{name}/recap",
    response_model=TerminalRecap,
    summary="Drop a hand-written recap",
)
async def clear_terminal_recap(name: str, workspace_id: str | None = None) -> TerminalRecap:
    """Hand terminal ``name`` back to the automatic recap.

    Also clears the back-off, so a pane whose summaries were failing an hour ago
    tries again on the next poll instead of sitting out the rest of its quiet
    window.
    """
    term, _session = _pane_for_recap(name, workspace_id)
    recap_engine.unpin(term.key)
    return _recap_row(term, recap_engine.recap_for(term, lines=term.transcript.lines()))


@router.post(
    "/terminals/{name}/recap/refresh",
    response_model=TerminalRecap,
    summary="Summarize a terminal again now",
)
async def refresh_terminal_recap(name: str, workspace_id: str | None = None) -> TerminalRecap:
    """Read terminal ``name`` again and write a fresh recap, waiting for it.

    The background summarizer is deliberately lazy — one summary per pane per
    75 seconds, and only when something material has changed — which is right
    for a header nobody is looking at and wrong the moment somebody is. This
    skips every one of those gates and returns the new sentence.

    Never fails because of the model: an install with no key, a depleted
    provider or a timeout comes back with the transcript-derived recap and a
    ``reason`` of ``unavailable`` plus a ``note`` saying what happened. Missing
    a summary is not an error worth a red pane (§3).
    """
    term, session = _pane_for_recap(name, workspace_id)
    summary = await recap_engine.summarize_now(
        term, lines=term.transcript.lines(), folder=session.folder
    )
    return _recap_row(term, summary)


@router.get(
    "/terminals/{name}/prompt",
    response_model=LastPrompt,
    summary="The exact prompt a terminal was last sent",
)
async def get_last_prompt(name: str, workspace_id: str | None = None) -> LastPrompt:
    """What terminal ``name`` was last told to do, word for word.

    This is the proof half of a delivery. Jarvis composes a brief, types it
    into a pane and says it did — and until this existed, the only evidence was
    the pane's own screen, which is exactly what is missing in every case worth
    checking: a pane that had parked its output, an emulator that never
    painted, a socket that reconnected a moment later, a CLI that redrew its
    input box without the text ever scrolling into view. "I sent it" and "you
    can read what I sent" are different claims, and only the second one can be
    verified by the person who has to trust it.

    Answers for a pane that has never been sent anything too, with an empty
    ``text`` and a null ``at`` — "nothing was sent here" is itself an answer,
    and a 404 would read as "no such pane".
    """
    term, _session = _pane_for_recap(name, workspace_id)
    return LastPrompt(
        name=term.name,
        text=term.last_prompt,
        chars=len(term.last_prompt),
        at=term.last_prompt_at,
        submitted=term.submitted,
        prompts_sent=term.prompts_sent,
    )


@router.get(
    "/terminals/{name}/prompts",
    response_model=PromptHistoryResponse,
    summary="Every prompt sent to one terminal",
)
async def get_prompt_history(name: str, workspace_id: str | None = None) -> PromptHistoryResponse:
    """Read every exact prompt handed to this pane, newest first.

    Prompt bodies live outside the workspace snapshot and are loaded only for
    this request. That keeps the IDE's state poll and first-screen restore small
    even when a workspace has many panes with long coding briefs.
    """
    term, _session = _pane_for_recap(name, workspace_id)
    stored = await asyncio.to_thread(prompt_history.load, term.history_id)
    entries = prompt_history.merged(stored, term.prompt_records)
    total = max(term.prompts_sent, len(entries))
    return PromptHistoryResponse(
        name=term.name,
        total=total,
        available=len(entries),
        complete=len(entries) >= term.prompts_sent,
        items=[
            PromptHistoryItem(
                id=entry.id,
                sequence=entry.sequence,
                text=entry.text,
                chars=len(entry.text),
                at=entry.at,
                submitted=entry.submitted,
            )
            for entry in reversed(entries)
        ],
    )


def _terminal_or_http_error(name: str) -> tuple[Session, Terminal]:
    """Resolve a globally unique pane name with consistent HTTP errors."""
    registry = get_registry()
    found = registry.find_terminal(name)
    if found is not None:
        return found
    if not registry.sessions:
        raise HTTPException(status_code=409, detail="No Agentic-IDE session is running.")
    known = ", ".join(t.name for s in registry.sessions for t in s.terminals) or "none"
    raise HTTPException(status_code=404, detail=f"No terminal called {name!r}. Running: {known}.")


@router.get("/terminals/{name}/report", summary="What one terminal is doing")
async def terminal_report(name: str, lines: int = 40) -> dict:
    """Status plus the recent readable output of the terminal called ``name``."""
    try:
        return get_registry().report(name, lines)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/terminals/{name}/conversation",
    summary="One terminal's conversation, as the CLI itself recorded it",
)
async def terminal_conversation(name: str) -> dict:
    """The turns of a pane's conversation — roles, prose and the steps between.

    Read from the coding CLI's OWN record rather than from the pane's screen. A
    TUI is a picture: it repaints in place, wraps to whatever width it had, and
    prints banners and spinners that mean nothing afterwards, so anything that
    reconstructs a conversation from it is guessing. The file the CLI writes to
    resume itself has none of those problems — roles are declared and tool calls
    carry their arguments (see :mod:`jarvis.agentic_ide.agent_transcript`).

    Two different "no", deliberately kept apart:

    * ``readable: false`` — this CLI keeps no record anyone can read. A settled
      fact about the product, so the surface can switch to the live pane and
      stay there.
    * ``readable: true, available: false`` — it does, but not yet: the file
      appears when the conversation starts, and one CLI only reveals its session
      id after the fact. Transient, so the surface WAITS rather than giving up.

    Collapsing the two is a real bug: a pane whose id has not been discovered
    yet would be treated as a CLI that can never be read, and a conversation
    that was seconds away would never be shown.
    """
    found = get_registry().find_terminal(name)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No terminal called {name}")
    _session, term = found
    readable = agent_transcript.can_read(term.agent)
    handle = term.resume
    if handle is None or not readable:
        return {
            "terminal": term.name,
            "agent": term.agent,
            "readable": readable,
            "available": False,
            "turns": [],
        }
    # Off the event loop: this opens a file that can be megabytes, and the loop
    # it would otherwise block is the one carrying every pane's output.
    turns = await asyncio.to_thread(
        agent_transcript.read,
        term.agent,
        handle.id,
        # The history to ask is the PANE's account: each subscription of the
        # same CLI keeps its conversations in its own directory, and the machine
        # default would hand back a different account's chat.
        home=account_home(term.agent, term.account),
    )
    if turns is None:
        # The reader exists and the pane has an id; the file simply is not there
        # yet. Still transient — `readable` stays true and the surface waits.
        return {
            "terminal": term.name,
            "agent": term.agent,
            "readable": True,
            "available": False,
            "turns": [],
        }
    return {
        "terminal": term.name,
        "agent": term.agent,
        "readable": True,
        "available": True,
        "turns": [t.to_dict() for t in turns],
    }


@router.get(
    "/voice-attachments",
    summary="List every file batch waiting for a spoken terminal prompt",
)
async def all_terminal_voice_attachments() -> dict:
    """Hydrate the persistent orb panel after navigation or a remount."""
    batches: list[dict] = []
    for session in get_registry().sessions:
        for term in session.terminals:
            async with term.pending_prompt_attachment_lock:
                batches.extend(
                    {
                        "terminal": term.name,
                        "batch_id": batch.batch_id,
                        "files": list(batch.files),
                        "reserved": (batch.batch_id in term.pending_prompt_attachment_reservations),
                    }
                    for batch in term.pending_prompt_attachment_batches
                )
    return {"batches": batches}


@router.get(
    "/terminals/{name}/voice-attachments",
    summary="List files waiting for a terminal's next spoken prompt",
)
async def terminal_voice_attachments(name: str) -> dict:
    """Return authoritative pending batches for the orb receipt."""
    found = get_registry().find_terminal(name)
    if found is None:
        # A closed pane owns no pending context. Returning an empty authoritative
        # list lets a preserved voice panel retire its old receipt cleanly.
        return {"terminal": name, "batches": []}
    _session, term = found
    async with term.pending_prompt_attachment_lock:
        batches = [
            {
                "batch_id": batch.batch_id,
                "files": list(batch.files),
                "reserved": (batch.batch_id in term.pending_prompt_attachment_reservations),
            }
            for batch in term.pending_prompt_attachment_batches
        ]
    return {"terminal": term.name, "batches": batches}


@router.delete(
    "/terminals/{name}/voice-attachments/{batch_id}",
    summary="Remove files waiting for a terminal's spoken prompt",
)
async def remove_terminal_voice_attachments(name: str, batch_id: str) -> dict:
    """Cancel one unconsumed orb drop; stored files remain in the workspace."""
    found = get_registry().find_terminal(name)
    if found is None:
        return {"terminal": name, "batch_id": batch_id, "removed": False}
    _session, term = found
    async with term.pending_prompt_attachment_lock:
        if batch_id in term.pending_prompt_attachment_reservations:
            raise HTTPException(
                status_code=409,
                detail="That attachment batch is already being added to a prompt.",
            )
        before = len(term.pending_prompt_attachment_batches)
        term.pending_prompt_attachment_batches[:] = [
            batch for batch in term.pending_prompt_attachment_batches if batch.batch_id != batch_id
        ]
        removed = len(term.pending_prompt_attachment_batches) != before
    return {"terminal": term.name, "batch_id": batch_id, "removed": removed}


@router.post("/terminals/{name}/attach", summary="Drop or paste files onto a terminal")
async def terminal_attach(
    name: str,
    files: list[UploadFile] | None = File(default=None),  # noqa: B008
    paths: str | None = Form(default=None),  # noqa: B008
    submit: bool = Form(default=False),  # noqa: B008
    note: str | None = Form(default=None),  # noqa: B008
    analyze: bool = Form(default=False),  # noqa: B008
    deliver: bool = Form(default=True),  # noqa: B008
    stage_for_voice: bool = Form(default=False),  # noqa: B008
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

    Two flags change what a drop is FOR, and the prompt bar uses both:

    * ``analyze=true`` reads the files' contents — a vision-capable model
      describes an image, a document has its text extracted — and returns that
      under ``analysis``. Hand it back to ``/terminals/{name}/prompt`` as
      ``attachments`` and the composed brief carries the picture's contents,
      which is what makes dropping a screenshot work against an agent that
      cannot open one.
    * ``deliver=false`` stores and analyses without typing anything into the
      pane, for a caller that is assembling a prompt rather than handing the
      agent a path right now. The files are on disk and referenced either way,
      so nothing is lost if the user then walks away.
    * ``stage_for_voice=true`` keeps the returned analyses on this pane until
      its next spoken prompt is composed. It requires ``deliver=false`` so one
      gesture cannot both type a loose path and attach the same file to a later
      brief.
    """
    if stage_for_voice and deliver:
        raise HTTPException(
            status_code=422,
            detail="Staging a voice-prompt attachment requires deliver=false.",
        )
    # Staging without analysis would preserve only a filename, which is exactly
    # what makes screenshots useless to text-only coding CLIs. Treat the staging
    # flag as the stronger request instead of relying on every client to send
    # both booleans forever.
    analyze = analyze or stage_for_voice
    registry = get_registry()
    # Resolved across every open workspace, not just the front one: call-signs
    # are unique across them, and a file dropped on a pane belongs to THAT
    # pane's folder — which is also where a copied file has to land.
    session, term = _terminal_or_http_error(name)

    references: list[str] = []
    stored_names: list[str] = []
    copied = 0
    # (name, bytes, reference) for everything that ends up attached — collected
    # only when the caller asked for an analysis, because reading a file the
    # agent could simply open itself is work nobody is waiting for. The
    # reference travels with the bytes so the description and the path the agent
    # opens can never drift apart.
    readable: list[tuple[str, bytes, str]] = []

    # 1. Paths that came with the drag. A path already inside the workspace is
    #    used as it lies; anything else is copied in.
    to_copy: list[tuple[str, bytes]] = []
    for raw in (paths or "").splitlines():
        candidate = raw.strip()
        if not candidate:
            continue
        inside = drops.within_workspace(candidate, session.folder)
        if inside is not None:
            reference = drops.reference(inside, agent=term.agent)
            references.append(reference)
            stored_names.append(Path(inside).name)
            if analyze:
                # Nothing is copied on this route, so the bytes have to be read
                # here to be described. A failure is not fatal: the reference
                # still ships, the file simply goes undescribed.
                try:
                    body = await asyncio.to_thread((Path(session.folder) / inside).read_bytes)
                except OSError as exc:
                    log.info("Agentic IDE attach: %r not readable for analysis (%s)", inside, exc)
                else:
                    readable.append((Path(inside).name, body, reference))
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

    # ``store`` silently skips empty entries, so they are dropped HERE instead —
    # that keeps ``stored[i]`` paired with ``to_copy[i]`` positionally. Pairing
    # by name would be wrong: two files that sanitize to the same name are
    # stored as two distinct files but would collapse into one key, and one drop
    # would be referenced twice while the other went missing.
    to_copy = [(name, data) for name, data in to_copy if data]

    if to_copy:
        try:
            stored = await asyncio.to_thread(drops.store, session.folder, to_copy)
        except drops.DropError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        copied = len(stored)
        for item, (_original, data) in zip(stored, to_copy, strict=True):
            reference = drops.reference(item.relative_path, agent=term.agent)
            references.append(reference)
            stored_names.append(item.name)
            if analyze:
                readable.append((item.name, data, reference))

    if not references:
        raise HTTPException(
            status_code=422,
            detail="That drop carried nothing this pane could use.",
        )

    analysis_items: list[drop_analysis.DropAnalysis] = []
    if analyze and readable:
        analysis_items = await _analyze_drops(readable)

    voice_batch: PendingPromptAttachmentBatch | None = None
    if stage_for_voice and analysis_items:
        try:
            voice_batch = await prompt_attachments.enqueue(term, analysis_items, stored_names)
        except prompt_attachments.PromptAttachmentQueueFull as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    analysis = [item.to_dict() for item in analysis_items]

    payload = " ".join(references)
    if note and note.strip():
        payload = f"{payload} {note.strip()}"

    delivered = False
    try:
        if not deliver:
            # Held deliberately: the caller is building a prompt around these
            # files and will send the whole thing itself.
            pass
        elif submit:
            await registry.send_prompt(term.name, payload)
            delivered = True
        else:
            # Typed, not submitted: the user still wants to say what to DO with
            # the file. A leading space keeps it off whatever they already typed.
            if not registry.write(term.key, f"{payload} "):
                raise HTTPException(
                    status_code=409,
                    detail=f"{term.name} is not accepting input right now.",
                )
            delivered = True
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "ok": True,
        "terminal": term.name,
        "references": references,
        "files": stored_names,
        "copied": copied,
        "submitted": bool(submit and delivered),
        "delivered": delivered,
        "analysis": analysis,
        "staged_for_voice": len(analysis_items) if stage_for_voice else 0,
        "voice_batch_id": voice_batch.batch_id if voice_batch else None,
    }


async def _analyze_drops(
    readable: list[tuple[str, bytes, str]],
) -> list[drop_analysis.DropAnalysis]:
    """Describe/extract the dropped files. Never raises — an analysis is a bonus.

    A drop that reached this point is already stored and referenced; the pane
    works with or without a description, so a provider outage here must cost the
    description and nothing else.
    """
    import mimetypes

    from jarvis.brain.drop_context import DroppedItem

    items = [
        (
            DroppedItem(
                name=name,
                mime=mimetypes.guess_type(name)[0] or "application/octet-stream",
                data=data,
            ),
            reference,
        )
        for name, data, reference in readable
    ]
    try:
        return await drop_analysis.analyze(items)
    except Exception as exc:  # noqa: BLE001 - the drop itself already succeeded
        log.info("Agentic IDE attach: analysis failed (%s)", exc)
        return []


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

    # A spawn for an instruction that JUST went to panes still open is a
    # duplicate, not a new order — the router brain re-issued a live 2026-08-11
    # order 37 s later because the first fleet's success was not yet visible in
    # its context, and four agents ended up racing on the same task in one
    # shared working tree. Checked before any pane opens so a refusal leaves
    # nothing behind. Re-briefing EXISTING panes stays allowed: those repeats
    # are visible to the user and cheap to make deliberately.
    if req.spawn and not req.dry_run and not req.force:
        from jarvis.agentic_ide import fanout as fanout_guard

        duplicate = fanout_guard.find_duplicate_fanout(registry.session, req.instruction)
        if duplicate is not None:
            age_s = int(max(0.0, time.monotonic() - duplicate.at))
            names = ", ".join(duplicate.terminals)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This instruction already went to {names} {age_s}s ago and "
                    "those panes are still open. Report that fleet instead of "
                    "spawning a second one; pass force=true only when the user "
                    "explicitly asked to run the same task again."
                ),
            )

    # Bound once, up here: the announcements at the end of this route apply to
    # every fan-out, and the fleet path that briefs EXISTING panes never enters
    # the spawn branch below.
    bus = getattr(request.app.state, "bus", None)

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
        session = registry.session
        if session is not None and bus is not None and created:
            try:
                await bus.publish(
                    terminals_added_event(session, created, source_layer="agentic_ide_routes")
                )
                # Bringing the workspace forward is not cosmetic here: a pane's
                # agent process starts when the VIEW mounts it, so a workspace
                # sitting behind another section never starts the panes this
                # call just opened — and briefing them would then be impossible
                # by construction.
                from jarvis.core.events import NavigateSidebar

                await bus.publish(
                    NavigateSidebar(section="agentic-ide", source_layer="agentic_ide_routes")
                )
            except Exception as exc:  # noqa: BLE001 - notification is not the work
                log.debug("AgenticIdeTerminalsAdded publish failed: %s", exc)

        if created and session is not None and bus is not None:
            # Wait for the panes to come up before typing into them. Without
            # this, "open one and brief it" opened the pane and briefed NOBODY:
            # a registry entry exists microseconds after the call, its agent
            # does not, and every delivery came back "not_running" (the known
            # limit this replaces). Bounded well under the command tool's HTTP
            # budget, and panes that never make it stay in `undelivered`.
            ready = await wait_for_prompt_ready(
                session,
                [t.name for t in created],
                timeout_s=SPAWN_READY_TIMEOUT_S,
            )
            late = [t.name for t in created if t.name not in ready]
            if late:
                log.info(
                    "Agentic IDE fan-out: panes not ready within %.0fs: %s",
                    SPAWN_READY_TIMEOUT_S,
                    ", ".join(late),
                )

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
    # One announcement per pane that was actually briefed — the fleet path is
    # where a spoken "prompt terminal two and three" lands, and it is exactly
    # the path where the user has no other way of knowing it happened: they are
    # talking, not looking at a prompt bar. See the same publish in
    # ``terminal_prompt`` for why the pane's own echo is not enough.
    if bus is not None and session is not None:
        for delivery in result.delivered:
            term = session.find(delivery.terminal)
            if term is None:
                continue
            try:
                await bus.publish(
                    prompt_sent_event(session, term, source_layer="agentic_ide_routes")
                )
            except Exception as exc:  # noqa: BLE001 - notification is not the work
                log.debug("AgenticIdePromptSent publish failed: %s", exc)
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


def _unknown_terminal_detail(registry: object, wanted: str) -> str:
    """The "no such pane" 404 for a prompt, with the move that RECOVERS it.

    Listing the running panes is not enough on its own. A caller that wanted to
    brief a pane called "Lee" reads a bare "not found" as "then open one" — and
    call-signs are POSITIONS handed out by the workspace, so the pane it opens
    is called something else entirely and the very same prompt 404s again. A
    live voice session on 2026-07-27 went round that loop four times and left
    four blank panes behind, without ever briefing anyone.

    So the error names the one move that works: brief a pane that exists, or
    open AND brief a new one in a single fan-out call, which returns the
    call-sign it was actually given.
    """
    running = ", ".join(
        t.name
        for s in registry.sessions
        for t in s.terminals  # type: ignore[attr-defined]
    )
    return (
        f"No terminal called {wanted!r}. Running: {running or 'none'}. "
        "Call-signs are the panes' positions (T1, T2, …), assigned by the "
        "workspace and cannot be requested, so opening another pane will NOT "
        "produce this name and retrying this "
        "prompt after a spawn fails the same way. Either prompt one of the "
        "panes listed above, or open and brief a new one in ONE call: "
        'POST /api/agentic-ide/fanout with spawn=[{"count": 1}] and the '
        "instruction."
    )


@router.post("/terminals/{name}/prompt", summary="Send a prompt to one terminal")
async def terminal_prompt(request: Request, name: str, req: PromptRequest) -> dict:
    """Type ``prompt`` into the terminal called ``name`` and press Enter.

    With ``compose=true`` the text is first rewritten into a prompt worth
    running — speech artefacts removed, the task stated as an imperative, and
    the relevant files of this workspace attached as ``@path`` references. Both
    the spoken path and the UI's typed prompt bar send with it on (maintainer
    decision 2026-08-12): typed or said, Jarvis writes the brief either way.

    ``dry_run=true`` returns the composed prompt without sending it, so a caller
    can show it for approval first.

    ``attachments`` carries files the user dropped with the instruction, already
    analysed by the attach endpoint. Their descriptions and extracted text go
    into the composition, which is the difference between an agent being told
    "fix the layout in the screenshot" and being told what the screenshot
    actually shows.
    """
    registry = get_registry()
    if not registry.sessions:
        raise HTTPException(status_code=409, detail="No Agentic-IDE session is running.")

    # Resolve the pane FIRST, so an unknown call-sign fails identically whether
    # or not composition was asked for. It used to 404 with composition on and
    # 409 with it off, which gave a caller two different-looking dead ends for
    # one cause — and neither of them said what to do about it.
    found = registry.find_terminal(name)
    if found is None:
        raise HTTPException(status_code=404, detail=_unknown_terminal_detail(registry, name))

    async def _compose_and_send() -> dict:
        text = req.prompt
        composed_by = "raw"
        files: list[str] = []
        attachments = [
            drop_analysis.DropAnalysis.from_dict(a.model_dump()) for a in req.attachments
        ]
        if req.compose:
            from jarvis.agentic_ide.prompt_composer import ComposeNotice, print_notice
            from jarvis.agentic_ide.prompt_composer import compose as compose_prompt

            # The pane decides which workspace composes the prompt. Composition
            # reads the codebase to attach `@file` references, and taking those
            # from the front workspace while sending to a pane in another one would
            # point the agent at files that are not in its tree.
            session, term_for_compose = found

            # Composition is 10-30 s of real model work, and the typed prompt
            # bar used to show a silent spinner for all of it. The composer's
            # own beats ride the app socket so every client can narrate the
            # wait; the stdout line stays, because a headless install watches
            # THAT. A dry run keeps the default sink — it is a preview, and
            # progress lines about a prompt nobody asked to send are noise.
            beat_bus = getattr(request.app.state, "bus", None)
            on_progress = None
            if beat_bus is not None and not req.dry_run:
                from jarvis.core.events import AgenticIdeComposeProgress

                def _publish_beat(notice: ComposeNotice) -> None:
                    print_notice(notice)
                    task = asyncio.get_running_loop().create_task(
                        beat_bus.publish(
                            AgenticIdeComposeProgress(
                                session_id=session.id,
                                terminal=notice.terminal or term_for_compose.name,
                                stage=notice.stage,
                                message=notice.message,
                                kind=notice.kind,
                                source_layer="agentic_ide_routes",
                            )
                        )
                    )
                    # Fire-and-forget: a beat that cannot be delivered must
                    # cost the line, never the brief (and never a warning
                    # about an unobserved task at teardown).
                    task.add_done_callback(
                        lambda done: done.cancelled() or done.exception()
                    )

                on_progress = _publish_beat

            result = await compose_prompt(
                req.prompt,
                session=session,
                terminal_name=term_for_compose.name,
                agent_display=AGENT_DISPLAY.get(term_for_compose.agent, term_for_compose.agent),
                attachments=attachments,
                on_progress=on_progress,
            )
            text, composed_by, files = result.text, result.composed_by, result.files
            if not text:
                raise HTTPException(
                    status_code=422, detail="The prompt was empty after composition."
                )
        elif attachments:
            # Attachments without composition still have to reach the agent. Silently
            # dropping them would be the worst reading of this request: the caller
            # said "here is what the user dropped" and the agent would receive a
            # sentence about a screenshot nobody described. The deterministic
            # renderer is the same one the no-provider path uses.
            from jarvis.agentic_ide import prompt_blueprint

            text = sanitize_prompt(
                prompt_blueprint.render_fallback(text, [], attachments),
                keep_newlines=True,
            )[:MAX_PROMPT_CHARS]
            composed_by = "fallback"

        if req.dry_run:
            return {
                "ok": True,
                "terminal": name,
                "sent": "",
                "composed": text,
                "composed_by": composed_by,
                "files": files,
                "attachments": [a.to_dict() for a in attachments],
                "dry_run": True,
            }

        try:
            term = await registry.send_prompt(name, text)
        except SessionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Say out loud that a prompt went in.
        #
        # Until now the ONLY sign was the agent echoing it back down that pane's own
        # terminal socket — one path, with no second opinion anywhere in the app. So
        # whenever that path was interrupted (a viewer that had lost its pane, a
        # window that was not looking, a socket in the middle of a slow reconnect)
        # the user was told the prompt had been sent and watched a screen where
        # nothing whatsoever happened, which reads as "it did nothing" (reported
        # 2026-07-28). This event is independent of the pane: it arrives on the app
        # socket every client already holds.
        bus = getattr(request.app.state, "bus", None)
        if bus is not None:
            try:
                await bus.publish(
                    prompt_sent_event(found[0], term, source_layer="agentic_ide_routes")
                )
            except Exception as exc:  # noqa: BLE001 - notification is not the work
                log.debug("AgenticIdePromptSent publish failed: %s", exc)

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

    # The delivery must survive its caller. This is the route a spoken "prompt
    # T1 …" executes through, and composition holds it for up to
    # COMPOSE_TIMEOUT_S — a voice hangup (or a dropped client) used to cancel
    # the handler mid-compose, so nothing was ever typed while the user
    # believed the pane was working (live 2026-08-06 18:52). A dry run has no
    # side effect worth protecting, and skipping the extra task keeps it cheap.
    if req.dry_run:
        return await _compose_and_send()
    work = asyncio.ensure_future(_compose_and_send())
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        if not work.cancelled():
            from jarvis.agentic_ide.fanout import note_detached_delivery

            note_detached_delivery(work, describe=f"the prompt for {name}")
        raise


# --------------------------------------------------------------------------- #
# PTY WebSocket                                                               #
# --------------------------------------------------------------------------- #
@router.websocket("/pty/{name}")
async def agentic_pty(ws: WebSocket, name: str) -> None:
    """Bidirectional bridge between one xterm pane and its agent's PTY.

    Wire protocol (JSON both ways) — client: ``{t:"i",d}`` input,
    ``{t:"r",cols,rows}`` resize, ``{t:"claim",cols,rows}`` foreground
    ownership; server: ``{t:"o",d}`` output, ``{t:"ready"}``,
    ``{t:"exit",code}``, ``{t:"error",message}``, ``{t:"size",cols,rows}``
    the geometry a REFUSED or clamped resize actually left the agent in (see
    ``report_geometry`` — a resize is a request, not an instruction).

    The optional ``workspace`` query parameter names the workspace this pane
    belongs to. It matters because several can be open and the front one changes
    while sockets are alive: without it a keystroke arriving mid-switch would be
    resolved against whichever workspace happens to be showing, and could land
    in a pane in a different folder. Omitted, the front workspace answers (the
    single-workspace case, and what older clients send).

    The optional ``appearance`` parameter (``light`` / ``dark``) is the ground
    this pane is drawn on. It is what the agent's CLI is told when it asks the
    terminal for its colours — a question answered in the backend rather than
    by xterm, so the reply cannot arrive after the CLI stopped waiting for it
    (see ``jarvis.agentic_ide.terminal_input``).
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
    appearance = (qp.get("appearance") or "").strip().lower() or None
    claim_owner = (qp.get("claim") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    registry = get_registry()
    send_lock = asyncio.Lock()

    async def on_output(text: str) -> None:
        async with send_lock:
            try:
                await ws.send_json({"t": "o", "d": text})
            except Exception:  # noqa: BLE001, S110 - viewer gone; transcript keeps filling
                pass

    async def on_replay(text: str) -> None:
        """The screen this pane is re-joining, as the bytes that drew it.

        Its own frame type rather than a large ``o``, because the viewer has to
        do something different with it: clear the terminal first. The same bytes
        appended to a screen that already holds a copy of that interface do not
        stack tidily — a TUI skips unchanged cells with cursor moves instead of
        overwriting them, so the two copies interleave character by character.
        See ``SessionRegistry._attach_locked``.

        A viewer that does not know this frame simply ignores it and comes back
        blank rather than scrambled, which is the better of the two failures and
        only reachable by a client older than this server.
        """
        async with send_lock:
            try:
                await ws.send_json({"t": "replay", "d": text})
            except Exception:  # noqa: BLE001, S110 - viewer gone; transcript keeps filling
                pass

    async def on_exit(code: int) -> None:
        async with send_lock:
            try:
                await ws.send_json({"t": "exit", "code": code})
            except Exception:  # noqa: BLE001, S110 - viewer gone
                pass

    async def report_geometry(requested: tuple[int, int]) -> None:
        """Tell the viewer the size its agent really got — but only if it lost.

        A resize is a REQUEST, and ``SessionRegistry.resize`` is allowed to turn
        it down: a tile under the viewer floor keeps the working geometry, a
        viewer that no longer holds the pane is ignored outright, and a pane
        stuck below the floor is lifted to it. The pane meanwhile reflowed its
        own xterm the moment it measured its tile, so a refusal used to leave the
        two grids permanently disagreeing — and a TUI addresses rows relatively,
        so its next repaint finishes into rows holding something else. That is
        the doubled, character-by-character text a narrow pane was showing
        (reported 2026-08-11); it looks like a rendering fault and is really two
        different screens in one grid.

        Sent ONLY when the granted size differs from the requested one. The
        normal path — the overwhelming majority of resizes — stays silent, so
        this adds no chatter to a dragged seam, and a viewer that never hears
        back can go on believing it was granted what it asked for, because it
        was. A client that does not know this frame ignores it and is no worse
        off than before.
        """
        granted = registry.pty_geometry(term.key, pane_workspace)
        if granted is None or granted == requested:
            return
        async with send_lock:
            try:
                await ws.send_json(
                    {"t": "size", "cols": granted[0], "rows": granted[1]}
                )
            except Exception:  # noqa: BLE001, S110 - viewer gone
                pass

    async def on_prompt(notice: dict) -> None:
        """This pane was just handed a prompt — tell the viewer outright.

        The alternative is what every earlier version relied on: let the user
        recognise the delivery in the agent's own output. That reliably fails
        in the states nobody can see from here — a parked pane, an emulator
        that has not painted, a socket that reconnected a moment ago, or a CLI
        that redraws its input box without the text ever scrolling into view.
        The user is then told the brief was sent and looks at a pane where
        nothing happened, which is indistinguishable from Jarvis making it up.

        Lossy on purpose: a viewer that is not connected in this instant misses
        it and picks the same receipt up from ``last_prompt_at`` on its next
        state read. This channel buys immediacy, not delivery.
        """
        async with send_lock:
            try:
                await ws.send_json({"t": "prompt", **notice})
            except Exception:  # noqa: BLE001, S110 - viewer gone; the state still carries it
                pass

    # Pin the workspace ONCE, before anything is attached. A client that sent no
    # id means "the one showing right now", and that has to be resolved here
    # rather than on every later message: the front workspace can change while
    # this socket is open, and a keystroke must keep going to the pane it was
    # typed into.
    pinned = registry.get(workspace_id)
    if workspace_id and pinned is None and registry.get(None) is not None:
        # **A pane whose workspace is gone must not land in a different one.**
        #
        # Call-signs are positional: every workspace numbers its panes from T1.
        # So a socket still holding a closed workspace's id used to resolve
        # against whatever is at the FRONT now and attach to a stranger's T2 —
        # in another folder, run by another agent. And because attaching makes
        # you the owner, it took that pane's output away from the window the
        # user was actually looking at, which then sat frozen until it was
        # reloaded (measured 2026-07-28: a leftover tab from before an app
        # restart quietly took over four live panes, and a prompt typed into
        # them appeared nowhere).
        #
        # Answering "that workspace is gone" instead lets the client do the one
        # correct thing: re-read the state and come back with the grid that
        # exists. Distinguished from the 4503 above on purpose — nothing is
        # coming back here, so waiting would be waiting for nothing.
        log.info(
            "Agentic IDE: %r asked for workspace %s, which is closed — telling it to re-read",
            name,
            workspace_id,
        )
        await ws.send_json(
            {
                "t": "error",
                "message": "That workspace was closed — reloading the terminals.",
            }
        )
        await ws.close(code=4409, reason="stale workspace")
        return
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
            on_replay=on_replay,
            claim_owner=claim_owner,
        )
    except SessionNotReady as exc:
        # "Not yet", not "not here": this pane connected while its workspace was
        # still being restored — the ordinary state of affairs for a second or
        # two after the app restarts, when every pane in a full grid reconnects
        # at once. Told "no such pane" (4404) they all gave up permanently and
        # the whole workspace came back frozen, so this says "try again" with a
        # code of its own and the pane keeps waiting.
        log.info(
            "Agentic IDE: %r connected before its workspace was open — asked it to wait",
            name,
        )
        await ws.send_json({"t": "error", "message": str(exc)})
        await ws.close(code=4503, reason="not ready")
        return
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
        # 4500, not 4404. The pane EXISTS — its agent refused to start. 4404 is
        # the client's code for "this pane is not in the open workspace", and it
        # acts on that: it replaces whatever reason was just sent with its own
        # "no longer part of the open workspace" line and asks the view to
        # re-read the grid. So the honest diagnosis one frame earlier ("… is not
        # configured yet — add its API key …") was painted over by a sentence
        # that was not true, and the user was told to go looking for a missing
        # pane instead of a missing key. A refusal has its own code now, and the
        # client keeps the reason that came with it.
        await ws.close(code=4500, reason="attach failed")
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
            # What this pane was last told to do, and when — handed over with
            # the handshake so a viewer that arrives AFTER the prompt (a
            # reload, a reconnect, a second window opened later) still shows
            # the receipt. Without it the proof would exist only for whoever
            # happened to be connected in that one instant, which is precisely
            # the user who did not need proving to.
            "last_prompt_at": term.last_prompt_at,
            "last_prompt_chars": len(term.last_prompt),
            "last_prompt_preview": term.last_prompt[:200],
            "submitted": term.submitted,
        }
    )

    # The handshake carries a size too, and ``attach`` is allowed to turn that
    # one down for the same reasons a later resize can be. Reported through the
    # same channel rather than as extra fields on ``ready``: a pane re-joining a
    # running agent is the MOST likely place to inherit a geometry it did not
    # ask for, and one reconciliation path is the only way the handshake and
    # every later resize cannot drift apart.
    await report_geometry((cols, rows))

    # Register only once the handshake is out. A notice arriving before the
    # pane knows its own name would be dropped by the client as belonging to
    # nothing, and the state read that follows would then be its first hint.
    term.prompt_viewers.append(on_prompt)

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
                # ``on_output`` identifies THIS socket. A pane open in two
                # places has two viewers and one pseudo-terminal, so a size
                # from the viewer that no longer holds the pane would set the
                # agent's screen to a window nobody is reading — and the viewer
                # that IS being read has no way to notice or correct it.
                want = (
                    _safe_int(msg.get("cols"), cols),
                    _safe_int(msg.get("rows"), rows),
                )
                registry.resize(term.key, *want, pane_workspace, viewer=on_output)
                await report_geometry(want)
            elif kind == "claim":
                want = (
                    _safe_int(msg.get("cols"), cols),
                    _safe_int(msg.get("rows"), rows),
                )
                registry.claim_viewer(
                    term.key, *want, pane_workspace, viewer=on_output
                )
                await report_geometry(want)
    finally:
        # The viewer went away — switched tab, reloaded, closed the browser.
        # None of those mean "stop working", so the agent keeps running and
        # only the viewer is released; the next viewer re-joins it. What stops
        # an agent is closing its workspace (DELETE /workspaces/{id}).
        #
        # ``on_output`` identifies WHICH viewer is leaving, and that is what
        # keeps a reload from blinding itself: the replacement socket for this
        # pane may already have taken the slot while this one was still closing,
        # and releasing it then would leave a live agent painting into nothing
        # (BUG-113).
        registry.detach(term.key, pane_workspace, viewer=on_output)
        # Unregistered by identity for the same reason: a pane open in two
        # windows has two notice channels, and removing "the last one" would
        # silence the window that is still watching. Guarded because the pane
        # may already have been torn down (closing a workspace clears these).
        try:
            term.prompt_viewers.remove(on_prompt)
        except ValueError:
            pass


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
            "'auto', 'subscription', 'api', or a specific brain provider id from the options list."
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


def _provider_label(provider: str) -> str:
    """A provider's own display name, falling back to its id.

    The picker must name the CLI the user actually connected. Reading it from
    the provider card rather than mapping ids here is what keeps that true for
    a CLI this file has never heard of (AP-21).
    """
    try:
        from .provider_spec import get_spec

        spec = get_spec(provider)
        return str(getattr(spec, "label", "") or provider) if spec else provider
    except Exception:  # noqa: BLE001 - a label is decoration, the id is the value
        return provider


def _tool_model_option() -> PromptWriterOption:
    """The "use the model I already picked" option, named so it is recognisable.

    The label carries the actual selection because that is the whole question
    the user is answering. "Tool Model" alone tells someone nothing about which
    model would write their briefs; "Tool Model (Gemini)" does, and an install
    with nothing pinned says so instead of offering a choice that cannot run.
    """
    try:
        from jarvis.brain.resolver import _tool_model_selection
        from jarvis.core.config import load_config

        provider, model = _tool_model_selection(load_config())
    except Exception:  # noqa: BLE001 - a settings page must still render
        log.info("prompt-writer: tool-model probe failed", exc_info=True)
        provider, model = "auto", None

    if provider == "auto":
        return PromptWriterOption(
            id="tool_model",
            label="Tool Model (none selected yet)",
            connected=False,
        )
    # Plain ASCII separator: this label is rendered in the desktop UI but also
    # printed by `jarvis api agentic-ide prompt-writer`, and a terminal's
    # encoding is not ours to assume (CLAUDE.md §5, Windows defaults to cp1252).
    detail = f"{_provider_label(provider)}"
    if model:
        detail = f"{detail} - {model}"
    return PromptWriterOption(
        id="tool_model",
        label=f"Tool Model ({detail})",
        connected=_tool_model_usable(provider),
    )


def _tool_model_usable(provider: str) -> bool:
    """Whether the pinned Tool Model could write right now.

    Checked without instantiating anything: a settings page asks this on every
    render, and the two things that actually block it — the provider not being
    in this build, and its credential being absent — are both cheap to read.
    """
    try:
        from jarvis.brain.app_control import is_credential_present
        from jarvis.brain.provider_registry import BrainProviderRegistry

        from .provider_spec import get_spec

        spec = get_spec(provider)
        if spec is None or provider not in set(BrainProviderRegistry().available()):
            return False
        return bool(is_credential_present(spec))
    except Exception:  # noqa: BLE001 - unknown means "do not promise it works"
        log.info("prompt-writer: tool-model usability probe failed", exc_info=True)
        return False


def _writer_options() -> list[PromptWriterOption]:
    candidates = _writer_candidates()
    options = [
        PromptWriterOption(
            id="auto",
            label="Automatic (a connected subscription, else the API model)",
            connected=True,
        ),
        _tool_model_option(),
        PromptWriterOption(
            id="subscription",
            label="Any connected coding CLI (never an API key)",
            connected=any(connected for _id, connected in candidates),
        ),
        PromptWriterOption(
            id="api",
            label="API model (billed per token)",
            connected=True,
        ),
    ]
    for provider, connected in candidates:
        options.append(
            PromptWriterOption(id=provider, label=_provider_label(provider), connected=connected)
        )
    return options


@router.get(
    "/prompt-writer",
    response_model=PromptWriterState,
    summary="Who writes Agentic-IDE task briefs",
)
async def prompt_writer_state() -> PromptWriterState:
    """Report the configured brief writer and every option, with live state."""
    return PromptWriterState(prompt_writer=_current_prompt_writer(), options=_writer_options())


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
        # Name the actual blocker. "Not signed in" is true of a coding CLI and
        # nonsense about a Tool Model, and a user sent to fix the wrong thing
        # gives up on the setting rather than on the diagnosis.
        detail = (
            "No usable Tool Model is configured. Pick one in the Tool Model "
            "settings — with its API key entered — then choose it here."
            if requested == "tool_model"
            else f"'{match.label}' is not signed in on this machine. Connect it "
            "first, or choose 'auto'."
        )
        raise HTTPException(status_code=409, detail=detail)
    _persist_prompt_writer(requested)
    # The composer answers "who writes" from a short-lived cache; a user who
    # just changed the answer must not have the next brief written by the old
    # one for the rest of the TTL.
    from jarvis.agentic_ide.writer import invalidate_writer_cache

    invalidate_writer_cache()
    return PromptWriterState(prompt_writer=requested, options=_writer_options())


__all__ = ["router"]
