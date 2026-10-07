"""Ops REST: one read-only view over missions, tasks, quests and workflows.

``GET /api/ops/work`` folds every system's work into the shared WorkLedger
statuses (see ``jarvis/ops/ledger.py``). Reading only: it changes no store,
starts no run and builds no service — a source that is not up in this process
is listed as ``unavailable``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from jarvis.ops.ledger import WORK_SOURCES, WorkLedger

router = APIRouter(prefix="/api/ops", tags=["ops"])


def _ledger(request: Request) -> WorkLedger:
    state = request.app.state
    return WorkLedger(
        missions=lambda: getattr(state, "mission_manager", None),
        tasks=lambda: getattr(state, "task_store", None),
        # Only a society runtime that already runs; reading never starts one.
        quests=lambda: getattr(state, "society", None),
        workflows=lambda: getattr(state, "workflow_store", None),
    )


@router.get("/work")
async def list_work(
    request: Request,
    source: str | None = Query(
        default=None, description="Comma-separated: mission,task,quest,workflow"
    ),
    include_finished: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Everything Jarvis is working on, in one status vocabulary (read-only)."""
    sources: list[str] | None = None
    if source:
        sources = [s.strip() for s in source.split(",") if s.strip()]
        unknown = [s for s in sources if s not in WORK_SOURCES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown work sources: {unknown}")
    snapshot = await _ledger(request).snapshot(
        sources=sources, include_finished=include_finished, limit=limit
    )
    return snapshot.to_dict()
