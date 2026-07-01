"""REST API for the task queue (Phase 5 Capability 4).

Endpoints:
- ``POST   /api/tasks``              → create + schedule a TaskSpec.
- ``GET    /api/tasks``              → task list, optionally ``?state=...``.
- ``GET    /api/tasks/{id}``         → full task with steps timeline.
- ``POST   /api/tasks/{id}/cancel``  → soft cancel (remove from the heap).
- ``DELETE /api/tasks/{id}``         → hard delete (terminal states only).

The router expects a ``TaskStore`` + ``TaskScheduler`` on
``app.state.task_store`` resp. ``app.state.task_scheduler`` — these are
set by the DesktopApp at startup. If neither is set, the endpoints answer
with ``503`` (Service Unavailable).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from jarvis.tasks.schema import TaskSpec

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _require_store(request: Request) -> Any:
    store = getattr(request.app.state, "task_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="TaskStore not available")
    return store


def _optional_scheduler(request: Request) -> Any | None:
    return getattr(request.app.state, "task_scheduler", None)


def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Converts a DB row into a UI summary dict. Flat keys only, no steps."""
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "state": row["state"],
        "trigger_type": row["trigger_type"],
        "due_at_ns": row.get("due_at_ns"),
        "created_at_ns": row.get("created_at_ns"),
        "started_at_ns": row.get("started_at_ns"),
        "finished_at_ns": row.get("finished_at_ns"),
        "attempts": row.get("attempts", 0),
        "last_error": row.get("last_error"),
    }


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@router.post("", status_code=201)
async def create_task(spec: TaskSpec, request: Request) -> dict[str, Any]:
    """Creates a task. If a ``TaskScheduler`` is available, the complete
    ``schedule()`` path runs (incl. heap push + wakeup); otherwise it's
    a plain store insert.
    """
    store = _require_store(request)
    scheduler = _optional_scheduler(request)
    if scheduler is not None:
        task_id = await scheduler.schedule(spec)
    else:
        task_id = await store.insert(spec)
    return {"id": task_id}


@router.get("")
async def list_tasks(
    request: Request,
    state: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List of all tasks, optionally filtered by state."""
    store = _require_store(request)
    filter_val: str | list[str] | None
    if state is None or state == "":
        filter_val = None
    elif "," in state:
        filter_val = [s.strip() for s in state.split(",") if s.strip()]
    else:
        filter_val = state
    rows = await store.list(state_filter=filter_val, limit=limit)
    return {
        "tasks": [_row_to_summary(r) for r in rows],
        "total": len(rows),
    }


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    """Full task incl. steps timeline."""
    store = _require_store(request)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # spec_json → dict for UI convenience
    spec_obj = None
    spec_raw = task.get("spec_json")
    if spec_raw:
        try:
            spec_obj = json.loads(spec_raw)
        except json.JSONDecodeError:
            spec_obj = None

    task_out = _row_to_summary(task)
    task_out["spec"] = spec_obj
    task_out["steps"] = task.get("steps", [])
    return task_out


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
    """Soft cancel: removes the task from the heap/event index and sets
    its state to ``cancelled``. Does **not** abort a hard CU loop — that's
    what the global kill switch is for.
    """
    store = _require_store(request)
    scheduler = _optional_scheduler(request)
    # 404 for unknown tasks
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["state"] in ("completed", "failed", "cancelled", "interrupted"):
        raise HTTPException(
            status_code=409,
            detail=f"Task is already final (state={task['state']})",
        )

    if scheduler is not None:
        ok = await scheduler.cancel_task(task_id, reason="web_ui_cancel")
    else:
        await store.update_state(task_id, "cancelled", error="web_ui_cancel")
        await store.append_step(task_id, "log",
                                {"event": "cancelled", "reason": "web_ui_cancel"})
        ok = True
    return {"ok": bool(ok), "id": task_id, "state": "cancelled"}


@router.delete("/{task_id}")
async def delete_task(task_id: str, request: Request) -> dict[str, Any]:
    """Hard delete — only allowed when the task is in a terminal state."""
    store = _require_store(request)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["state"] not in ("completed", "failed", "cancelled", "interrupted"):
        raise HTTPException(
            status_code=409,
            detail=f"Task is still active (state={task['state']}) — cancel it first",
        )
    deleted = await store.delete(task_id)
    return {"ok": deleted, "id": task_id}
