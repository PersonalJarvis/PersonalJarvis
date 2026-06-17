"""REST routes for the Run Inspector (forensic lens over voice sessions).

    GET /api/runs                 -> list[RunListItem]  (newest first, capped)
    GET /api/runs/{session_id}    -> Run

Read-only; reuses app.state.session_store (set by bootstrap_sessions) and a
process-local UsageLog. Loopback-only, no auth token (mirrors sessions_routes)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from jarvis.clis.usage_log import UsageLog
from jarvis.runs.loader import RunLoader
from jarvis.runs.model import Run, RunListItem
from jarvis.sessions.store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])

# A single process-local UsageLog reader; it finds its own db via cli_usage_db_path().
_usage_log: UsageLog | None = None


def _get_usage_log() -> UsageLog | None:
    global _usage_log
    if _usage_log is None:
        try:
            _usage_log = UsageLog()
        except Exception as exc:  # noqa: BLE001 — usage log is an optional slice
            log.debug("UsageLog unavailable for run-inspector: %s", exc)
            _usage_log = None
    return _usage_log


def _loader(request: Request) -> RunLoader:
    store: SessionStore | None = getattr(request.app.state, "session_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="session-recorder-disabled")
    return RunLoader(session_store=store, usage_log=_get_usage_log(), missions_lookup=None)


@router.get("", response_model=list[RunListItem])
async def list_runs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RunListItem]:
    return _loader(request).list_runs(limit=limit)


@router.get("/{session_id}", response_model=Run)
async def get_run(session_id: str, request: Request) -> Run:
    run = _loader(request).load_run(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run-not-found")
    return run
