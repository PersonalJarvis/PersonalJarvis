"""FastAPI routes for the first-time onboarding guide.

All reads fail open (never 5xx): a missing/corrupt state file or a failing
first-run probe reports a safe "incomplete" default so the UI gate can decide
its own behaviour. There is NO server-side wake-word trademark rejection —
the legal posture rests on the accepted Terms + the wake-word acknowledgment,
not on blocking.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from jarvis.core.config import is_first_run
from jarvis.setup import state as st
from jarvis.setup.onboarding_meta import (
    CURRENT_TERMS_VERSION,
    ONBOARDING_STEPS,
    WAKE_WORD_LEGAL_REFERENCES,
    read_terms_text,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Tests override this to redirect the state file; production leaves it None
# (state.py resolves the default data/setup_state.json).
_STATE_PATH_OVERRIDE: Path | None = None


def _path() -> Path | None:
    return _STATE_PATH_OVERRIDE


class StepBody(BaseModel):
    step: str
    skipped: list[str] | None = None


def _force_onboarding() -> bool:
    return bool(os.environ.get("JARVIS_FORCE_ONBOARDING"))


def _safe_state_payload() -> dict:
    """Build the GET /state payload. Never raises — fails open to 'incomplete'."""
    try:
        s = st.get_onboarding_state(_path())
    except Exception as exc:  # noqa: BLE001 — UI must keep working
        log.warning("onboarding_get_state_failed: %s", exc, exc_info=True)
        s = {
            "completed_at": None, "current_step": None, "skipped_steps": [],
            "terms_accepted_at": None, "terms_version": None,
            "wake_word_acknowledged_at": None,
        }

    legacy_done = False
    try:
        legacy_done = not is_first_run()
    except Exception as exc:  # noqa: BLE001
        log.debug("onboarding: is_first_run failed: %s", exc)

    completed = (s["completed_at"] is not None) or legacy_done
    if _force_onboarding():
        completed = False

    return {
        "completed": completed,
        "current_step": s["current_step"],
        "skipped_steps": s["skipped_steps"],
        "terms": {
            "accepted": s["terms_accepted_at"] is not None,
            "accepted_version": s["terms_version"],
            "current_version": CURRENT_TERMS_VERSION,
        },
        "wake_word_acknowledged": s["wake_word_acknowledged_at"] is not None,
        "legal_references": WAKE_WORD_LEGAL_REFERENCES,
        "steps": ONBOARDING_STEPS,
    }


@router.get("/state")
async def get_state() -> dict:
    return _safe_state_payload()


@router.get("/terms")
async def get_terms() -> dict:
    return {"version": CURRENT_TERMS_VERSION, "text": read_terms_text()}


@router.post("/step")
async def post_step(body: StepBody) -> dict:
    st.set_onboarding_step(body.step, skipped=body.skipped, path=_path())
    return {"ok": True}


@router.post("/accept-terms")
async def post_accept_terms() -> dict:
    st.accept_terms(CURRENT_TERMS_VERSION, path=_path())
    return {"ok": True, "version": CURRENT_TERMS_VERSION}


@router.post("/acknowledge-wake-word")
async def post_ack_wake_word() -> dict:
    st.acknowledge_wake_word(_path())
    return {"ok": True}


@router.post("/complete")
async def post_complete() -> dict:
    st.mark_onboarding_complete(_path())
    return {"ok": True}


__all__ = ["router"]
