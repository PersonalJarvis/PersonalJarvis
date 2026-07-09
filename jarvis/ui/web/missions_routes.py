"""REST API for the Phase-6 mission subsystem.

Endpoints:
- ``GET    /api/missions``                 → list of all missions (filterable).
- ``GET    /api/missions/{id}``            → mission detail + events + verdicts.
- ``POST   /api/missions/dispatch``        → create a new mission + start the run.
- ``POST   /api/missions/{id}/cancel``     → best-effort state transition.
- ``POST   /api/missions/kill/{worker}``   → worker stub (full Job Object logic
                                              lands in the Phase-5 production wiring).

Pattern like ``tasks_routes.py``:
- The resource (``MissionManager``/``Kontrollierer``) is attached to
  ``app.state.<name>`` at server start.
- If not set → ``HTTPException(503)``.
- Pydantic body models inline, no separate schema module.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from jarvis.missions.events import EventEnvelope, MissionCancelled, now_ms
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import (
    IllegalStateTransition,
    MissionState,
    is_terminal,
)
from jarvis.ui.web.missions_worker import extract_worker_missions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/missions", tags=["missions"])


# ---------------------------------------------------------------------------
# DI-Helpers
# ---------------------------------------------------------------------------


def _require_manager(request: Request) -> MissionManager:
    """MissionManager from app.state, or 503."""
    mgr = getattr(request.app.state, "mission_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=503, detail="MissionManager not available"
        )
    return mgr


def _optional_kontrollierer(request: Request) -> Any | None:
    """The Kontrollierer only becomes available after the Phase-5 production wiring.

    If None: ``dispatch`` still creates the mission, but doesn't start it —
    the caller has to know that, or accept a 503 if it strictly needs the
    run to start.
    """
    return getattr(request.app.state, "kontrollierer", None)


# ---------------------------------------------------------------------------
# Body-Models
# ---------------------------------------------------------------------------


class DispatchBody(BaseModel):
    """Payload for POST /dispatch."""

    prompt: str
    language: Literal["de", "en"] = "de"
    confirmed: bool = False  # Phase-5 destructive_confirm gate (UI-Path)


class RerunBody(BaseModel):
    """Payload for POST /{mission_id}/rerun.

    Mirrors the ``confirmed`` field of :class:`DispatchBody` so a re-run cannot
    silently bypass the destructive-confirm gate the original dispatch passed.
    """

    confirmed: bool = False


# ---------------------------------------------------------------------------
# Listing + Detail
# ---------------------------------------------------------------------------


_VALID_STATES: frozenset[str] = frozenset(s.value for s in MissionState)


@router.get("")
async def list_missions(
    request: Request,
    state: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List of all missions, optionally filtered by state.

    Query parameters:
    - ``state``: comma-separated list of MissionState values (e.g. ``RUNNING,PENDING``).
    - ``limit``: max number of entries (server cap 1000).
    """
    mgr = _require_manager(request)

    limit = max(1, min(int(limit), 1000))
    state_filter: list[str] | None = None
    if state:
        candidates = [s.strip() for s in state.split(",") if s.strip()]
        unknown = [s for s in candidates if s not in _VALID_STATES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown mission states: {unknown}",
            )
        state_filter = candidates

    if state_filter is None:
        sql = (
            "SELECT id, prompt, state, language, created_ms, updated_ms, "
            "iteration, cost_usd FROM missions "
            "ORDER BY created_ms DESC LIMIT ?"
        )
        params: tuple[Any, ...] = (limit,)
    else:
        placeholders = ",".join("?" for _ in state_filter)
        sql = (
            "SELECT id, prompt, state, language, created_ms, updated_ms, "
            "iteration, cost_usd FROM missions "
            f"WHERE state IN ({placeholders}) "
            "ORDER BY created_ms DESC LIMIT ?"
        )
        params = (*state_filter, limit)

    cur = await mgr.store.conn.execute(sql, params)
    rows = await cur.fetchall()
    await cur.close()

    missions = [
        {
            "id": r[0],
            "prompt": r[1],
            "state": r[2],
            "language": r[3],
            "created_ms": int(r[4]),
            "updated_ms": int(r[5]),
            "iteration": int(r[6]),
            "cost_usd": float(r[7]),
        }
        for r in rows
    ]
    return {"missions": missions, "total": len(missions)}


