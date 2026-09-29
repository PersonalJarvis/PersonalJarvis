"""REST routes for the Agentic IDE's Git panel.

Endpoints (all under ``/api/agentic-ide/git``)::

    GET  /inspect             Branch, changes, ahead/behind, branches, worktrees
    GET  /overview            A workspace's branches with merged-into, PR and CI state
    POST /prepare             Git half of opening a workspace or an agent
    POST /commit              Stage everything and commit
    POST /push                Push the current branch (sets upstream once)
    POST /fetch               Fetch every remote
    POST /pull-request        Open a PR with the GitHub CLI
    POST /merge               Merge a branch back into its target
    POST /worktrees/remove    Remove a linked worktree (asks again when dirty)
    POST /worktrees/prune     Forget worktrees deleted by hand

Every route takes the folder it works on — a workspace folder, a project path
or a worktree — and runs git in the anyio threadpool (plain ``def`` handlers),
never on the event loop. Loopback-only like the rest of the IDE API.

A failed git call answers 422 with ``{"detail": {"message", "code"}}`` so the
UI can offer the follow-up it has for that code (``dirty`` → "remove anyway?").
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from jarvis.agentic_ide import git_ops, git_overview
from jarvis.agentic_ide.git_ops import GitError, PrepareMode
from jarvis.agentic_ide.session import get_registry

router = APIRouter(prefix="/api/agentic-ide/git", tags=["agentic-ide-git"])


def _fail(exc: GitError) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": str(exc), "code": exc.code})


class FolderRequest(BaseModel):
    folder: str = Field(..., min_length=1, description="Folder inside the repository")


class PrepareRequest(FolderRequest):
    mode: PrepareMode = "current"
    branch: str = Field("", max_length=200)
    base: str = Field("", max_length=400, description="Base branch/commit, or worktree path")


class CommitRequest(FolderRequest):
    message: str = Field(..., min_length=1, max_length=5000)


class PullRequestRequest(FolderRequest):
    title: str = Field("", max_length=300)
    draft: bool = False


class MergeRequest(FolderRequest):
    into: str = Field("", max_length=200, description="Target branch; default branch if empty")


class RemoveWorktreeRequest(FolderRequest):
    worktree: str = Field(..., min_length=1)
    force: bool = False
    delete_branch: bool = False


@router.get("/inspect", summary="Git status, branches and worktrees of a folder")
def inspect_folder(folder: str = Query(..., min_length=1)) -> dict:
    try:
        return git_ops.inspect(folder).to_dict()
    except GitError as exc:
        raise _fail(exc) from exc


@router.get("/overview", summary="Branches of a workspace's repository with PR and CI state")
def workspace_overview(
    workspace_id: str = Query(..., min_length=1),
    refresh: bool = Query(False, description="Re-read GitHub now instead of the cached answer"),
) -> dict:
    """What the IDE's Git tab lists: every branch, what it is merged into, its
    pull requests and its CI — local git always, GitHub through ``gh`` when it
    is installed and signed in (cached per repository, see ``git_overview``).

    Workspace-scoped on purpose: the folder comes from the open workspace, so
    this cannot be pointed at an arbitrary path on the machine.
    """
    session = get_registry().get(workspace_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return git_overview.overview(session.folder, refresh=refresh).to_dict()


@router.post("/prepare", summary="Prepare a checkout for a new workspace or agent")
def prepare_checkout(req: PrepareRequest) -> dict:
    try:
        return git_ops.prepare(req.folder, req.mode, branch=req.branch, base=req.base).to_dict()
    except GitError as exc:
        raise _fail(exc) from exc


@router.post("/commit", summary="Stage every change and commit it")
def commit_changes(req: CommitRequest) -> dict:
    try:
        sha = git_ops.commit_all(req.folder, req.message)
    except GitError as exc:
        raise _fail(exc) from exc
    return {"ok": True, "commit": sha}


@router.post("/push", summary="Push the current branch")
def push_branch(req: FolderRequest) -> dict:
    try:
        upstream = git_ops.push(req.folder)
    except GitError as exc:
        raise _fail(exc) from exc
    return {"ok": True, "upstream": upstream}


@router.post("/fetch", summary="Fetch every remote")
def fetch_remotes(req: FolderRequest) -> dict:
    try:
        git_ops.fetch(req.folder)
    except GitError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


@router.post("/pull-request", summary="Open a pull request for the current branch")
def open_pull_request(req: PullRequestRequest) -> dict:
    try:
        url = git_ops.create_pull_request(req.folder, title=req.title, draft=req.draft)
    except GitError as exc:
        raise _fail(exc) from exc
    return {"ok": True, "url": url}


@router.post("/merge", summary="Merge the current branch back into its target")
def merge_branch(req: MergeRequest) -> dict:
    try:
        sha = git_ops.merge_back(req.folder, into=req.into)
    except GitError as exc:
        raise _fail(exc) from exc
    return {"ok": True, "commit": sha}


@router.post(
    "/worktrees/remove",
    summary="Remove a linked git worktree",
    openapi_extra={"x-jarvis-dangerous": True},
)
def remove_worktree(req: RemoveWorktreeRequest) -> dict:
    open_folders = [session.folder for session in get_registry().sessions]
    if git_ops.worktree_in_use(req.worktree, open_folders):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "A workspace is still open in this worktree. Close it first.",
                "code": "in_use",
            },
        )
    try:
        summary = git_ops.remove_worktree(
            req.folder, req.worktree, force=req.force, delete_branch=req.delete_branch
        )
    except GitError as exc:
        raise _fail(exc) from exc
    return {"ok": True, "message": summary}


@router.post("/worktrees/prune", summary="Forget worktrees whose folders are gone")
def prune_worktrees(req: FolderRequest) -> dict:
    try:
        git_ops.prune_worktrees(req.folder)
    except GitError as exc:
        raise _fail(exc) from exc
    return {"ok": True}
