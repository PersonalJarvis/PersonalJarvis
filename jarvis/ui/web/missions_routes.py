"""REST-API fuer das Phase-6-Mission-Subsystem.

Endpoints:
- ``GET    /api/missions``                 → Liste aller Missions (filterbar).
- ``GET    /api/missions/{id}``            → Mission-Detail + Events + Verdicts.
- ``POST   /api/missions/dispatch``        → Neue Mission anlegen + Run starten.
- ``POST   /api/missions/{id}/cancel``     → Best-effort State-Transition.
- ``POST   /api/missions/kill/{worker}``   → Worker-Stub (volle Job-Object-Logik
                                              kommt in Phase-5-Production-Wiring).

Pattern wie ``tasks_routes.py``:
- Resource (``MissionManager``/``Kontrollierer``) wird beim Server-Start in
  ``app.state.<name>`` gehaengt.
- Wenn nicht gesetzt → ``HTTPException(503)``.
- Pydantic-Body-Models inline, kein extra Schema-Modul.
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
)
from jarvis.ui.web.missions_worker import extract_worker_missions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/missions", tags=["missions"])


# ---------------------------------------------------------------------------
# DI-Helpers
# ---------------------------------------------------------------------------


def _require_manager(request: Request) -> MissionManager:
    """MissionManager aus app.state oder 503."""
    mgr = getattr(request.app.state, "mission_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=503, detail="MissionManager nicht verfuegbar"
        )
    return mgr


def _optional_kontrollierer(request: Request) -> Any | None:
    """Kontrollierer ist erst nach Phase-5-Production-Wiring verfuegbar.

    Wenn None: ``dispatch`` legt zwar die Mission an, startet sie aber
    nicht — der Caller muss das wissen oder einen 503 akzeptieren, wenn er
    den Run-Start zwingend braucht.
    """
    return getattr(request.app.state, "kontrollierer", None)


# ---------------------------------------------------------------------------
# Body-Models
# ---------------------------------------------------------------------------


class DispatchBody(BaseModel):
    """Payload fuer POST /dispatch."""

    prompt: str
    language: Literal["de", "en"] = "de"
    confirmed: bool = False  # Phase-5 destructive_confirm gate (UI-Path)


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
    """Liste aller Missions, optional gefiltert nach State.

    Query-Parameter:
    - ``state``: Komma-Liste aus MissionState-Werten (z.B. ``RUNNING,PENDING``).
    - ``limit``: max. Anzahl Eintraege (Server-Cap 1000).
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
                detail=f"Unbekannte mission-states: {unknown}",
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
    """Detail-View: Header + alle Events + alle Critic-Verdicts.

    ``verdicts`` ist die abgeleitete Liste der ``CriticVerdictReady``-Payloads
    (zur UI-Convenience — der Frontend-Verdict-Tab muss nicht selbst filtern).
    """
    mgr = _require_manager(request)

    view = await mgr.store.get_mission_view(mission_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Mission nicht gefunden")
    prompt, state, language, iteration, cost_usd = view

    # Header-Timestamps separat lesen (get_mission_view liefert sie nicht).
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

    # Phase 9 (Welle 4 UI): OpenClaw-Worker-Snapshots fuer die UI-Spalten
    # (Modell / Cost / State-Dir / Logfile / Reattach-Status). Pure Aggregation
    # ueber den vorhandenen Event-Stream — kein Schema-Drift, kein Live-Lookup.
    openclaw_workers = extract_worker_missions(envelopes)

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
        "openclaw_workers": openclaw_workers,
    }


# ---------------------------------------------------------------------------
# Dispatch + Cancel + Kill
# ---------------------------------------------------------------------------


@router.post("/dispatch", status_code=201)
async def dispatch_mission(
    body: DispatchBody,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    """Legt eine Mission an und startet ``Kontrollierer.run_mission`` im Hintergrund.

    Wenn kein Kontrollierer verdrahtet ist (Phase-4-MVP-Mode), wird die Mission
    nur dispatched (PENDING-Header + MissionDispatched-Event) — der Caller
    bekommt die ``mission_id`` zurueck und kann den Lifecycle ueber WS verfolgen.

    Phase-5 Safety-Gate: wenn der Prompt destruktiv aussieht (rm -rf, drop table,
    git push --force, etc.) UND ``confirmed=false`` -> HTTP 409 mit
    ``requires_confirm: true``. UI zeigt AlertDialog, User klickt OK,
    Re-POST mit ``confirmed: true``.
    """
    from fastapi.responses import JSONResponse
    from jarvis.missions.safety.destructive_confirm import is_destructive

    # Phase-5 destructive_confirm gate (UI-Path, voice-Path noch nicht aktiv)
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
                        "Destruktive Mission erkannt. Re-POST mit "
                        '"confirmed": true zum Bestaetigen.'
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
            "dispatch_mission %s: kein Kontrollierer verdrahtet — "
            "Mission bleibt PENDING bis Phase-5-Production-Wiring",
            mission_id,
        )
        started = False

    return {"mission_id": mission_id, "started": str(started).lower()}


@router.post("/{mission_id}/cancel")
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
        raise HTTPException(status_code=404, detail="Mission nicht gefunden")

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


@router.post("/kill/{worker_id}")
async def kill_worker(worker_id: str, request: Request) -> dict[str, Any]:
    """Best-effort Worker-Kill (Stub fuer Phase-4-MVP).

    Volle Job-Object-Map + KillSwitch-Wiring kommt in Phase-5. Aktuell:
    - Wenn ``app.state.kontrollierer`` eine ``kill_worker(worker_id)``-Methode
      hat, wird sie aufgerufen.
    - Sonst: ``503`` mit Hinweis dass das Feature noch nicht verdrahtet ist.
    """
    kontrollierer = _optional_kontrollierer(request)
    if kontrollierer is None:
        raise HTTPException(
            status_code=503,
            detail="Kontrollierer nicht verfuegbar — kill noch nicht verdrahtet",
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
        logger.exception("kill_worker(%s) fehlgeschlagen", worker_id)
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {"killed": bool(result), "worker_id": worker_id, "reason": "ok"}
