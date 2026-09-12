"""Authenticated Mars world definition and durable draft-station control surface."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
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

_INITIALIZATION_TIMEOUT_S = 30.0
_INITIALIZATION_STOP_TIMEOUT_S = 5.0


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


def _station_lock(state: Any) -> asyncio.Lock:
    if getattr(state, "mars_station_lock", None) is None:
        state.mars_station_lock = asyncio.Lock()
    return state.mars_station_lock


async def _initialize_mars_station(state: Any) -> Any:
    """The application owns initialization even when every HTTP waiter leaves."""
    from jarvis.society.mars.ordinary import OrdinaryStationExecutor
    from jarvis.society.mars.service import MarsStationService
    from jarvis.society.mars.store import MarsStore

    service = None
    published = False
    try:
        async with asyncio.timeout(_INITIALIZATION_TIMEOUT_S):
            runtime = getattr(state, "society", None)
            if runtime is None:
                factory = getattr(state, "society_factory", None)
                if factory is None:
                    raise HTTPException(503, "mars_station_unavailable")
                runtime = factory()
                state.society = runtime
            await runtime.ensure_started()
            if getattr(state, "mars_station_stopping", False):
                raise HTTPException(503, "mars_station_stopped")
            service = MarsStationService(
                MarsStore(runtime.store.path.parent / "mars" / "ordinary.db"),
                OrdinaryStationExecutor(runtime),
                # Existing chat cancellation waits up to fifteen seconds before forcing stop.
                operation_timeout_s=20.0,
            )
            await service.start()
            if getattr(state, "mars_station_stopping", False):
                raise HTTPException(503, "mars_station_stopped")
            # No await separates the stop check and publication. The state lock
            # only protects task creation; it is never held across initialization.
            state.mars_station = service
            state.mars_station_task = asyncio.create_task(
                _reconcile_loop(service), name="mars-station-owner"
            )
            published = True
            return service
    except Exception as exc:
        # Exception text may contain private drafts or provider error bodies.
        log.warning("Mars station startup unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, "mars_station_unavailable") from exc
    finally:
        if service is not None and not published:
            await service.close()


def _observe_initialization(task: asyncio.Task[Any]) -> None:
    # All waiters may have disconnected. Startup logs a sanitized failure itself;
    # retrieving the exception avoids asyncio logging its private exception chain.
    if not task.cancelled():
        task.exception()


async def ensure_mars_station(state: Any) -> Any:
    """Share one bounded initialization task between HTTP and deferred recovery."""
    async with _station_lock(state):
        if getattr(state, "mars_station_stopping", False):
            raise HTTPException(503, "mars_station_stopped")
        existing = getattr(state, "mars_station", None)
        if existing is not None:
            return existing
        initialization = getattr(state, "mars_station_initialization_task", None)
        if initialization is None or initialization.done():
            initialization = asyncio.create_task(
                _initialize_mars_station(state), name="mars-station-initialize"
            )
            initialization.add_done_callback(_observe_initialization)
            state.mars_station_initialization_task = initialization
    # A canceled HTTP request must not cancel initialization needed by other
    # callers or the process-owned recovery task. Explicit stop cancels the owner.
    return await asyncio.shield(initialization)


def schedule_mars_resume(state: Any, data_dir: Path) -> None:
    """Resume an existing journal after boot, without requiring any client.

    Merely scheduling this task does no I/O and calls no runtime factory. A
    normal install that has never used Mars retains its lazy boot behavior.
    """
    if getattr(state, "mars_station_stopping", False):
        return
    previous = getattr(state, "mars_station_startup_task", None)
    if previous is not None and not previous.done():
        return

    async def resume() -> None:
        # Even an eager task factory must not run recovery on the boot chain.
        await asyncio.sleep(0)
        try:
            path = Path(data_dir) / "mars" / "ordinary.db"
            if await asyncio.to_thread(path.is_file):
                await ensure_mars_station(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Mars station deferred resume unavailable (%s)", type(exc).__name__)

    state.mars_station_startup_task = asyncio.create_task(resume(), name="mars-station-resume")


async def stop_mars_station(state: Any) -> None:
    """Fence late initialization and join both owners before releasing the store."""
    state.mars_station_stopping = True
    tasks = tuple(
        task
        for name in (
            "mars_station_startup_task",
            "mars_station_initialization_task",
            "mars_station_task",
        )
        if (task := getattr(state, name, None)) is not None
    )
    for task in tasks:
        task.cancel()
    if tasks:
        _done, pending = await asyncio.wait(tasks, timeout=_INITIALIZATION_STOP_TIMEOUT_S)
        if pending:
            # Retain ownership references and the stop latch if a collaborator
            # ignores cancellation. It still cannot publish a station when it
            # eventually returns; its finally block closes any partial service.
            log.warning("Mars station shutdown deadline expired (TimeoutError)")
            raise TimeoutError("mars_station_shutdown_timeout")
    async with _station_lock(state):
        service = getattr(state, "mars_station", None)
        if service is not None:
            await service.close()
            state.mars_station = None
        state.mars_station_startup_task = None
        state.mars_station_initialization_task = None
        state.mars_station_task = None


async def _service(request: Request) -> Any:
    return await ensure_mars_station(request.app.state)


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
async def submit_mars_draft(agent_id: str, body: StationCommand, request: Request) -> CommandRecord:
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
