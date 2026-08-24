"""Starter plans and the "ready" moment (``/api/setup``).

Own module on purpose: ``setup_routes.py`` is the Obsidian setup wizard and
shares the ``/api/setup`` prefix.

    GET  /api/setup/starter-plans          → plans + which of their keys are saved
    POST /api/setup/starter-plans/{id}     → remember the plan the user picked
    GET  /api/setup/readiness              → is the active voice mode fully set up?
    POST /api/setup/readiness/celebrated   → the one-time "all set" note was shown

Readiness reuses the section-health rollup (the real per-tier probes) instead
of a second opinion: "ready" means every section the mode needs reports
``ok``. Reads fail open — a broken probe reports not-ready, never a 5xx.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from jarvis.core import config as cfg_mod
from jarvis.setup import state as st
from jarvis.setup.starter_plans import (
    CUSTOM_PLAN_ID,
    STARTER_PLANS,
    get_plan,
    plan_ready_sections,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])

# Tests redirect the state file; production resolves the default.
_STATE_PATH_OVERRIDE: Path | None = None


def _path() -> Path | None:
    return _STATE_PATH_OVERRIDE


def _family_label(family: str) -> str:
    from .provider_spec import get_spec

    spec = get_spec(family)
    return spec.label if spec else family


def _key_slots(families: tuple[str, ...]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for family in families:
        slot = cfg_mod.secret_family_primary_slot(family)
        if slot is None:
            continue
        try:
            present = bool(cfg_mod.get_provider_secret(family))
        except Exception as exc:  # noqa: BLE001 — a locked keyring reads as "no key"
            log.debug("starter-plan key probe failed for %s: %s", family, exc)
            present = False
        slots.append(
            {"family": family, "slot": slot, "label": _family_label(family), "present": present}
        )
    return slots


@router.get("/starter-plans")
async def list_starter_plans() -> dict[str, Any]:
    plans = []
    for plan in STARTER_PLANS:
        slots = _key_slots(plan.key_families)
        plans.append(
            {
                "id": plan.id,
                "label": plan.label,
                "summary": plan.summary,
                "mode": plan.mode,
                "recommended": plan.recommended,
                "assignments": dict(plan.assignments),
                "key_slots": slots,
                "keys_complete": bool(slots) and all(s["present"] for s in slots),
                "ready_sections": list(plan_ready_sections(plan.mode)),
            }
        )
    return {"plans": plans, "selected": st.get_starter_plan(_path()), "custom_id": CUSTOM_PLAN_ID}


@router.post("/starter-plans/{plan_id}")
async def select_starter_plan(plan_id: str) -> dict[str, Any]:
    if plan_id != CUSTOM_PLAN_ID and get_plan(plan_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown starter plan: {plan_id}")
    st.set_starter_plan(plan_id, _path())
    return {"ok": True, "selected": plan_id}


def _voice_mode(request: Request) -> str:
    cfg = getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)
    mode = str(getattr(getattr(cfg, "voice", None), "mode", "") or "").strip().lower()
    return mode if mode in ("pipeline", "realtime") else "realtime"


@router.get("/readiness")
async def readiness(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Whether every section the active voice mode needs answers ``ok``.

    ``celebrated`` tells the UI whether the one-time note was already shown;
    the UI shows it exactly once, when ``ready`` first turns true.
    """
    mode = _voice_mode(request)
    required = plan_ready_sections(mode)
    sections: dict[str, Any] = {}
    try:
        from . import provider_routes

        snapshot = await provider_routes.section_health(request, refresh=refresh)
        raw = getattr(snapshot, "sections", None) or {}
        for name in required:
            entry = raw.get(name)
            if entry is None:
                sections[name] = {"status": "unknown", "reason": "missing", "detail": ""}
            elif hasattr(entry, "model_dump"):
                sections[name] = entry.model_dump()
            elif isinstance(entry, dict):
                sections[name] = entry
            else:
                sections[name] = {"status": str(getattr(entry, "status", "unknown"))}
    except Exception as exc:  # noqa: BLE001 — readiness is advisory, never a 5xx
        log.debug("readiness: section health unavailable (%s)", exc)
        sections = {
            name: {"status": "unknown", "reason": "error", "detail": ""} for name in required
        }
    ready = bool(required) and all(sections[name].get("status") == "ok" for name in required)
    return {
        "mode": mode,
        "required": list(required),
        "sections": sections,
        "ready": ready,
        "celebrated": st.is_ready_celebrated(_path()),
        "starter_plan": st.get_starter_plan(_path()),
    }


@router.post("/readiness/celebrated")
async def mark_celebrated() -> dict[str, Any]:
    st.mark_ready_celebrated(_path())
    return {"ok": True}


__all__ = ["router"]
