"""Authenticated Ultra Agent Swarm control, scoped inspection and bounded replay."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket
from pydantic import BaseModel, ConfigDict, Field
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import FileResponse, StreamingResponse

from jarvis.core.protocols import (
    PreparationAnswers,
    PreparationBegin,
    PreparationLaunch,
    PreparationView,
)
from jarvis.core.swarm_reputation import AgentSkills, ContributionRecheck
from jarvis.core.swarm_types import TeamCreate
from jarvis.swarm.settings import DistributedSetup

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/swarm", tags=["swarm"])
_DANGEROUS = {"x-jarvis-dangerous": True}


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int | None = Field(default=None, ge=1)
    expected_storage_generation: str | None = Field(default=None, max_length=64)


class PublishBody(ControlBody):
    artifact_id: str = Field(min_length=1, max_length=100)
    destination: Literal["artifact"] = "artifact"
    request_key: str = Field(min_length=1, max_length=100)


class BackupBody(ControlBody):
    request_key: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=100)


class DeleteBody(BackupBody):
    confirm_team_id: str = Field(min_length=32, max_length=32)


class RetentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    before_days: int = Field(default=30, ge=1, le=36500)


class _BackupMultipartParser(MultiPartParser):
    """Bound per-part header memory and close spool files on transport cancellation."""

    def on_part_begin(self) -> None:
        self._backup_header_bytes = 0
        super().on_part_begin()

    def _count_header(self, size: int) -> None:
        self._backup_header_bytes += size
        if self._backup_header_bytes > 8192:
            raise MultiPartException("Backup multipart header exceeds its limit")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._count_header(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._count_header(end - start)
        super().on_header_value(data, start, end)

    async def parse(self):
        try:
            return await super().parse()
        except BaseException:
            # Starlette cleans up parse errors; transport cancellation must own
            # the same already-created spooled files until they are closed.
            for stream in self._files_to_close_on_error:
                stream.close()
            raise


class SpecialistBody(ControlBody):
    source_agent_id: str = Field(min_length=1, max_length=100)
    authorized_input: str = Field(default="", max_length=16000)
    request_key: str = Field(min_length=1, max_length=100)


class RecheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=100)


@router.get(
    "/teams/{team_id}/agents/{agent_id}/skills",
    response_model=AgentSkills,
    summary="Inspect measured domain skills and attributable progression",
)
async def get_swarm_agent_skills(
    request: Request,
    team_id: str,
    agent_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).skills(team_id, agent_id, limit, offset))


@router.post(
    "/teams/{team_id}/tasks/{task_id}/recheck",
    response_model=ContributionRecheck,
    summary="Recheck an accepted contribution against its original deterministic proof",
    openapi_extra=_DANGEROUS,
)
async def recheck_swarm_contribution(
    request: Request, team_id: str, task_id: str, body: RecheckBody
) -> dict[str, Any]:
    return await _call(
        (await _service(request.app.state)).recheck(team_id, task_id, body.request_key)
    )


@router.get("/requests", summary="Review pending specialist Swarm proposals")
async def list_swarm_requests(
    request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[dict[str, Any]]:
    return await _call((await _service(request.app.state)).list_requests(limit, offset))


@router.post(
    "/requests/{request_id}/approve",
    summary="Approve a specialist's bounded team brief",
    openapi_extra=_DANGEROUS,
)
async def approve_swarm_request(
    request: Request, request_id: str, body: TeamCreate
) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).approve_request(request_id, body))


@router.post(
    "/requests/{request_id}/reject",
    summary="Reject a pending Swarm proposal",
    openapi_extra=_DANGEROUS,
)
async def reject_swarm_request(request: Request, request_id: str) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).reject_request(request_id))


@router.post(
    "/teams/{team_id}/specialists",
    summary="Assign an explicitly selected specialist",
    openapi_extra=_DANGEROUS,
)
async def assign_swarm_specialist(
    request: Request, team_id: str, body: SpecialistBody
) -> dict[str, Any]:
    return await _call(
        (await _service(request.app.state)).assign_specialist(
            team_id,
            body.source_agent_id,
            body.authorized_input,
            body.request_key,
            body.expected_version,
            body.expected_storage_generation,
        )
    )


@router.delete(
    "/teams/{team_id}/specialists/{agent_id}",
    summary="Revoke specialist membership without changing personal records",
    openapi_extra=_DANGEROUS,
)
async def revoke_swarm_specialist(request: Request, team_id: str, agent_id: str) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).revoke_specialist(team_id, agent_id))


async def _service(state: Any) -> Any:
    service = getattr(state, "swarm", None)
    if service is None:
        factory = getattr(state, "swarm_factory", None)
        if not callable(factory):
            raise HTTPException(503, "Swarm runtime is unavailable")
        service = factory()
        state.swarm = service
    await service.start()
    return service


async def _call(awaitable: Any) -> Any:
    try:
        return await awaitable
    except PermissionError as exc:
        raise HTTPException(403, "This Swarm resource is not accessible") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)[:300]) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)[:300]) from exc


@router.get("/capabilities", summary="Inspect local and distributed Swarm readiness")
async def swarm_capabilities(request: Request) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).capabilities())


@router.get("/teams", summary="List independent Swarm teams and retained runs")
async def list_swarm_teams(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    return await _call((await _service(request.app.state)).list_teams(limit=limit, offset=offset))


@router.get(
    "/distributed-config",
    summary="Inspect optional distributed setup without revealing credentials",
)
async def get_distributed_setup(request: Request) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).distributed_config())


@router.put(
    "/distributed-config",
    summary="Configure optional distributed services",
    openapi_extra=_DANGEROUS,
)
async def set_distributed_setup(request: Request, body: DistributedSetup) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).configure_distributed(body))


@router.post("/teams", summary="Create an explicitly opted-in Swarm team", openapi_extra=_DANGEROUS)
async def create_swarm_team(request: Request, body: TeamCreate) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).create_team(body))


@router.post(
    "/preparations",
    response_model=PreparationView,
    summary="Clarify a goal before launching its Swarm",
    openapi_extra=_DANGEROUS,
)
async def create_swarm_preparation(request: Request, body: TeamCreate) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).create_preparation(body))


@router.get(
    "/teams/{team_id}/preparation",
    response_model=PreparationView,
    summary="Read the saved questions and plan awaiting approval",
)
async def get_swarm_preparation(request: Request, team_id: str) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).preparation(team_id))


@router.post(
    "/teams/{team_id}/preparation",
    response_model=PreparationView,
    summary="Begin clarification for an unlaunched empty team",
    openapi_extra=_DANGEROUS,
)
async def begin_swarm_preparation(
    request: Request, team_id: str, body: PreparationBegin
) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).begin_preparation(team_id, body))


@router.post(
    "/teams/{team_id}/preparation/answers",
    response_model=PreparationView,
    summary="Turn clarification answers into a reviewable plan",
    openapi_extra=_DANGEROUS,
)
async def answer_swarm_preparation(
    request: Request, team_id: str, body: PreparationAnswers
) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).answer_preparation(team_id, body))


@router.post(
    "/teams/{team_id}/launch",
    response_model=PreparationView,
    summary="Launch the exact stored plan approved by the owner",
    openapi_extra=_DANGEROUS,
)
async def launch_swarm_preparation(
    request: Request, team_id: str, body: PreparationLaunch
) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).launch_preparation(team_id, body))


@router.get("/teams/{team_id}", summary="Inspect a Swarm's persistent identity and limits")
async def get_swarm_team(request: Request, team_id: str) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).team(team_id))


async def control_swarm_team(
    request: Request,
    team_id: str,
    action: Literal["start", "pause", "resume", "stop", "cancel", "archive", "recover", "backup"],
    body: ControlBody | None = None,
) -> dict[str, Any]:
    service = await _service(request.app.state)
    if action == "recover":
        return await _call(service.recover_team(team_id))
    if action == "backup":
        return await _call(service.backup(team_id))
    return await _call(
        service.control(
            team_id,
            action,
            expected_version=body.expected_version if body else None,
            expected_storage_generation=body.expected_storage_generation if body else None,
        )
    )


@router.get("/teams/{team_id}/world", summary="Read this Swarm's bounded durable world projection")
async def get_swarm_world(
    request: Request,
    team_id: str,
    group: str = Query("", max_length=100),
) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).world(team_id, group=group))


@router.get("/teams/{team_id}/storage", summary="Inspect owned Swarm backups and storage limits")
async def get_swarm_storage(request: Request, team_id: str) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).storage_status(team_id))


@router.post(
    "/teams/{team_id}/backup",
    summary="Export a team with its selected publications",
    openapi_extra=_DANGEROUS,
)
async def export_swarm_backup(
    request: Request, team_id: str, body: BackupBody | None = None
) -> dict[str, Any]:
    return await _call(
        (await _service(request.app.state)).backup(
            team_id, body.request_key if body else None, body.expected_version if body else None
        )
    )


@router.get(
    "/teams/{team_id}/backups/{backup_id}", summary="Download one owned portable Swarm backup"
)
async def download_swarm_backup(request: Request, team_id: str, backup_id: str) -> FileResponse:
    path = await _call((await _service(request.app.state)).backup_file(team_id, backup_id))
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"swarm-{team_id}-{backup_id}.zip",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
    )


@router.get("/restores", summary="Find owned interrupted imports before a team is visible")
async def list_swarm_restores(request: Request) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).pending_restores())


@router.post(
    "/restores",
    summary="Restore an uploaded Swarm backup with optional confirmed replacement",
    openapi_extra={
        **_DANGEROUS,
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file", "request_key"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "request_key": {"type": "string", "maxLength": 100},
                            "replace_team_id": {"type": "string", "maxLength": 32},
                        },
                    }
                }
            },
        },
    },
)
async def restore_swarm_backup(request: Request) -> dict[str, Any]:
    slots = getattr(request.app.state, "swarm_restore_upload_slots", None)
    if slots is None:
        slots = asyncio.Semaphore(2)
        request.app.state.swarm_restore_upload_slots = slots
    async with slots:
        return await _restore_swarm_upload(request)


async def _restore_swarm_upload(request: Request) -> dict[str, Any]:
    from jarvis.swarm.lifecycle import MAX_ARCHIVE_BYTES

    size = 0
    too_large = False

    async def bounded_body():
        nonlocal size, too_large
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES + 65536:
                too_large = True
                raise MultiPartException("Backup exceeds the 512 MiB upload limit")
            yield chunk

    try:
        form = await _BackupMultipartParser(
            request.headers, bounded_body(), max_files=1, max_fields=2
        ).parse()
    except (MultiPartException, ValueError) as exc:
        raise HTTPException(413 if too_large else 422, str(exc)) from exc
    temporary: Path | None = None
    try:
        upload = form.get("file")
        request_key = form.get("request_key")
        replacement = form.get("replace_team_id", "")
        if (
            set(form.keys()) - {"file", "request_key", "replace_team_id"}
            or len(form.multi_items()) != len(form.keys())
            or not callable(getattr(upload, "read", None))
            or not isinstance(request_key, str)
            or not 1 <= len(request_key) <= 100
            or not isinstance(replacement, str)
            or (replacement and len(replacement) != 32)
        ):
            raise HTTPException(422, "Select one backup file and a bounded restore operation key")
        upload = cast(Any, upload)  # The bounded file-read capability was checked above.
        service = await _service(request.app.state)
        directory = service.root / "lifecycle" / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".zip", delete=False) as output:
            temporary = Path(output.name)
            copied = 0
            while chunk := await upload.read(65536):
                copied += len(chunk)
                if copied > MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "Backup exceeds the 512 MiB upload limit")
                await service.storage.transfer(output.write, chunk)
        task = asyncio.create_task(
            _call(service.restore_backup(temporary, request_key, replacement))
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The durable mutation completes or records its retry state before
            # its source upload can be removed by disconnect cleanup.
            await task
            raise
    finally:
        await form.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@router.post(
    "/restores/{restore_id}/resume",
    summary="Resume an owned validated storage replacement",
    openapi_extra=_DANGEROUS,
)
async def resume_swarm_restore(request: Request, restore_id: str) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).resume_restore(restore_id))


@router.delete(
    "/teams/{team_id}",
    summary="Delete a confirmed Swarm workspace and retain published output",
    openapi_extra=_DANGEROUS,
)
async def delete_swarm_team(request: Request, team_id: str, body: DeleteBody) -> dict[str, Any]:
    if body.confirm_team_id != team_id:
        raise HTTPException(422, "Confirm the exact team identity before deleting it")
    return await _call(
        (await _service(request.app.state)).delete_team(
            team_id, body.request_key, body.expected_version, body.expected_storage_generation
        )
    )


@router.post(
    "/teams/{team_id}/retention",
    summary="Prune expired team messages, staging and old backups",
    openapi_extra=_DANGEROUS,
)
async def retain_swarm_team(request: Request, team_id: str, body: RetentionBody) -> dict[str, str]:
    return await _call((await _service(request.app.state)).retain_team(team_id, body.before_days))


@router.get("/teams/{team_id}/{kind}", summary="Inspect a bounded page of team-local records")
async def list_swarm_records(
    request: Request,
    team_id: str,
    kind: Literal[
        "tasks",
        "agents",
        "messages",
        "events",
        "artifacts",
        "reputation",
        "publications",
        "decisions",
        "checkpoints",
    ],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000000),
) -> list[dict[str, Any]]:
    return await _call(
        (await _service(request.app.state)).records(team_id, kind, limit=limit, offset=offset)
    )


@router.get("/teams/{team_id}/{kind}/record/{record_id}", summary="Inspect one task or agent")
async def get_swarm_record(
    request: Request,
    team_id: str,
    kind: Literal["tasks", "agents", "decisions", "checkpoints"],
    record_id: str,
) -> dict[str, Any]:
    return await _call((await _service(request.app.state)).record(team_id, kind, record_id))


@router.get(
    "/teams/{team_id}/artifacts/{artifact_id}", summary="Download an authorized team artifact"
)
async def get_swarm_artifact(request: Request, team_id: str, artifact_id: str) -> Response:
    record, content = await _call(
        (await _service(request.app.state)).artifact_stream(team_id, artifact_id)
    )
    return StreamingResponse(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "ETag": '"' + record["sha256"] + '"',
            "Content-Length": record["size_bytes"],
        },
    )


@router.post(
    "/teams/{team_id}/publish",
    summary="Publish one selected verified lead result",
    openapi_extra=_DANGEROUS,
)
async def publish_swarm_result(request: Request, team_id: str, body: PublishBody) -> dict[str, Any]:
    return await _call(
        (await _service(request.app.state)).publish(
            team_id,
            body.artifact_id,
            body.request_key,
            expected_version=body.expected_version,
            expected_storage_generation=body.expected_storage_generation,
        )
    )


@router.get(
    "/publications/{publication_id}", summary="Download a retained selected Swarm publication"
)
async def get_swarm_publication(request: Request, publication_id: str) -> Response:
    record, content = await _call(
        (await _service(request.app.state)).publication_stream(publication_id)
    )
    return StreamingResponse(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Content-Length": record["size_bytes"],
        },
    )


@router.websocket("/teams/{team_id}/ws")
async def swarm_world_stream(websocket: WebSocket, team_id: str) -> None:
    # SurfaceSecurity authenticates the whole HTTP/WS surface before this route.
    service = await _service(websocket.app.state)
    try:
        await service.team(team_id)
    except (PermissionError, ValueError, KeyError):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    group = websocket.query_params.get("group", "")[:100]
    after = websocket.query_params.get("after", "0")
    reader = asyncio.create_task(websocket.receive())
    try:
        first = True
        while True:
            if reader.done():
                event = reader.result()
                if event["type"] == "websocket.disconnect":
                    break
                reader = asyncio.create_task(websocket.receive())
            snapshot = await service.world(team_id, group=group)
            revision = snapshot["revision"]
            if first or str(revision) != after:
                envelope = {"team_id": team_id, "type": "snapshot", "snapshot": snapshot}
                encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
                if len(encoded.encode("utf-8")) > 65536:
                    raise ValueError("Swarm projection exceeded its transport budget")
                await asyncio.wait_for(websocket.send_text(encoded), timeout=5)
                after, first = str(revision), False
            await asyncio.wait({reader}, timeout=0.5)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - every read/send failure terminates this subscriber
        log.debug("Swarm subscriber disconnected: %s", type(exc).__name__)
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


# Register after specific POST routes so /publish is never parsed as an action.
router.add_api_route(
    "/teams/{team_id}/{action}",
    control_swarm_team,
    methods=["POST"],
    summary="Control a Swarm lifecycle",
    openapi_extra=_DANGEROUS,
)
