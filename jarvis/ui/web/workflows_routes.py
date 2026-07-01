"""REST API for workflows (Phase 6).

Endpoints:
- ``GET    /api/workflows``              → dashboard list (workflows + summary).
- ``POST   /api/workflows``              → create a WorkflowDef.
- ``GET    /api/workflows/{id}``         → detail incl. most recent runs.
- ``PATCH  /api/workflows/{id}``         → enable/disable (partial update).
- ``DELETE /api/workflows/{id}``         → remove.
- ``POST   /api/workflows/{id}/run``     → trigger manually, body = input dict.
- ``GET    /api/workflows/runs/{run_id}``→ run detail with step timeline.

Expected on ``app.state``:
- ``workflow_store``   (WorkflowStore)
- ``workflow_runner``  (WorkflowRunner)

Fallback: if neither is set, the endpoints return 503 — the UI then shows
an empty state instead of crashing.
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from jarvis.workflows.schema import WorkflowDef

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ----------------------------------------------------------------------
# State accessor
# ----------------------------------------------------------------------

def _require_store(request: Request) -> Any:
    store = getattr(request.app.state, "workflow_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="WorkflowStore not available")
    return store


def _require_runner(request: Request) -> Any:
    runner = getattr(request.app.state, "workflow_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="WorkflowRunner not available")
    return runner


# ----------------------------------------------------------------------
# Row → UI dict
# ----------------------------------------------------------------------

def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Flat dashboard summary — without the full def (lean for lists)."""
    def_json = row.get("def_json") or "{}"
    try:
        defs = json.loads(def_json)
        step_count = len(defs.get("steps", []))
        tags = defs.get("tags", []) or []
    except json.JSONDecodeError:
        step_count = 0
        tags = []
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "enabled": bool(row.get("enabled")),
        "trigger_type": row.get("trigger_type") or "manual",
        "cron_expression": row.get("cron_expression"),
        "created_at_ns": row.get("created_at_ns"),
        "created_by": row.get("created_by") or "user",
        "last_run_at_ns": row.get("last_run_at_ns"),
        "last_run_state": row.get("last_run_state"),
        "next_run_at_ns": row.get("next_run_at_ns"),
        "step_count": step_count,
        "tags": tags,
    }


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@router.get("/integrations")
async def integrations_status(request: Request) -> dict[str, Any]:
    """Status of external integrations — the UI renders a setup banner when
    something is missing (e.g. the Telegram bot token is not set)."""
    from jarvis.core.config import get_secret

    # Telegram
    token = get_secret("telegram_bot_token", "TELEGRAM_BOT_TOKEN")
    cfg = getattr(request.app.state, "config", None)
    chat_id = ""
    if cfg is not None:
        try:
            chat_id = cfg.integrations.telegram.chat_id or ""
        except Exception:  # noqa: BLE001
            chat_id = ""

    # gws CLI: try to resolve the path
    import shutil
    gws_path = shutil.which("gws")

    return {
        "telegram": {
            "configured": bool(token and chat_id),
            "has_token": bool(token),
            "has_chat_id": bool(chat_id),
            "setup_hint": (
                "Create a bot via @BotFather, set the token as "
                "ENV TELEGRAM_BOT_TOKEN (setx TELEGRAM_BOT_TOKEN <token>), "
                "and enter the chat ID under [integrations.telegram] in "
                "jarvis.toml."
            ),
        },
        "gws_cli": {
            "configured": bool(gws_path),
            "path": gws_path,
            "setup_hint": (
                "Google Workspace CLI — used for gmail/calendar/drive workflows. "
                "See the gws docs for installation + auth; after setup, "
                "test with 'gws auth status'."
            ),
        },
    }


@router.get("")
async def list_workflows(request: Request) -> dict[str, Any]:
    """Dashboard list: all workflows + aggregated metrics."""
    store = _require_store(request)
    rows = await store.list_workflows()
    summaries = [_row_to_summary(r) for r in rows]

    now_ns = time.time_ns()
    active = sum(1 for r in rows if r.get("enabled"))
    cron = sum(1 for r in rows if r.get("trigger_type") == "cron" and r.get("enabled"))
    recent_runs = await store.list_runs(limit=10)
    next_run_candidates = [
        int(s["next_run_at_ns"])
        for s in summaries
        if s.get("next_run_at_ns") and int(s["next_run_at_ns"]) > now_ns
    ]
    next_run_at_ns = min(next_run_candidates) if next_run_candidates else None

    return {
        "workflows": summaries,
        "summary": {
            "total": len(summaries),
            "enabled": active,
            "cron_enabled": cron,
            "next_run_at_ns": next_run_at_ns,
        },
        "recent_runs": recent_runs,
    }


@router.post("", status_code=201)
async def create_workflow(
    wf: WorkflowDef,
    request: Request,
) -> dict[str, Any]:
    """Creates a new workflow (or overwrites by ID)."""
    store = _require_store(request)
    wid = await store.upsert_workflow(wf)
    return {"id": wid}


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    store = _require_store(request)
    row = await store.get_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    summary = _row_to_summary(row)
    try:
        defs = json.loads(row.get("def_json") or "{}")
    except json.JSONDecodeError:
        defs = {}
    summary["definition"] = defs
    summary["recent_runs"] = await store.list_runs(workflow_id, limit=15)
    return summary


@router.patch("/{workflow_id}")
async def patch_workflow(
    workflow_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Partial update. Currently only ``enabled`` — more fields as needed."""
    store = _require_store(request)
    row = await store.get_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if "enabled" in payload:
        await store.set_enabled(workflow_id, bool(payload["enabled"]))
        # Cron workflows: recompute next_run on toggle
        if not payload["enabled"]:
            await store.set_next_run(workflow_id, None)
    updated = await store.get_workflow(workflow_id)
    return _row_to_summary(updated or {"id": workflow_id})


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    store = _require_store(request)
    deleted = await store.delete_workflow(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"ok": True, "id": workflow_id}


@router.post("/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    request: Request,
    input_data: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Manually triggers a workflow run. Returns the run ID immediately —
    the actual run is fire-and-forget; the UI polls / streams over WS."""
    _require_store(request)
    runner = _require_runner(request)
    try:
        run_id = await runner.trigger(
            workflow_id,
            trigger_reason="manual",
            input_data=input_data or {},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run_id": run_id, "workflow_id": workflow_id}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    store = _require_store(request)
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/")
async def list_runs(request: Request, workflow_id: str | None = None) -> dict[str, Any]:
    store = _require_store(request)
    runs = await store.list_runs(workflow_id, limit=30)
    return {"runs": runs, "total": len(runs)}
