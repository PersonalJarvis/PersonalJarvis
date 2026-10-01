"""REST API for the project/chat library — the new chat surface's sidebar.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/chat-library/projects                       → every project
    POST   /api/chat-library/projects                       → open/create one
    PUT    /api/chat-library/projects/order                 → reorder projects via drag and drop
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
(AGENTS.md §3).

``exists`` on a project is reported rather than acted on. A folder can be
missing because an external drive is unplugged or a network share is late, and
deleting somebody's chat history over a late mount is not a trade this feature
gets to make: the row says the folder is unreachable and stays.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.agentic_ide import library, resume_store

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
    #: Manual sidebar position, set by drag and drop. 0.0 until arranged.
    position: float = 0.0
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


class ProjectOrderIn(BaseModel):
    project_ids: list[str] = Field(
        description="Visible project ids front to back, in sidebar order.",
    )


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


@router.put("/projects/order", response_model=ProjectsOut, summary="Reorder projects")
def reorder_projects(body: ProjectOrderIn) -> ProjectsOut:
    """Persist a drag-and-drop sidebar order. Nothing is renamed or moved on disk."""
    try:
        ordered = library.reorder_projects(body.project_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProjectsOut(projects=[_project_out(p) for p in ordered])


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
    # The IDE sidebar re-derives a project row from every remembered workspace
    # in its folder, so the remembered ones have to go too or the project comes
    # straight back. Open workspaces are the caller's to close first.
    removed = library.delete_project(project_id)
    forgotten = resume_store.forget(project_id=project_id)
    return RemovedOut(removed=removed or forgotten > 0)


class RevealedOut(BaseModel):
    opened: bool


def _project_folder(project_id: str) -> str | None:
    """The folder of a project, including one known only from its workspaces.

    Older installs derive a project row from a remembered workspace without a
    library entry, and the sidebar offers the same actions on both kinds.
    """
    project = library.get_project(project_id)
    if project is not None:
        return project.path
    from jarvis.agentic_ide import workspace_catalog
    from jarvis.agentic_ide.session import get_registry

    for entry in workspace_catalog.project_graph(get_registry())["projects"]:
        if entry["id"] == project_id:
            return str(entry["path"])
    return None


@router.post(
    "/projects/{project_id}/reveal",
    response_model=RevealedOut,
    summary="Open a project's folder in the file manager",
)
def reveal_project(request: Request, project_id: str) -> RevealedOut:
    """Open the project's folder in Explorer, Finder or the Linux file manager.

    Desktop-only: on a headless host the folder is on the server, not in front
    of the user, so the route 404s there like the other native file actions.
    The path comes from the stored project, never from the client.
    """
    folder = _require_folder(request, project_id)
    from jarvis.platform.open_path import open_file

    return RevealedOut(opened=open_file(Path(folder)))


class LauncherOut(BaseModel):
    id: str
    label: str


class LaunchersOut(BaseModel):
    """Where this project can be opened right now, so the menu offers only those."""

    file_manager: bool
    editors: list[LauncherOut]
    remote_url: str | None = None
    remote_label: str | None = None


class OpenInIn(BaseModel):
    target: str = Field(
        min_length=1,
        max_length=40,
        description='An editor id from ``/launchers`` or ``"remote"``.',
    )


def _require_folder(request: Request, project_id: str) -> str:
    if not bool(getattr(request.app.state, "native_file_actions", False)):
        raise HTTPException(status_code=404, detail="native-file-actions-disabled")
    folder = _project_folder(project_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="No such project")
    if not _folder_exists(folder):
        raise HTTPException(status_code=404, detail="The project folder is not reachable.")
    return folder


@router.get(
    "/projects/{project_id}/launchers",
    response_model=LaunchersOut,
    summary="Editors and web pages a project's folder can be opened in",
)
def project_launchers(request: Request, project_id: str) -> LaunchersOut:
    """Installed code editors, and the folder's hosted git remote if it has one.

    Asked when the sidebar's menu opens, so the menu shows "Open in Cursor" only
    where Cursor is installed and "Open on GitHub" only where there is a GitHub
    remote. A headless host has no local apps: every list comes back empty.
    """
    if not bool(getattr(request.app.state, "native_file_actions", False)):
        return LaunchersOut(file_manager=False, editors=[])
    folder = _project_folder(project_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="No such project")
    if not _folder_exists(folder):
        return LaunchersOut(file_manager=False, editors=[])
    from jarvis.agentic_ide import project_links
    from jarvis.ui.web import outputs_routes

    editor_ids = {oid for oid, _ in outputs_routes._OPENER_EDITORS}
    editors = [
        LauncherOut(id=entry["id"], label=entry["label"])
        for entry in outputs_routes._available_openers()
        if entry["id"] in editor_ids
    ]
    remote = project_links.remote_web_url(folder)
    return LaunchersOut(
        file_manager=True,
        editors=editors,
        remote_url=remote,
        remote_label=project_links.host_label(remote) if remote else None,
    )


@router.post(
    "/projects/{project_id}/open-in",
    response_model=RevealedOut,
    summary="Open a project's folder in an editor or its web page",
)
def open_project_in(request: Request, project_id: str, body: OpenInIn) -> RevealedOut:
    """Open the folder in an installed editor, or the remote's page in the browser.

    ``target`` is a closed id — never a path or a URL from the client — so this
    cannot launch an arbitrary program or page.
    """
    folder = _require_folder(request, project_id)
    from jarvis.agentic_ide import project_links

    if body.target == "remote":
        url = project_links.remote_web_url(folder)
        if url is None:
            raise HTTPException(status_code=404, detail="This folder has no hosted git remote.")
        import webbrowser

        return RevealedOut(opened=webbrowser.open(url))
    from jarvis.platform.open_path import open_file_with
    from jarvis.ui.web import outputs_routes

    if body.target not in {oid for oid, _ in outputs_routes._OPENER_EDITORS}:
        raise HTTPException(status_code=400, detail="Unknown editor.")
    resolved = outputs_routes._resolve_opener(body.target)
    if resolved is None:
        raise HTTPException(status_code=409, detail="That editor is not installed.")
    kind, value = resolved
    return RevealedOut(opened=open_file_with(Path(folder), kind, value))


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
