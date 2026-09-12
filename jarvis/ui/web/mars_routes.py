"""Authenticated Mars world definition and durable draft-station control surface."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.responses import Response

from jarvis.society.mars.definition import load_definition
from jarvis.society.mars.models import (
    CommandRecord,
    StationCommand,
    StationError,
    StationEventBatch,
    StationSnapshot,
)

log = logging.getLogger(__name__)


class _PrivateStationRoute(APIRoute):
    """Keep malformed private drafts out of FastAPI's rejected-input echo.

    A request can fail validation before the station's credential guard runs.
    Even error locations may contain a user-supplied extra field name, so no
    input, location, exception text or context is copied into the response.
    The declared endpoint models still generate the normal OpenAPI schema.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def private_validation(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": {"reason": "invalid_station_request"}},
                    headers={"Cache-Control": "no-store"},
                )

        return private_validation


router = APIRouter(prefix="/api/society/mars", tags=["mars"], route_class=_PrivateStationRoute)


async def _reconcile_loop(service: Any) -> None:
    """The process owns progress even with zero renderers and zero HTTP clients."""
    while True:
        try:
            await service.reconcile()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Mars station reconciliation unavailable (%s)", type(exc).__name__)
        await asyncio.sleep(1.0)


async def _service(request: Request) -> Any:
    state = request.app.state
    if getattr(state, "mars_station_lock", None) is None:
        state.mars_station_lock = asyncio.Lock()
    async with state.mars_station_lock:
        existing = getattr(state, "mars_station", None)
        if existing is not None:
            return existing
        from jarvis.society.mars.ordinary import OrdinaryStationExecutor
        from jarvis.society.mars.service import MarsStationService
        from jarvis.society.mars.store import MarsStore

        from .society_routes import _runtime

        runtime = await _runtime(request)
        service = MarsStationService(
            MarsStore(runtime.store.path.parent / "mars" / "ordinary.db"),
            OrdinaryStationExecutor(runtime),
            # Existing chat cancellation waits up to fifteen seconds before forcing stop.
            operation_timeout_s=20.0,
        )
        try:
            await service.start()
        except Exception as exc:
            log.warning("Mars station startup unavailable (%s)", type(exc).__name__)
            raise HTTPException(503, "mars_station_unavailable") from exc
        state.mars_station = service
        state.mars_station_task = asyncio.create_task(
            _reconcile_loop(service), name="mars-station-owner"
        )
        return service


def _error(exc: StationError) -> HTTPException:
    return HTTPException(exc.status_code, {"reason": exc.reason})


@router.get("/definition")
async def get_mars_definition() -> dict[str, Any]:
    """Read the packaged canonical world without starting agents or a renderer."""
    return load_definition()


@router.get("/snapshot", response_model=StationSnapshot)
async def get_mars_snapshot(request: Request) -> StationSnapshot:
    """Read current durable station state; no private draft text is returned."""
    try:
        return await (await _service(request)).snapshot()
    except StationError as exc:
        raise _error(exc) from exc


@router.get("/events", response_model=StationEventBatch)
async def get_mars_events(
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> StationEventBatch:
    """Read a bounded delivered cursor, with an explicit snapshot-resync signal."""
    try:
        return await (await _service(request)).events(after_seq, limit=limit)
    except StationError as exc:
        raise _error(exc) from exc


@router.post(
    "/agents/{agent_id}/commands",
    response_model=CommandRecord,
    openapi_extra={"x-jarvis-dangerous": True},
)
async def submit_mars_draft(
    agent_id: str, body: StationCommand, request: Request
) -> CommandRecord:
    """Submit one idempotent communication draft through existing agent authority."""
    try:
        return await (await _service(request)).submit(agent_id, body)
    except StationError as exc:
        raise _error(exc) from exc


@router.post(
    "/agents/{agent_id}/commands/{command_id}/cancel",
    response_model=CommandRecord,
    openapi_extra={"x-jarvis-dangerous": True},
)
async def cancel_mars_command(agent_id: str, command_id: str, request: Request) -> CommandRecord:
    """Request a scoped stop without canceling a newer unrelated conversation."""
    try:
        return await (await _service(request)).cancel(agent_id, command_id)
    except StationError as exc:
        raise _error(exc) from exc
