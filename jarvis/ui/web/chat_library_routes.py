"""REST API for the project/chat library — the new chat surface's sidebar.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/chat-library/projects                       → every project
    POST   /api/chat-library/projects                       → open/create one
    PATCH  /api/chat-library/projects/{pid}                 → rename, pin, archive
    DELETE /api/chat-library/projects/{pid}                 → forget it and its chats
    GET    /api/chat-library/projects/{pid}/chats           → that project's chats
    POST   /api/chat-library/projects/{pid}/chats           → start a chat
    PATCH  /api/chat-library/projects/{pid}/chats/{tid}     → rename, archive, retarget
    DELETE /api/chat-library/projects/{pid}/chats/{tid}     → forget one chat

**Listing is deliberately two calls, not one.** A project's chats load when the
project is opened, never with the project list — the sidebar of somebody with
forty repos and a thousand conversations has to arrive in one small response,
and it does: the project list carries a COUNT per project, not the chats. That
is the whole reason :mod:`jarvis.agentic_ide.library` stores one file per
project.

Neither does any of this return message CONTENT. A chat row is a title, an
agent, a timestamp and a one-line preview; opening the conversation is a
separate call against the coding CLI's own transcript. So this router has no
Brain dependency, no session dependency and no filesystem cost beyond a few
small JSON reads — it works headless and on a fresh install with no keys at all
(CLAUDE.md §3).

``exists`` on a project is reported rather than acted on. A folder can be
missing because an external drive is unplugged or a network share is late, and
deleting somebody's chat history over a late mount is not a trade this feature
gets to make: the row says the folder is unreachable and stays.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.agentic_ide import library

router = APIRouter(prefix="/api/chat-library", tags=["chat-library"])


# --------------------------------------------------------------------------- #
# payloads
# --------------------------------------------------------------------------- #


class ProjectOut(BaseModel):
    """One project as the sidebar needs it."""

    id: str
    path: str
    name: str
    color: str | None = None
    pinned: bool = False
    archived: bool = False
    created_at: float = 0.0
    last_opened_at: float = 0.0
    #: Is the folder reachable on this machine right now? Reported, never acted
    #: on — see the module docstring.
    exists: bool = True
    #: Is this the holder for chats started without choosing a folder? There is
    #: at most one, and the sidebar lists its chats apart from the projects.
    scratch: bool = False
    #: How many live chats it holds. The COUNT travels with the list; the chats
    #: themselves do not.
    chats: int = 0


class ProjectsOut(BaseModel):
    projects: list[ProjectOut]


class OpenProjectIn(BaseModel):
    path: str = Field(..., min_length=1)
    name: str | None = None


class PatchProjectIn(BaseModel):
    name: str | None = None
    color: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ChatOut(BaseModel):
    """One chat row. Metadata only — never a message."""

    id: str
    project_id: str
    title: str = ""
    agent: str = ""
    model: str | None = None
    account: str | None = None
    #: The live pane this chat is attached to, if any. Null means the
    #: conversation exists but nothing is running — the normal resting state.
    terminal: str | None = None
    #: Can this chat be reopened in its CLI? False for a conversation the CLI
    #: never gave us a handle for; the UI offers a fresh start instead of a
    #: resume that would silently begin from nothing.
    resumable: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    archived: bool = False
    preview: str = ""
    prompts_sent: int = 0


class ChatsOut(BaseModel):
    chats: list[ChatOut]


class CreateChatIn(BaseModel):
    agent: str = Field(..., min_length=1)
    model: str | None = None
    account: str | None = None
    title: str = ""


class PatchChatIn(BaseModel):
    title: str | None = None
    archived: bool | None = None
    model: str | None = None
    account: str | None = None
    #: Which live pane this chat is attached to. Written by the surface once a
    #: prompt has actually started an agent, so reopening the chat finds the
    #: terminal that already holds the conversation instead of starting a
    #: second one beside it.
    terminal: str | None = None


class RemovedOut(BaseModel):
    removed: bool


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _folder_exists(path: str) -> bool:
    """Is the project's folder reachable? Any OS complaint reads as "no".

    A permission error, a dead network share and a genuinely missing directory
    all mean the same thing to the caller — the folder cannot be opened right
    now — so they are not distinguished here.
    """
    try:
        return Path(path).is_dir()
    except OSError:
        # Silent on purpose: this IS the answer, not a swallowed failure — the
        # caller asked whether the folder is reachable and gets "no". It runs
        # per project on every listing, so logging would be pure noise.
        return False


def _project_out(project: library.Project) -> ProjectOut:
    return ProjectOut(
        **project.to_dict(),
        exists=_folder_exists(project.path),
        chats=len(library.list_threads(project.id)),
    )


def _chat_out(thread: library.Thread) -> ChatOut:
    data: dict[str, Any] = thread.to_dict()
    resume = data.pop("resume", None)
    return ChatOut(**data, resumable=bool(resume))


def _require_project(project_id: str) -> library.Project:
    project = library.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="No such project")
    return project


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #


@router.get("/projects", response_model=ProjectsOut, summary="Every project and its chat count")
def list_projects(include_archived: bool = False) -> ProjectsOut:
    return ProjectsOut(
        projects=[_project_out(p) for p in library.list_projects(include_archived=include_archived)]
    )


@router.post("/projects", response_model=ProjectOut, summary="Open a folder as a project")
def open_project(body: OpenProjectIn) -> ProjectOut:
    """Idempotent by folder: opening the same repo again returns the same project.

    Deliberately does NOT check that the folder exists. Registering a project on
    a drive that is currently unplugged is a reasonable thing to do, and the
    response says so via ``exists`` — refusing here would make the library
    disagree with itself, since a project whose folder vanishes later is kept.
    """
    project = library.ensure_project(body.path, name=body.name)
    return _project_out(project)


@router.post(
    "/projects/scratch",
    response_model=ProjectOut,
    summary="The holder for chats started without a folder",
)
def open_scratch() -> ProjectOut:
    """Idempotent: there is exactly one of these, and this is how it is reached.

    A chat still needs somewhere to run — a coding CLI is a process with a
    working directory — so this returns a real project rooted at the home
    folder. What makes it different is only that nobody picked it, which is why
    the sidebar lists its chats on their own instead of among the projects.
    """
    return _project_out(library.ensure_scratch())


@router.patch(
    "/projects/{project_id}", response_model=ProjectOut, summary="Rename, pin or archive a project"
)
def patch_project(project_id: str, body: PatchProjectIn) -> ProjectOut:
    updated = library.update_project(
        project_id,
        name=body.name,
        color=body.color,
        pinned=body.pinned,
        archived=body.archived,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="No such project")
    return _project_out(updated)


@router.delete(
    "/projects/{project_id}",
    response_model=RemovedOut,
    summary="Forget a project and every chat in it",
    # Destructive and not undoable: the chat list goes with the project. The
    # folder and the coding CLIs' own conversations are untouched.
    openapi_extra={"x-jarvis-dangerous": True},
)
def delete_project(project_id: str) -> RemovedOut:
    return RemovedOut(removed=library.delete_project(project_id))


# --------------------------------------------------------------------------- #
# chats
# --------------------------------------------------------------------------- #


@router.get(
    "/projects/{project_id}/chats",
    response_model=ChatsOut,
    summary="One project's chats, newest first",
)
def list_chats(project_id: str, include_archived: bool = False) -> ChatsOut:
    _require_project(project_id)
    return ChatsOut(
        chats=[
            _chat_out(t)
            for t in library.list_threads(project_id, include_archived=include_archived)
        ]
    )


@router.post(
    "/projects/{project_id}/chats", response_model=ChatOut, summary="Start a chat in a project"
)
def create_chat(project_id: str, body: CreateChatIn) -> ChatOut:
    """A new chat starts untitled and unattached.

    Nothing is launched here — no pane, no process, no tokens. A chat becomes
    live when the first prompt is sent, which is what makes creating one free
    and makes an accidental one visibly a blank rather than a running agent.
    """
    _require_project(project_id)
    library.touch_project(project_id)
    thread = library.create_thread(
        project_id,
        agent=body.agent,
        model=body.model,
        account=body.account,
        title=body.title,
    )
    return _chat_out(thread)


@router.patch(
    "/projects/{project_id}/chats/{chat_id}",
    response_model=ChatOut,
    summary="Rename or archive a chat",
)
def patch_chat(project_id: str, chat_id: str, body: PatchChatIn) -> ChatOut:
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        existing = library.get_thread(project_id, chat_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="No such chat")
        return _chat_out(existing)
    updated = library.update_thread(project_id, chat_id, **changes)
    if updated is None:
        raise HTTPException(status_code=404, detail="No such chat")
    return _chat_out(updated)


@router.delete(
    "/projects/{project_id}/chats/{chat_id}",
    response_model=RemovedOut,
    summary="Forget one chat",
    # The library entry goes; the coding CLI's own conversation on disk stays.
    # It is that CLI's data, written before Jarvis was involved.
    openapi_extra={"x-jarvis-dangerous": True},
)
def delete_chat(project_id: str, chat_id: str) -> RemovedOut:
    return RemovedOut(removed=library.delete_thread(project_id, chat_id))