@router.get("/{mission_id}")
async def get_mission(mission_id: str, request: Request) -> dict[str, Any]:
    """Detail view: header + all events + all critic verdicts.

    ``verdicts`` is the derived list of ``CriticVerdictReady`` payloads
    (for UI convenience — the frontend verdict tab doesn't have to filter itself).
    """
    mgr = _require_manager(request)

    view = await mgr.store.get_mission_view(mission_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    prompt, state, language, iteration, cost_usd = view

    # Read header timestamps separately (get_mission_view doesn't return them).
    cur = await mgr.store.conn.execute(
        "SELECT created_ms, updated_ms FROM missions WHERE id = ?",
        (mission_id,),
    )
    ts_row = await cur.fetchone()
    await cur.close()
    created_ms = int(ts_row[0]) if ts_row else 0
    updated_ms = int(ts_row[1]) if ts_row else 0

    envelopes: list[EventEnvelope] = await mgr.store.events_for_mission(
        mission_id
    )
    events_dump = [env.model_dump(mode="json") for env in envelopes]
    verdicts = [
        env.payload.model_dump(mode="json")
        for env in envelopes
        if env.payload.event_type == "CriticVerdictReady"
    ]

    # Phase 9 (Welle 4 UI): Worker-Snapshots for the UI columns
    # (model / cost / state-dir / logfile / reattach status). Pure aggregation
    # over the existing event stream — no schema drift, no live lookup.
    worker_snapshots = extract_worker_missions(envelopes)

    return {
        "mission": {
            "id": mission_id,
            "prompt": prompt,
            "state": state,
            "language": language,
            "iteration": iteration,
            "cost_usd": cost_usd,
            "created_ms": created_ms,
            "updated_ms": updated_ms,
        },
        "events": events_dump,
        "verdicts": verdicts,
        "worker_snapshots": worker_snapshots,
    }


# ---------------------------------------------------------------------------
# Dispatch + Cancel + Kill
# ---------------------------------------------------------------------------


@router.post("/dispatch", status_code=201, openapi_extra={"x-jarvis-dangerous": True})
async def dispatch_mission(
    body: DispatchBody,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    """Creates a mission and starts ``Kontrollierer.run_mission`` in the background.

    If no Kontrollierer is wired up (Phase-4 MVP mode), the mission is
    only dispatched (PENDING header + MissionDispatched event) — the caller
    gets the ``mission_id`` back and can follow the lifecycle via WS.

    Phase-5 safety gate: if the prompt looks destructive (rm -rf, drop table,
    git push --force, etc.) AND ``confirmed=false`` -> HTTP 409 with
    ``requires_confirm: true``. The UI shows an AlertDialog, the user clicks OK,
    and re-POSTs with ``confirmed: true``.
    """
    from fastapi.responses import JSONResponse
    from jarvis.missions.safety.destructive_confirm import is_destructive

    # Phase-5 destructive_confirm gate (UI path; the voice path isn't active yet)
    if not body.confirmed:
        is_destr, det = is_destructive(body.prompt)
        if is_destr and det is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "requires_confirm": True,
                    "pattern_id": det.pattern_id,
                    "matched_text": det.matched_text,
                    "target_hint": det.target_hint,
                    "warning": (
                        "Destructive mission detected. Re-POST with "
                        '"confirmed": true to proceed.'
                    ),
                },
            )

    mgr = _require_manager(request)
    kontrollierer = _optional_kontrollierer(request)

    mission_id = await mgr.dispatch(prompt=body.prompt, language=body.language)

    if kontrollierer is not None:
        background_tasks.add_task(kontrollierer.run_mission, mission_id)
        started = True
    else:
        logger.info(
            "dispatch_mission %s: no Kontrollierer wired up — "
            "mission stays PENDING until the Phase-5 production wiring",
            mission_id,
        )
        started = False

    return {"mission_id": mission_id, "started": str(started).lower()}


