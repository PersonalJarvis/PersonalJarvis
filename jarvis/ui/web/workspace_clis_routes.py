"""REST routes for the terminal CLIs the user added themselves.

The HTTP face of :mod:`jarvis.workspace.custom_clis`. Everything these routes
touch ends up in the same registry the shipped CLIs live in, so a CLI created
here is immediately offered by the setup wizard's terminal allocation, the
"open a terminal — what?" picker, the pane split menu and the voice catalog —
no restart, no second list to keep in step.

Mount alongside the other routers::

    from .workspace_clis_routes import router as workspace_clis_router
    app.include_router(workspace_clis_router)

**Why the write routes are marked dangerous.** A stored entry is a command line
this app will start in a terminal. That is exactly what the user is asking for
when they type it into the form themselves — and exactly what must not happen
because a model decided it would be helpful. The metadata keeps the generated
CLI and the tool broker asking first (CLAUDE.md §5, AP-14).
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from jarvis.workspace import custom_clis
from jarvis.workspace.agents import refresh_custom_agents

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace-clis", tags=["workspace-clis"])


class CustomCliModel(BaseModel):
    """One user-added CLI as the UI sees it."""

    id: str = Field(description="Stable registry key. Never changes on a rename.")
    display_name: str
    command: str = Field(description="The command line that starts this CLI.")
    description: str = ""
    file_reference: str = Field(
        default="quoted",
        description=(
            "How a dropped file is written into this CLI's prompt: 'at' for "
            "@path (most modern coding CLIs) or 'quoted' for \"path\"."
        ),
    )
    logo_url: str = Field(
        default="",
        description="Where to fetch this entry's mark; empty when it has none.",
    )
    runs_through_shell: bool = Field(
        default=False,
        description=(
            "True when the command is shell source (a pipeline, an environment "
            "assignment, two commands chained) and therefore starts inside a "
            "shell rather than as the pane's own process."
        ),
    )
    binary: str = Field(
        default="",
        description="The word that has to be on PATH for this command to start.",
    )


class CustomCliListResponse(BaseModel):
    clis: list[CustomCliModel]
    max_name_length: int = custom_clis.MAX_NAME_LEN
    max_command_length: int = custom_clis.MAX_COMMAND_LEN
    max_logo_bytes: int = custom_clis.MAX_LOGO_BYTES
    logo_extensions: list[str] = Field(
        default_factory=lambda: sorted(custom_clis.LOGO_TYPES)
    )


class CreateCustomCliRequest(BaseModel):
    display_name: str
    command: str
    description: str = ""
    file_reference: str = "quoted"


class UpdateCustomCliRequest(BaseModel):
    """Every field optional — an omitted one is left exactly as it was."""

    display_name: str | None = None
    command: str | None = None
    description: str | None = None
    file_reference: str | None = None


def _model(entry: custom_clis.CustomCli) -> CustomCliModel:
    return CustomCliModel(
        id=entry.id,
        display_name=entry.display_name,
        command=entry.command,
        description=entry.description,
        file_reference=entry.file_reference,
        logo_url=custom_clis.logo_url(entry),
        runs_through_shell=custom_clis.needs_shell(entry.command),
        binary=entry.binary,
    )


def _published(entry: custom_clis.CustomCli) -> CustomCliModel:
    """Return the entry AND make the registry aware of the change.

    Every write goes through here, because a stored entry that the registry has
    not re-read is one the user can see in the list and cannot open in a pane —
    which reads as the feature being broken rather than as a stale cache.
    """
    refresh_custom_agents()
    return _model(entry)


def _bad_request(exc: custom_clis.CustomCliError) -> HTTPException:
    """A validation complaint, phrased for the person who typed the form."""
    return HTTPException(status_code=422, detail=str(exc))


@router.get("", response_model=CustomCliListResponse, summary="List your own CLIs")
async def list_clis() -> CustomCliListResponse:
    """Every CLI the user added, plus the limits the form has to respect."""
    return CustomCliListResponse(
        clis=[_model(entry) for entry in custom_clis.list_custom_clis()]
    )


@router.post(
    "",
    response_model=CustomCliModel,
    summary="Add a CLI of your own",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def create_cli(req: CreateCustomCliRequest) -> CustomCliModel:
    """Store a new CLI and offer it everywhere a terminal can be opened."""
    try:
        entry = custom_clis.create_custom_cli(
            req.display_name,
            req.command,
            description=req.description,
            file_reference=req.file_reference,
        )
    except custom_clis.CustomCliError as exc:
        raise _bad_request(exc) from exc
    log.info("workspace-clis: added %r (%s)", entry.display_name, entry.id)
    return _published(entry)


@router.patch(
    "/{cli_id}",
    response_model=CustomCliModel,
    summary="Change one of your CLIs",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def update_cli(cli_id: str, req: UpdateCustomCliRequest) -> CustomCliModel:
    """Edit a stored CLI. Panes already running it keep the command they started
    with — a live process cannot be re-launched under it."""
    try:
        entry = custom_clis.update_custom_cli(
            cli_id,
            display_name=req.display_name,
            command=req.command,
            description=req.description,
            file_reference=req.file_reference,
        )
    except custom_clis.CustomCliError as exc:
        raise _bad_request(exc) from exc
    return _published(entry)


@router.delete(
    "/{cli_id}",
    summary="Remove one of your CLIs",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def delete_cli(cli_id: str) -> dict[str, Any]:
    """Forget a CLI and its logo. Open panes running it are left alone."""
    try:
        entry = custom_clis.delete_custom_cli(cli_id)
    except custom_clis.CustomCliError as exc:
        raise _bad_request(exc) from exc
    refresh_custom_agents()
    log.info("workspace-clis: removed %r (%s)", entry.display_name, entry.id)
    return {"ok": True, "id": entry.id, "display_name": entry.display_name}


@router.put(
    "/{cli_id}/logo",
    response_model=CustomCliModel,
    summary="Set a CLI's logo",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def upload_logo(
    cli_id: str, file: Annotated[UploadFile, File()]
) -> CustomCliModel:
    """Store an image as this CLI's mark in every picker.

    Read with a ceiling rather than whole: ``UploadFile`` will happily spool a
    multi-gigabyte body to disk before anything gets to reject it, so the size
    limit is applied to the read itself. One byte over the limit is enough to
    know, and the rest of the body is never touched.
    """
    limit = custom_clis.MAX_LOGO_BYTES
    data = await file.read(limit + 1)
    try:
        entry = custom_clis.set_logo(cli_id, data, file.filename or "")
    except custom_clis.CustomCliError as exc:
        raise _bad_request(exc) from exc
    return _published(entry)


@router.delete(
    "/{cli_id}/logo",
    response_model=CustomCliModel,
    summary="Remove a CLI's logo",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def remove_logo(cli_id: str) -> CustomCliModel:
    """Drop the logo; the picker falls back to the monogram it draws without one."""
    try:
        entry = custom_clis.clear_logo(cli_id)
    except custom_clis.CustomCliError as exc:
        raise _bad_request(exc) from exc
    return _published(entry)


@router.get("/{cli_id}/logo", summary="A CLI's logo")
async def get_logo(cli_id: str) -> FileResponse:
    """Serve the stored image.

    Two headers earn their place. ``Content-Security-Policy: sandbox`` because
    SVG is a document format: a mark fetched into an ``<img>`` cannot run its
    own script, but the same URL opened in a tab could, and the header closes
    that door for both. ``X-Content-Type-Options: nosniff`` so a browser that
    disagrees with our media type does not go looking for a better one.
    """
    found = custom_clis.logo_file(cli_id)
    if found is None:
        raise HTTPException(status_code=404, detail="This CLI has no logo.")
    path, media_type = found
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            # The picker asks for this on every render, and a logo the user
            # just replaced has to be the one they see.
            "Cache-Control": "no-cache",
        },
    )


__all__ = ["router"]
