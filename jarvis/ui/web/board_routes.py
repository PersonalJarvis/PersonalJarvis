"""REST-API fuer das Personal-Mastery-Dashboard.

Endpoints:

- ``GET  /api/board/personal/summary``       → Totals + 30-Tage-Fenster
- ``GET  /api/board/personal/heatmap``       → GitHub-Style-Grid (?days=365)
- ``GET  /api/board/personal/tools``         → Tool-Usage-Histogramm
- ``GET  /api/board/personal/records``       → Personal Records
- ``POST /api/board/personal/refresh``       → Manueller Aggregator-Run
- ``GET  /api/board/achievements``           → Alle Specs mit Unlock-Status
- ``GET  /api/board/bio``                    → Aktuelle AI-Bio + Alter
- ``POST /api/board/bio/regenerate``         → Manuell neue Bio generieren
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from jarvis.board.aggregator import BoardAggregator
from jarvis.board.evaluator import AchievementEvaluator
from jarvis.board.profile import BioGenerator, BioStore
from jarvis.board.store import BoardStore
from jarvis.core.events import BioFeedbackRecorded

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/board/personal", tags=["board"])
board_router = APIRouter(prefix="/api/board", tags=["board"])


def _require_store(request: Request) -> BoardStore:
    store = getattr(request.app.state, "board_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="BoardStore nicht verfuegbar")
    return store


def _require_aggregator(request: Request) -> BoardAggregator:
    agg = getattr(request.app.state, "board_aggregator", None)
    if agg is None:
        raise HTTPException(status_code=503, detail="BoardAggregator nicht verfuegbar")
    return agg


# Freshness window for the live-indicator endpoints. A poll re-aggregates from
# sessions.db at most once per this interval, so newly spoken words appear
# within a poll cycle instead of staying frozen until the 6 h batch tick.
_FRESHEN_TTL_S = 6.0


async def _freshen(request: Request, ttl_s: float = _FRESHEN_TTL_S) -> None:
    """Re-aggregate before a read if the cached stats are older than ``ttl_s``.

    Never raises — a failed freshen just serves the previous (slightly older)
    numbers rather than 500-ing the dashboard.
    """
    agg = getattr(request.app.state, "board_aggregator", None)
    if agg is None:
        return
    try:
        await asyncio.to_thread(agg.run_if_stale, ttl_s)
    except Exception:  # noqa: BLE001
        log.debug("Board freshen-on-read failed — serving cache", exc_info=True)


def _require_evaluator(request: Request) -> AchievementEvaluator:
    ev = getattr(request.app.state, "achievement_evaluator", None)
    if ev is None:
        raise HTTPException(status_code=503, detail="AchievementEvaluator nicht verfuegbar")
    return ev


def _require_bio_generator(request: Request) -> BioGenerator:
    gen = getattr(request.app.state, "bio_generator", None)
    if gen is None:
        raise HTTPException(status_code=503, detail="BioGenerator nicht verfuegbar")
    return gen


def _require_bio_store(request: Request) -> BioStore:
    store = getattr(request.app.state, "bio_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="BioStore nicht verfuegbar")
    return store


# ----------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------

class SummaryTotals(BaseModel):
    tasks_completed: int
    tasks_failed: int
    voice_commands: int
    hours_saved: float
    activity_events: int
    conversation_hours: float
    user_words: int = 0
    jarvis_words: int = 0
    session_count: int = 0
    active_days: int
    first_day: str | None


class SummaryWindow(BaseModel):
    tasks_completed: int
    tasks_failed: int
    voice_commands: int
    hours_saved: float
    activity_events: int
    conversation_hours: float
    user_words: int = 0
    jarvis_words: int = 0
    session_count: int = 0
    voice_first_try_rate: float | None
    unique_tools: int


class SummaryResponse(BaseModel):
    window_days: int
    totals: SummaryTotals
    window: SummaryWindow
    streak_days: int
    longest_streak: int = 0


class HeatmapCell(BaseModel):
    date: str
    tasks_completed: int
    tasks_failed: int
    activity_events: int
    conversation_hours: float
    user_words: int = 0
    jarvis_words: int = 0


class HeatmapResponse(BaseModel):
    start: str
    end: str
    days: int
    cells: list[HeatmapCell]


class CategoryEntry(BaseModel):
    category: str
    count: int


class CategoriesResponse(BaseModel):
    window_days: int | None
    total: int
    categories: list[CategoryEntry]


class ToolHistogramEntry(BaseModel):
    tool: str
    days_used: int


class ToolsResponse(BaseModel):
    window_days: int
    total_unique: int
    histogram: list[ToolHistogramEntry]


class PersonalRecordEntry(BaseModel):
    metric: str
    value: float
    achieved_on: str
    context: dict


class RecordsResponse(BaseModel):
    records: list[PersonalRecordEntry]


class RefreshResponse(BaseModel):
    ok: bool
    triggered: bool


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@router.get("/summary", response_model=SummaryResponse)
async def personal_summary(
    request: Request,
    window_days: int = Query(30, ge=1, le=365),
) -> SummaryResponse:
    store = _require_store(request)
    await _freshen(request)
    data = await asyncio.to_thread(store.summary, window_days=window_days)
    return SummaryResponse.model_validate(data)


@router.get("/heatmap", response_model=HeatmapResponse)
async def personal_heatmap(
    request: Request,
    days: int = Query(365, ge=7, le=730),
) -> HeatmapResponse:
    store = _require_store(request)
    await _freshen(request)
    data = await asyncio.to_thread(store.heatmap, days=days)
    return HeatmapResponse.model_validate(data)


@router.get("/tools", response_model=ToolsResponse)
async def personal_tools(
    request: Request,
    window_days: int = Query(90, ge=7, le=365),
) -> ToolsResponse:
    store = _require_store(request)
    data = await asyncio.to_thread(store.tools, window_days=window_days)
    return ToolsResponse.model_validate(data)


@router.get("/categories", response_model=CategoriesResponse)
async def personal_categories(
    request: Request,
    window_days: int | None = Query(None, ge=1, le=365),
) -> CategoriesResponse:
    """Usage broken down by the six task categories ("what did you use Jarvis
    for"). ``window_days`` omitted = all history.
    """
    store = _require_store(request)
    await _freshen(request)
    data = await asyncio.to_thread(store.categories, window_days=window_days)
    return CategoriesResponse.model_validate(data)


@router.get("/records", response_model=RecordsResponse)
async def personal_records(request: Request) -> RecordsResponse:
    store = _require_store(request)
    data = await asyncio.to_thread(store.records)
    return RecordsResponse.model_validate(data)


@router.post("/refresh", response_model=RefreshResponse)
async def personal_refresh(request: Request) -> RefreshResponse:
    """Manuell den Aggregator anwerfen. Synchron, bis DB geupsertet ist.

    Wird vom UI-„Refresh"-Button ausgeloest. Keine Long-Poll-Illusion —
    der Request kehrt erst zurueck, wenn die DB aktuell ist. Das ist bei
    <10 k Events ein Bruchteil einer Sekunde.
    """
    agg = _require_aggregator(request)
    try:
        await asyncio.to_thread(agg.run)
        return RefreshResponse(ok=True, triggered=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("manueller Board-Refresh fehlgeschlagen")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# Phase B — Achievements + Bio
# ----------------------------------------------------------------------

class AchievementItem(BaseModel):
    id: str
    title: str
    description: str
    tier: str
    unlocked_at: str | None
    evidence: dict


class AchievementListResponse(BaseModel):
    total: int
    unlocked: int
    items: list[AchievementItem]


class BioResponse(BaseModel):
    text: str | None
    generated_at: str | None
    model_used: str | None
    triggered_by: str | None
    staleness_days: int | None


class BioRegenerateRequest(BaseModel):
    memory_text: str = ""
    soul_text: str = ""


class BioRegenerateResponse(BaseModel):
    ok: bool
    generated_at: str | None
    text: str | None
    reason: str | None = None


class BioFeedbackRequest(BaseModel):
    """Reagier-Klick unter einer Bio. Das Signal kalibriert die NAECHSTE
    Generation; kein Sofort-Regenerate.
    """
    bio_generated_at: str
    kind: Literal["trifft", "trifft_nicht", "haerter"]


class BioFeedbackResponse(BaseModel):
    ok: bool
    reason: str | None = None


@board_router.get("/achievements", response_model=AchievementListResponse)
async def list_achievements(request: Request) -> AchievementListResponse:
    ev = _require_evaluator(request)
    items = await asyncio.to_thread(ev.list_all)
    unlocked = sum(1 for i in items if i["unlocked_at"] is not None)
    return AchievementListResponse(
        total=len(items),
        unlocked=unlocked,
        items=[AchievementItem.model_validate(i) for i in items],
    )


@board_router.get("/bio", response_model=BioResponse)
async def get_bio(request: Request) -> BioResponse:
    bio_store = _require_bio_store(request)
    latest = await asyncio.to_thread(bio_store.latest)
    if latest is None:
        return BioResponse(
            text=None, generated_at=None, model_used=None,
            triggered_by=None, staleness_days=None,
        )
    staleness = _staleness_days(latest["generated_at"])
    return BioResponse(
        text=latest["text"],
        generated_at=latest["generated_at"],
        model_used=latest.get("model_used"),
        triggered_by=latest.get("triggered_by"),
        staleness_days=staleness,
    )


@board_router.post("/bio/regenerate", response_model=BioRegenerateResponse)
async def regenerate_bio(
    request: Request,
    payload: BioRegenerateRequest | None = None,
) -> BioRegenerateResponse:
    """Triggert eine neue Bio.

    Bei Brain-Outage: 200 mit ``ok=False`` + ``reason=...``. Kein 500 —
    die alte Bio ist ja noch verfuegbar via ``GET /bio``.
    """
    gen = _require_bio_generator(request)
    body = payload or BioRegenerateRequest()
    try:
        result = await gen.generate_bio(
            memory_text=body.memory_text,
            soul_text=body.soul_text,
            triggered_by="manual",
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Bio-Regenerate fehlgeschlagen")
        return BioRegenerateResponse(
            ok=False, generated_at=None, text=None, reason=str(exc),
        )
    if result is None:
        return BioRegenerateResponse(
            ok=False, generated_at=None, text=None,
            reason="Brain nicht verfuegbar — alte Bio bleibt",
        )
    return BioRegenerateResponse(
        ok=True,
        generated_at=result["generated_at"],
        text=result["text"],
    )


@board_router.post("/bio/feedback", response_model=BioFeedbackResponse)
async def post_bio_feedback(
    request: Request,
    payload: BioFeedbackRequest,
) -> BioFeedbackResponse:
    """Schreibt einen Reagier-Klick (``Trifft`` / ``Trifft nicht`` / ``Haerter``).

    Der Klick wird als Tone-Vector aggregiert und in den Prompt der naechsten
    Bio-Generation gefuettert (siehe ``BioStore.recent_feedback`` +
    ``prompts.render_bio_prompt:_render_feedback``).
    """
    bio_store = _require_bio_store(request)
    try:
        await asyncio.to_thread(
            bio_store.record_feedback, payload.bio_generated_at, payload.kind,
        )
    except ValueError as exc:
        return BioFeedbackResponse(ok=False, reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Bio-Feedback persistieren fehlgeschlagen")
        return BioFeedbackResponse(ok=False, reason=str(exc))

    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        try:
            await bus.publish(BioFeedbackRecorded(
                bio_generated_at=payload.bio_generated_at,
                kind=payload.kind,
                source_layer="ui.board",
            ))
        except Exception:  # noqa: BLE001
            log.debug("Bio-Feedback Bus-Publish fehlgeschlagen — non-fatal")
    return BioFeedbackResponse(ok=True)


def _staleness_days(generated_at_iso: str | None) -> int | None:
    if not generated_at_iso:
        return None
    try:
        dt = datetime.fromisoformat(generated_at_iso)
    except ValueError:
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return max(0, (now - dt).days)
