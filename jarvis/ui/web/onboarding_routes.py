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
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
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


def _schedule_app_shutdown(request: Request) -> None:
    """Quit the whole app shortly after the decline response flushes.

    Desktop hosts get the clean ``DesktopApp.request_quit()`` sequence (mark
    quit → destroy window → hard-exit backstop, same as the restart path but
    without a relauncher). Headless hosts have no window to destroy, so a
    short-delayed hard exit is the honest equivalent — the HTTP 200 reaches
    the browser first, then the server is gone.
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "request_quit", None)
    if callable(fn):
        try:
            if fn():
                return
        except Exception:  # noqa: BLE001 — a decline must still end the process
            log.warning("desktop quit failed; falling back to hard exit", exc_info=True)
    threading.Timer(0.8, os._exit, args=(0,)).start()


@router.post("/decline-terms")
async def post_decline_terms(request: Request) -> dict:
    """Declining the Terms quits the app (design 2026-07-09).

    The install one-liner never asks anything, so the Terms gate on first
    launch is the ONE consent moment — declining it must not leave a
    half-running assistant behind. Nothing is persisted on decline: the next
    start shows the gate again. 409 once the Terms are accepted — the gate no
    longer exists then, and this endpoint must not double as a kill switch.
    """
    if _safe_state_payload()["terms"]["accepted"]:
        raise HTTPException(status_code=409, detail="terms already accepted")
    _schedule_app_shutdown(request)
    return {"ok": True, "quitting": True}


@router.post("/acknowledge-wake-word")
async def post_ack_wake_word() -> dict:
    st.acknowledge_wake_word(_path())
    return {"ok": True}


def _schedule_fresh_restart(request: Request) -> bool:
    """Restart the app in a fresh process right after onboarding completes.

    The first launch happens straight out of the installer: the process the
    user just walked through onboarding has been running since BEFORE the
    language, wake word, and providers were configured. A relauncher restart
    (``DesktopApp.request_restart()`` — the same detached helper the
    self-update uses) re-initializes every subsystem from the now-complete
    config, so the assistant never lingers half-warm. The completion marker
    is persisted before this is called, so the fresh instance reads
    completed=true and can never re-open the gate. Headless hosts have no
    window — ``request_restart`` reports False and onboarding simply
    completes in place. Best-effort: a restart failure never fails the
    completion request.
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "request_restart", None)
    if not callable(fn):
        return False
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001 — completing onboarding must never 500 here
        log.warning("post-onboarding fresh restart failed; staying up", exc_info=True)
        return False


@router.post("/complete")
async def post_complete(request: Request) -> dict:
    st.mark_onboarding_complete(_path())
    restarting = _schedule_fresh_restart(request)
    return {"ok": True, "restarting": restarting}


__all__ = ["router"]
