"""REST API for the task queue (Phase 5 Capability 4) — the Automations section.

Endpoints:
- ``GET    /api/tasks/templates``            → the pre-built automations catalogue.
- ``POST   /api/tasks/templates/{key}/add``  → instantiate a template as a task.
- ``POST   /api/tasks``              → create + schedule a TaskSpec.
- ``GET    /api/tasks``              → task list, optionally ``?state=...``.
- ``GET    /api/tasks/{id}``         → full task with steps timeline.
- ``POST   /api/tasks/{id}/run``     → run the action now, out of band.
- ``PATCH  /api/tasks/{id}``         → ``{"enabled": bool}`` pause / resume.
- ``POST   /api/tasks/{id}/cancel``  → soft cancel (remove from the heap).
- ``DELETE /api/tasks/{id}``         → hard delete (terminal states only).

The router expects a ``TaskStore`` + ``TaskScheduler`` on
``app.state.task_store`` resp. ``app.state.task_scheduler`` — these are
set by the DesktopApp at startup. If neither is set, the endpoints answer
with ``503`` (Service Unavailable). Template readiness reads the live tool
names from ``app.state.brain`` when it exists.

Route order matters: the ``/templates`` routes are registered BEFORE the
``/{task_id}`` routes so "templates" is never captured as a task id.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from jarvis.tasks import templates as tpl
from jarvis.tasks.scheduler import TaskNotFound, TaskStateConflict
from jarvis.tasks.schema import PAUSABLE_TRIGGER_TYPES, TERMINAL_STATES, TaskSpec

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

#: ``last_result`` is a card teaser, not the transcript — the full text stays
#: in the task's ``agent_result`` step.
LAST_RESULT_MAX_CHARS = 400


def _require_store(request: Request) -> Any:
    store = getattr(request.app.state, "task_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="TaskStore not available")
    return store


def _optional_scheduler(request: Request) -> Any | None:
    return getattr(request.app.state, "task_scheduler", None)


def _require_scheduler(request: Request) -> Any:
    scheduler = _optional_scheduler(request)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="TaskScheduler not available")
    return scheduler


def _live_tool_names(request: Request) -> list[str] | None:
    """Tool names of the live brain, or ``None`` when no brain is up yet
    (readiness then defaults to "ready" rather than flagging every card)."""
    brain = getattr(request.app.state, "brain", None)
    snapshot = getattr(brain, "snapshot", None)
    if brain is None or not callable(snapshot):
        return None
    try:
        tools = snapshot().get("tools_available")
    except Exception:  # noqa: BLE001 — a half-built brain must not 500 the catalogue
        return None
    if tools is None:
        return None
    return [str(t) for t in tools]


def _last_run_state(row: dict[str, Any]) -> str | None:
    """Outcome of the most recent run, independent of the schedule state.

    A recurring task returns to ``scheduled`` after each run, so its row
    state says nothing about how the last run went; ``finished_at_ns`` +
    ``last_error`` do (the store clears ``last_error`` on a later success).
    """
    state = row["state"]
    if state == "running":
        return "running"
    if state in TERMINAL_STATES:
        return str(state)
    if row.get("finished_at_ns") is None:
        return None
    return "failed" if row.get("last_error") else "completed"


def _parse_spec(row: dict[str, Any]) -> dict[str, Any] | None:
    raw = row.get("spec_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _row_to_summary(
    row: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    last_result: str | None = None,
) -> dict[str, Any]:
    """Converts a DB row into a UI summary dict. Flat keys only, no steps."""
    spec = spec if spec is not None else _parse_spec(row)
    trigger = (spec or {}).get("trigger") or {}
    interval = trigger.get("interval_seconds") if trigger.get("type") == "every" else None
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "state": row["state"],
        "trigger_type": row["trigger_type"],
        "due_at_ns": row.get("due_at_ns"),
        "next_due_at_ns": row.get("due_at_ns"),
        "created_at_ns": row.get("created_at_ns"),
        "started_at_ns": row.get("started_at_ns"),
        "finished_at_ns": row.get("finished_at_ns"),
        "attempts": row.get("attempts", 0),
        "last_error": row.get("last_error"),
        "tags": list((spec or {}).get("tags") or []),
        "created_by": (spec or {}).get("created_by") or "user",
        "interval_seconds": interval,
        "last_run_state": _last_run_state(row),
        "last_result": last_result,
    }


# ----------------------------------------------------------------------
# Templates (registered first — see module docstring)
# ----------------------------------------------------------------------

class TemplateAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, str] = Field(default_factory=dict)
    schedule: tpl.TemplateSchedule | None = None
    title: str | None = Field(default=None, max_length=256)
    locale: str = "en"


@router.get("/templates")
async def list_templates(request: Request, locale: str = "en") -> dict[str, Any]:
    """The automations catalogue, localized, with a readiness verdict per
    template computed against the live brain's tool names."""
    live_tools = _live_tool_names(request)
    return {
        "templates": [
            t.to_api(locale, live_tools=live_tools)
            for t in tpl.all_templates().values()
        ],
        "categories": list(tpl.CATEGORIES),
    }