@router.post("/{mission_id}/cancel", openapi_extra={"x-jarvis-dangerous": True})
async def cancel_mission(mission_id: str, request: Request) -> dict[str, Any]:
    """Cancel a mission: terminal state transition + kill the in-flight run.

    Order matters: the DB state flips to CANCELLED first so the dying
    orchestrator task cannot race a late APPROVED/FAILED transition, then
    the in-flight ``run_mission`` asyncio task is cancelled (the TaskGroup
    propagation closes the per-worker Job Objects, killing the worker
    subprocesses). Returns ``409`` when the mission is already in a
    terminal state (APPROVED/FAILED/CANCELLED/TIMED_OUT) — the state
    machine raises that as ``IllegalStateTransition``.
    """
    mgr = _require_manager(request)
    view = await mgr.mission(mission_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    try:
        env = await mgr.transition_state(
            mission_id,
            MissionState.CANCELLED,
            reason="ui_cancel",
            source_actor="ui",
        )
    except IllegalStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    # Best-effort: abort the in-flight orchestrator task (if any). A missing
    # Kontrollierer (or one without the method) degrades to the pure state
    # flip — the pre-feature behaviour.
    worker_killed = False
    kontrollierer = _optional_kontrollierer(request)
    canceller = getattr(kontrollierer, "cancel_running_mission", None)
    if canceller is not None:
        try:
            worker_killed = bool(canceller(mission_id))
        except Exception:  # noqa: BLE001
            logger.exception(
                "cancel_running_mission(%s) failed — state is already "
                "CANCELLED, the run dies at its next transition",
                mission_id,
            )

    # Canonical terminal event: recovery reconciliation and the voice
    # announcer key off MissionCancelled (a MissionStateChanged alone is
    # not a terminal-event marker).
    cancel_env = EventEnvelope(
        mission_id=mission_id,
        source_actor="ui",
        ts_ms=now_ms(),
        payload=MissionCancelled(cascade=worker_killed, reason="ui_cancel"),
    )
    await mgr.store.append_and_publish(cancel_env)

    return {
        "ok": True,
        "mission_id": mission_id,
        "state": MissionState.CANCELLED.value,
        "event_seq": env.seq,
        "worker_killed": worker_killed,
    }


# States from which a mission may be re-run. APPROVED is deliberately excluded
# (a successful mission has nothing to continue/restart). The terminal source
# mission is never mutated — the re-run is a fresh PENDING mission linked back
# via ``parent_mission_id``, so no state-machine transition is needed here.
_RERUNNABLE_STATES: frozenset[MissionState] = frozenset(
    {
        MissionState.CANCELLED,
        MissionState.FAILED,
        MissionState.TIMED_OUT,
    }
)

# Terminal mission-state *values* (string form, as stored in the header). A
# re-run child counts as "live" while its state is NOT in this set. Unknown
# strings are treated as live (conservative: block a duplicate rather than
# spawn one). Derived from the single source of truth in the state machine.
_TERMINAL_STATE_VALUES: frozenset[str] = frozenset(
    s.value for s in MissionState if is_terminal(s)
)


@router.post("/{mission_id}/rerun", openapi_extra={"x-jarvis-dangerous": True})
async def rerun_mission(
    mission_id: str,
    body: RerunBody,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    """Re-run a terminal mission by re-dispatching its original prompt.

    Used by the Outputs view: "Continue" a CANCELLED mission and "Restart" a
    FAILED/TIMED_OUT one. Both are the same operation — re-dispatch the stored
    prompt as a NEW mission linked to the source via ``parent_mission_id``. The
    source mission is left untouched as a permanent audit record; a fresh card
    appears in the Outputs view and runs.

    The audit ``action`` is derived from the source state, not supplied by the
    client: CANCELLED -> "continue", FAILED/TIMED_OUT -> "restart".

    Errors:
    - ``404`` when the mission is unknown.
    - ``409`` when the source state is not re-runnable (e.g. APPROVED).
    - ``409 {requires_confirm: true}`` when the stored prompt looks destructive
      and ``confirmed`` is false — same gate as ``/dispatch``. UI re-POSTs with
      ``confirmed: true`` after the user acknowledges.
    """
    from fastapi.responses import JSONResponse

    from jarvis.missions.safety.destructive_confirm import is_destructive

    mgr = _require_manager(request)

    view = await mgr.store.get_mission_view(mission_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    prompt, state_str, language = view[0], view[1], view[2]

    try:
        source_state = MissionState(state_str)
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail=f"Mission has an unknown state: {state_str!r}",
        ) from None

    if source_state not in _RERUNNABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Mission is not re-runnable from state {source_state.value}. "
                "Only CANCELLED, FAILED or TIMED_OUT missions can be re-run."
            ),
        )

    # Idempotency / liveness guard (forensic 2026-06-22, mission 019eefcb-cee2):
    # the source mission stays terminal forever as an audit record, so it is
    # re-runnable indefinitely — and a burst of /rerun POSTs (a click-storm or a
    # frontend re-render loop) used to dispatch one NEW child per request: nine
    # children, "one mission with nine sub-agents". A parent may have at most ONE
    # live (non-terminal) re-run child; while one exists, return it idempotently
    # instead of spawning a duplicate. This mirrors the spawn_worker tool's
    # cooldown gate, which guards the source_actor="hauptjarvis" voice path the
    # same way — the source_actor="ui" rerun path simply never had its
    # equivalent. Once the child reaches a terminal state, a fresh re-run is
    # allowed again (the guard is a liveness gate, not a permanent lock). Placed
    # before the destructive gate so a still-running, already-confirmed child is
    # never re-prompted for confirmation.
    live_children = [
        child_id
        for (child_id, child_state) in await mgr.store.find_child_missions(
            mission_id
        )
        if child_state not in _TERMINAL_STATE_VALUES
    ]
    if live_children:
        existing_child = live_children[0]
        action = (
            "continue" if source_state is MissionState.CANCELLED else "restart"
        )
        logger.info(
            "rerun_mission: parent %s already has a live re-run child %s — "
            "returning it idempotently instead of dispatching a duplicate",
            mission_id,
            existing_child,
        )
        return {
            "ok": True,
            "parent_mission_id": mission_id,
            "mission_id": existing_child,
            "action": action,
            "started": True,
            "deduplicated": True,
        }

    # Destructive re-gate: a re-run must not bypass the safety check the
    # original dispatch enforced. Same helper + same 409 shape as /dispatch.
    if not body.confirmed:
        is_destr, det = is_destructive(prompt)
        if is_destr and det is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "requires_confirm": True,
                    "pattern_id": det.pattern_id,
                    "matched_text": det.matched_text,
                    "target_hint": det.target_hint,
                    "warning": (
                        "Destructive mission detected. Re-POST with "
                        '"confirmed": true to proceed.'
                    ),
                },
            )

    # Derive intent from the source state for an accurate audit trail — never
    # trust a client-supplied label.
    if source_state is MissionState.CANCELLED:
        action = "continue"
        source_actor_reason = "ui_continue"
    else:
        action = "restart"
        source_actor_reason = "ui_restart"

    safe_language: Literal["de", "en"] = (
        language if language in ("de", "en") else "de"
    )
    new_mission_id = await mgr.dispatch(
        prompt=prompt,
        language=safe_language,
        source_actor="ui",
        parent_mission_id=mission_id,
    )
    logger.info(
        "rerun_mission: %s (%s) of source %s as new mission %s",
        action,
        source_actor_reason,
        mission_id,
        new_mission_id,
    )

    kontrollierer = _optional_kontrollierer(request)
    started = False
    if kontrollierer is not None:
        background_tasks.add_task(kontrollierer.run_mission, new_mission_id)
        started = True

    return {
        "ok": True,
        "parent_mission_id": mission_id,
        "mission_id": new_mission_id,
        "action": action,
        "started": started,
    }


@router.post("/kill/{worker_id}", openapi_extra={"x-jarvis-dangerous": True})
async def kill_worker(worker_id: str, request: Request) -> dict[str, Any]:
    """Best-effort worker kill (stub for the Phase-4 MVP).

    The full Job Object map + kill-switch wiring lands in Phase 5. Currently:
    - If ``app.state.kontrollierer`` has a ``kill_worker(worker_id)`` method,
      it gets called.
    - Otherwise: ``503`` noting that the feature isn't wired up yet.
    """
    kontrollierer = _optional_kontrollierer(request)
    if kontrollierer is None:
        raise HTTPException(
            status_code=503,
            detail="Kontrollierer not available — kill not wired up yet",
        )
    killer = getattr(kontrollierer, "kill_worker", None)
    if killer is None:
        return {
            "killed": False,
            "worker_id": worker_id,
            "reason": "kontrollierer.kill_worker not implemented (Phase-5 Wiring)",
        }
    try:
        result = await killer(worker_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("kill_worker(%s) failed", worker_id)
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {"killed": bool(result), "worker_id": worker_id, "reason": "ok"}
