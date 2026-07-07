"""FastAPI routes for the first-time onboarding guide.

All reads fail open (never 5xx): a missing/corrupt state file or a failing
first-run probe reports a safe "incomplete" default so the UI gate can decide
its own behaviour. There is NO server-side wake-word trademark rejection —
the legal posture rests on the accepted Terms + the wake-word acknowledgment,
not on blocking.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Response
from pydantic import BaseModel

from jarvis.setup import state as st
from jarvis.setup.onboarding_fastpath import state_payload as _shared_state_payload
from jarvis.setup.onboarding_meta import CURRENT_TERMS_VERSION, read_terms_text

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


def _safe_state_payload() -> dict:
    """Build the GET /state payload — shared with the fast-boot handler
    (jarvis.setup.onboarding_fastpath) so the warming window and the real app
    can never disagree. Never raises — fails open to 'incomplete'."""
    return _shared_state_payload(_path())


@router.get("/state")
async def get_state(response: Response) -> dict:
    # Same no-cache guarantee the fast-boot handler gives: the gate must never
    # act on a cached completed/incomplete verdict.
    response.headers["cache-control"] = "no-store, max-age=0"
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
