"""Server-free native voice package management; qualification stays explicit."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from jarvis.core.paths import user_data_dir
from jarvis.realtime.local_runtime.catalog import catalog_model
from jarvis.realtime.local_runtime.library import LocalVoiceLibrary, PackageJob, VoiceLibrary
from jarvis.realtime.local_runtime.models import LocalModelManifest


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    library = getattr(app.state, "local_voice_library", None)
    if library is not None:
        await library.close()


router = APIRouter(prefix="/api/local-voice", tags=["local-voice"], lifespan=_lifespan)


def _library(request: Request) -> LocalVoiceLibrary:
    library = getattr(request.app.state, "local_voice_library", None)
    if library is None:
        library = LocalVoiceLibrary(user_data_dir() / "local-voice" / "models")
        request.app.state.local_voice_library = library
    return library


class AcquireVoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(default="", max_length=100)
    fingerprint: str = Field(default="", max_length=64)
    manifest: LocalModelManifest | None = None
    source_directory: str = Field(default="", max_length=4096)


@router.get("/models", response_model=VoiceLibrary, summary="List native voice model packages")
async def list_voice_models(request: Request) -> VoiceLibrary:
    return await asyncio.to_thread(_library(request).snapshot)


@router.post(
    "/models/acquire",
    response_model=PackageJob,
    status_code=202,
    summary="Download or import a native voice model package",
)
async def acquire_voice_model(body: AcquireVoiceModel, request: Request) -> PackageJob:
    if sum((bool(body.model_id), bool(body.fingerprint), body.manifest is not None)) != 1:
        raise HTTPException(400, "Choose one catalog model or supply one custom manifest.")
    library = _library(request)
    model = body.manifest or catalog_model(body.model_id)
    if body.fingerprint:
        model = await asyncio.to_thread(library.resolve, body.fingerprint)
    if model is None:
        raise HTTPException(404, "This voice model is not in the catalog.")
    try:
        return library.start(model, Path(body.source_directory) if body.source_directory else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=PackageJob,
    summary="Cancel a native voice package operation",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def cancel_voice_model(job_id: str, request: Request) -> PackageJob:
    try:
        return _library(request).cancel(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
