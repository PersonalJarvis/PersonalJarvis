"""REST-Endpoints fuer Conductor.

Erwartet drei Objekte auf ``request.app.state``:
- ``conductor_store`` (``ConductorStore``)
- ``conductor_runner`` (``Runner``)
- ``conductor_scheduler`` (``Scheduler``) — optional

Ohne Store: 503.
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from ..core.schema import Job

router = APIRouter(prefix="/api/conductor", tags=["conductor"])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _require_store(request: Request) -> Any:
    store = getattr(request.app.state, "conductor_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="ConductorStore nicht verfuegbar")
    return store


def _require_runner(request: Request) -> Any:
    runner = getattr(request.app.state, "conductor_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="Conductor Runner nicht verfuegbar")
    return runner


def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    try:
        spec = json.loads(row.get("spec_json") or "{}")
        sched = json.loads(row.get("schedule_json") or "{}")
        tags = json.loads(row.get("tags_json") or "[]")
    except json.JSONDecodeError:
        spec = {}
        sched = {}
        tags = []
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "enabled": bool(row.get("enabled")),
        "type": row.get("type"),
        "schedule_type": row.get("schedule_type"),
        "schedule_expr": row.get("schedule_expr"),
        "created_at_ns": row.get("created_at_ns"),
        "last_run_at_ns": row.get("last_run_at_ns"),
        "last_run_state": row.get("last_run_state"),
        "next_run_at_ns": row.get("next_run_at_ns"),
        "tags": tags,
        "spec": spec,
        "schedule": sched,
        "webhook_token": row.get("webhook_token"),
    }


# ----------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------

@router.get("/jobs")
async def list_jobs(request: Request) -> dict[str, Any]:
    store = _require_store(request)
    rows = await store.list_jobs()
    summaries = [_row_to_summary(r) for r in rows]
    runs = await store.list_runs(limit=30)
    total = len(summaries)
    enabled = sum(1 for s in summaries if s["enabled"])
    by_type: dict[str, int] = {}
    for s in summaries:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1
    return {
        "jobs": summaries,
        "summary": {
            "total": total,
            "enabled": enabled,
            "by_type": by_type,
        },
        "recent_runs": runs,
    }


@router.post("/jobs", status_code=201)
async def create_job(job: Job, request: Request) -> dict[str, Any]:
    store = _require_store(request)
    # Wenn Schedule ein Webhook ist ohne Token: eins generieren.
    if job.schedule.type == "webhook" and len(job.schedule.token) < 16:
        new_token = secrets.token_urlsafe(24)
        job = job.model_copy(
            update={"schedule": job.schedule.model_copy(update={"token": new_token})}
        )
    jid = await store.upsert_job(job)
    return {"id": jid}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    store = _require_store(request)
    row = await store.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    summary = _row_to_summary(row)
    summary["recent_runs"] = await store.list_runs(job_id=job_id, limit=20)
    return summary


@router.patch("/jobs/{job_id}")
async def patch_job(
    job_id: str, request: Request, payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    store = _require_store(request)
    row = await store.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    if "enabled" in payload:
        await store.set_enabled(job_id, bool(payload["enabled"]))
        if not payload["enabled"]:
            await store.set_next_run(job_id, None)
    return _row_to_summary(await store.get_job(job_id) or {"id": job_id})


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, request: Request) -> dict[str, Any]:
    store = _require_store(request)
    ok = await store.delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return {"ok": True, "id": job_id}


@router.post("/jobs/{job_id}/run")
async def run_job(
    job_id: str, request: Request,
    input_data: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    _require_store(request)
    runner = _require_runner(request)
    try:
        run_id = await runner.trigger(
            job_id, trigger="manual", input_data=input_data or {},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run_id": run_id, "job_id": job_id}


# ----------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------

@router.get("/runs")
async def list_runs(
    request: Request, job_id: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    store = _require_store(request)
    runs = await store.list_runs(job_id=job_id, limit=limit)
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    store = _require_store(request)
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run nicht gefunden")
    return run


# ----------------------------------------------------------------------
# Webhook-Trigger (public, token-authenticated)
# ----------------------------------------------------------------------

@router.post("/hooks/{token}")
async def webhook_trigger(
    token: str, request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Webhook-URL fuer extern gehostete Trigger. Token ist die Auth.

    Beispiel: ``curl -X POST https://your-jarvis/api/conductor/hooks/<token>``
    """
    store = _require_store(request)
    runner = _require_runner(request)
    row = await store.get_job_by_webhook_token(token)
    if row is None:
        raise HTTPException(status_code=404, detail="Unbekannter Webhook-Token")
    if not row.get("enabled"):
        raise HTTPException(status_code=409, detail="Job deaktiviert")
    run_id = await runner.trigger(row["id"], trigger="webhook", input_data=body or {})
    return {"ok": True, "run_id": run_id, "job_id": row["id"]}
