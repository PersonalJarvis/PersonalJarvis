"""REST API of the Jarvis Verse level system (``jarvis/progression``).

- ``GET  /api/progression``          → every subject's level, the rulebook and recent awards
- ``POST /api/progression/actions``  → report a world action from the Verse (metered server-side)

Live awards travel as ``ProgressionAwarded`` over the WebSocket; this API is
the first read and the catch-up after a reconnect.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.progression.rules import (
    MAX_LEVEL,
    REWARDS,
    RULES,
    TITLES,
    total_xp_for_level,
)
from jarvis.progression.service import ProgressionService, UnknownAction

router = APIRouter(prefix="/api/progression", tags=["progression"])


def _service(request: Request) -> ProgressionService:
    service = getattr(request.app.state, "progression", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Level system not available")
    return service


def _rulebook() -> dict[str, Any]:
    return {
        "max_level": MAX_LEVEL,
        "level_xp": [total_xp_for_level(level) for level in range(1, MAX_LEVEL + 1)],
        "rules": [
            {
                "source": r.source,
                "kind": r.kind,
                "xp": r.xp,
                "trigger": r.trigger,
                "cooldown_s": r.cooldown_s,
                "daily_cap": r.daily_cap,
            }
            for r in RULES
        ],
        "rewards": [
            {
                "reward_id": r.reward_id,
                "slot": r.slot,
                "levels": {"person": r.person, "agent": r.agent, "pet": r.pet},
            }
            for r in REWARDS
        ],
        "titles": {
            kind: [{"level": lvl, "title": t} for lvl, t in bands]
            for kind, bands in TITLES.items()
        },
    }


@router.get("", summary="Levels of the person, the pet and every agent")
async def get_progression(
    request: Request,
    after_seq: int = Query(0, ge=0, description="Only awards newer than this sequence number"),
) -> dict[str, Any]:
    service = _service(request)
    subjects, recent, latest = await asyncio.gather(
        asyncio.to_thread(service.snapshot),
        asyncio.to_thread(service.store.recent, after_seq=after_seq, limit=50),
        asyncio.to_thread(service.store.latest_seq),
    )
    return {
        "subjects": subjects,
        "pet_id": service.current_pet(),
        "recent": recent,
        "latest_seq": latest,
        **_rulebook(),
    }


class WorldAction(BaseModel):
    action: str = Field(..., max_length=40, description="A world-action source from the rulebook")
    ref: str = Field(
        "", max_length=40, description="The action's subject, e.g. the floor discovered"
    )


@router.post("/actions", summary="Report a small action inside the Jarvis Verse")
async def post_action(body: WorldAction, request: Request) -> dict[str, Any]:
    service = _service(request)
    try:
        award = await service.report_action(body.action, ref=body.ref)
    except UnknownAction as exc:
        raise HTTPException(status_code=422, detail=f"unknown action: {exc}") from exc
    return {"awarded": award.to_dict() if award is not None else None}
