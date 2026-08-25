"""REST for the Local models setup assistant.

    POST /api/providers/{provider_id}/local-models/assistant/run        start a guided turn
    GET  /api/providers/{provider_id}/local-models/assistant/session    the one session + tier
    POST /api/providers/{provider_id}/local-models/assistant/test       end-to-end setup test
    GET  /api/providers/{provider_id}/local-models/assistant/benchmarks the proven/new table
    GET  /api/providers/{provider_id}/local-models/assistant/health     the badge record

The assistant is a ``local-models`` agent-chat session (the timeline, the
WebSocket stream and the approval cards are the agent chat's — see
``agent_chat_routes.py``); these routes only start it with the canned opener
of a mode and expose the runner, the benchmark table and the health file.
Every handler sits behind the pull-capability gate the section uses
(``provider_routes._require_pull_capable``). ``/run`` answers 409 with one
English sentence when the Jarvis-Agents tier cannot run — never a silent
fallback.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.ui.web.agent_chat_routes import _service_from_state
from jarvis.ui.web.provider_routes import (
    _require_pull_capable,
    _resolve_cfg,
    _worker_flagged_dead,
    _worker_usable,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/providers/{provider_id}/local-models/assistant",
    tags=["local-models-assistant"],
)

Mode = Literal["setup", "diagnose", "test"]


class RunBody(BaseModel):
    mode: Mode = "setup"


class RunResponse(BaseModel):
    session_id: str
    turn_id: str
    surface: str = "local-models"


class SessionResponse(BaseModel):
    session_id: str | None
    surface: str = "local-models"
    provider: str
    model: str
    ready: bool
    reason: str


class TestBody(BaseModel):
    roles: list[str] | None = Field(default=None, description="Subset of the writable roles.")


class HealthResponse(BaseModel):
    status: str
    reason: str
    since: str | None = None
    last_ok: str | None = None
    checked_at: str | None = None


def _opener(mode: str) -> str:
    from jarvis.local_models.assistant_prompt import (
        DIAGNOSE_OPENER,
        SETUP_OPENER,
        TEST_OPENER,
    )

    return {"setup": SETUP_OPENER, "diagnose": DIAGNOSE_OPENER, "test": TEST_OPENER}[mode]


def _usable(provider: str) -> bool:
    """The provider-agnostic worker check the API-keys page uses."""
    from jarvis.local_models.assistant_session import _default_usable

    return (_worker_usable(provider) or _default_usable(provider)) and not _worker_flagged_dead(
        provider
    )


async def _live(cfg: Any) -> dict[tuple[str, str], tuple[bool, str]]:
    """One real call per candidate pair (cached ten minutes): a key that
    authenticates but is refused for quota is not a working assistant."""
    from jarvis.local_models.assistant_session import chain_candidates, probe_live

    return await probe_live(cfg, chain_candidates(cfg, usable=_usable))


def _svc(request: Request) -> Any:
    svc = _service_from_state(request.app.state)
    if svc is None:
        raise HTTPException(status_code=503, detail="agent-chat-unavailable")
    return svc


def _cfg(request: Request) -> Any:
    cfg = _resolve_cfg(request)
    if cfg is None:
        raise HTTPException(status_code=503, detail="Configuration is unavailable (headless mode?)")
    return cfg


@router.post("/run", response_model=RunResponse)
async def post_run(provider_id: str, body: RunBody, request: Request) -> RunResponse:
    """Start a guided turn: ensure the one session, send the mode's opener."""
    from jarvis.agent_chat.service import SessionBusy
    from jarvis.local_models.assistant_session import ensure_session

    _require_pull_capable(provider_id)
    cfg = _cfg(request)
    svc = _svc(request)
    live = await _live(cfg)
    try:
        session = ensure_session(svc, cfg, usable=_usable, live=live)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        turn_id = await svc.send(session.session_id, _opener(body.mode))
    except SessionBusy as exc:
        raise HTTPException(
            status_code=409, detail="The assistant is still answering; wait for that turn."
        ) from exc
    return RunResponse(session_id=session.session_id, turn_id=turn_id)


@router.get("/session", response_model=SessionResponse)
async def get_session(provider_id: str, request: Request) -> SessionResponse:
    """The one ``local-models`` session (or null) and whether the tier can run."""
    from jarvis.local_models.assistant_session import session_state

    _require_pull_capable(provider_id)
    cfg = _cfg(request)
    svc = _svc(request)
    live = await _live(cfg)
    return SessionResponse(**session_state(svc, cfg, usable=_usable, live=live))


@router.post("/test")
async def post_test(provider_id: str, request: Request, body: TestBody | None = None) -> dict:
    """Run the end-to-end setup test and persist the report."""
    from jarvis.local_models.assistant_test import run_setup_test

    _require_pull_capable(provider_id)
    cfg = _cfg(request)
    roles = tuple(body.roles) if body is not None and body.roles else None
    try:
        report = await run_setup_test(cfg, roles)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report.to_payload()


@router.get("/benchmarks")
async def get_benchmarks(provider_id: str, request: Request, refresh: bool = False) -> dict:
    """The benchmark table; ``refresh=1`` rebuilds it through the brain's web search."""
    from jarvis.local_models import benchmarks

    _require_pull_capable(provider_id)
    table: benchmarks.BenchmarkTable | None
    if refresh:
        from jarvis.agent_chat.runner_brain import brain_manager
        from jarvis.agent_chat.surface_kits import local_models_search_fn

        search_fn = local_models_search_fn(brain_manager())
        if search_fn is None:
            raise HTTPException(
                status_code=503, detail="Web search is not available yet; try again shortly."
            )
        table = await benchmarks.refresh_benchmarks(search_fn)
    else:
        table = benchmarks.load_cached() or benchmarks.curated_only(
            "No benchmark cache yet; refresh=1 builds one."
        )
    return table.to_payload()


@router.get("/health", response_model=HealthResponse)
async def get_health(provider_id: str) -> HealthResponse:
    """The persisted badge record — a file read, no live call."""
    from jarvis.local_models.health_monitor import read_health_record

    _require_pull_capable(provider_id)
    return HealthResponse(**read_health_record())


__all__ = ["router"]