@router.post("/templates/{key}/add", status_code=201)
async def add_template(
    key: str, body: TemplateAddRequest, request: Request,
) -> dict[str, Any]:
    """Instantiate a template as a scheduled task (``created_by="template"``,
    tagged ``template:<key>``). 404 unknown key, 422 missing required input."""
    template = tpl.get_template(key)
    if template is None:
        raise HTTPException(status_code=404, detail=f"Unknown template {key!r}")
    try:
        spec = tpl.build_spec(
            template,
            inputs=body.inputs,
            schedule=body.schedule,
            title=body.title,
            locale=body.locale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store = _require_store(request)
    scheduler = _optional_scheduler(request)
    if scheduler is not None:
        task_id = await scheduler.schedule(spec)
    else:
        task_id = await store.insert(spec)
    return {"id": task_id}


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
    results = await store.latest_agent_results(
        [r["id"] for r in rows], max_chars=LAST_RESULT_MAX_CHARS,
    )
    return {
        "tasks": [_row_to_summary(r, last_result=results.get(r["id"])) for r in rows],
        "total": len(rows),
    }


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    """Full task incl. steps timeline."""
    store = _require_store(request)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    spec_obj = _parse_spec(task)
    results = await store.latest_agent_results(
        [task_id], max_chars=LAST_RESULT_MAX_CHARS,
    )
    task_out = _row_to_summary(task, spec=spec_obj, last_result=results.get(task_id))
    task_out["spec"] = spec_obj
    task_out["steps"] = task.get("steps", [])
    return task_out


class TaskPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.patch("/{task_id}")
async def patch_task(
    task_id: str, body: TaskPatchRequest, request: Request,
) -> dict[str, Any]:
    """``{"enabled": false}`` pauses a recurring task, ``true`` resumes it at
    its next occurrence. 409 for one-shot triggers or a conflicting state."""
    store = _require_store(request)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["trigger_type"] not in PAUSABLE_TRIGGER_TYPES:
        raise HTTPException(
            status_code=409,
            detail=f"Only recurring tasks can be paused (trigger={task['trigger_type']})",
        )
    scheduler = _require_scheduler(request)
    try:
        if body.enabled:
            await scheduler.resume(task_id)
            new_state = "scheduled"
        else:
            await scheduler.pause(task_id)
            new_state = "paused"
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except TaskStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    refreshed = await store.get(task_id)
    return {
        "ok": True,
        "id": task_id,
        "state": new_state,
        "due_at_ns": (refreshed or {}).get("due_at_ns"),
    }


@router.post("/{task_id}/run", openapi_extra={"x-jarvis-dangerous": True})
async def run_task_now(task_id: str, request: Request) -> dict[str, Any]:
    """Runs the task's action NOW, out of band — the schedule of a recurring
    task is not shifted. 409 while it is already running."""
    store = _require_store(request)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["state"] == "running":
        raise HTTPException(status_code=409, detail="Task is already running")
    scheduler = _require_scheduler(request)
    try:
        await scheduler.run_now(task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except TaskStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "id": task_id}


@router.post("/{task_id}/cancel", openapi_extra={"x-jarvis-dangerous": True})
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
    if task["state"] in TERMINAL_STATES:
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
    if task["state"] not in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"Task is still active (state={task['state']}) — cancel it first",
        )
    deleted = await store.delete(task_id)
    return {"ok": deleted, "id": task_id}
