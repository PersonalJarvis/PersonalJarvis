"""REST API for in-app marketplace publishing.

Endpoints:
    GET    /api/marketplace/publish/identity          — signed-in state (+enabled)
    POST   /api/marketplace/publish/signin/start      — begin GitHub device flow
    GET    /api/marketplace/publish/signin/poll/{id}  — poll until approved
    DELETE /api/marketplace/publish/identity          — sign out (drop token)
    POST   /api/marketplace/publish/validate          — field-level pre-check
    POST   /api/marketplace/publish/submit            — publish (opens the bot PR)
    GET    /api/marketplace/publish/status            — is name@version live in the feed

The heavy lifting lives in ``jarvis/marketplace/publish.py``; this layer only
shapes it for the view and the auto-generated CLI.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from jarvis.marketplace.token_store import TokenStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketplace/publish", tags=["marketplace"])

# In-flight device flows: flow_id -> completion task. Entries are popped on
# the poll that observes completion; abandoned flows die with their 15-minute
# GitHub expiry, so this stays a handful of entries at worst.
_flows: dict[str, asyncio.Task[dict[str, Any]]] = {}


async def _complete_signin(handler: Any, session: Any) -> dict[str, Any]:
    from jarvis.marketplace.publish import PUBLISHER_TOKEN_ID, fetch_identity

    result = await handler.await_completion(session)
    if result.tokens is None:
        return {"status": "error", "error": result.error or "sign-in failed"}
    store = TokenStore()
    store.save(PUBLISHER_TOKEN_ID, result.tokens)
    try:
        identity = await fetch_identity(result.tokens)
    except RuntimeError as exc:
        # The token is saved; only the name lookup failed. The view falls
        # back to GET /identity, which retries.
        log.warning("publish sign-in: identity lookup failed: %s", exc)
        identity = None
    return {"status": "connected", **(identity or {})}


@router.get("/identity", openapi_extra={"x-jarvis-readonly": True})
async def publish_identity(response: Response) -> dict[str, Any]:
    """Signed-in state, plus whether publishing is enabled at all."""
    from jarvis.marketplace.publish import current_identity, publish_endpoint

    response.headers["Cache-Control"] = "no-store"
    enabled = bool(publish_endpoint())
    if not enabled:
        return {"enabled": False, "signed_in": False}
    try:
        state = await current_identity()
    except RuntimeError as exc:
        # GitHub unreachable: report the stored token as "unknown", not as
        # signed out — signing the user out over a network blip would drop a
        # perfectly good token.
        return {"enabled": True, "signed_in": False, "unreachable": str(exc)}
    return {"enabled": True, **state}


@router.post("/signin/start")
async def signin_start() -> dict[str, Any]:
    """Begin the device flow: returns the user code to type at GitHub."""
    from jarvis.marketplace.publish import make_device_handler, publish_endpoint

    if not publish_endpoint():
        raise HTTPException(status_code=503, detail="publishing is disabled in this deployment")
    handler = make_device_handler()
    try:
        session = await handler.start(None)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _flows[session.flow_id] = asyncio.create_task(
        _complete_signin(handler, session), name=f"publish-signin:{session.flow_id}"
    )
    return {
        "flow_id": session.flow_id,
        "user_code": session.user_code,
        "verification_uri": session.verification_uri,
        "verification_uri_complete": session.verification_uri_complete,
        "expires_at_ms": session.expires_at_ms,
        "interval": session.interval,
    }


@router.get("/signin/poll/{flow_id}", openapi_extra={"x-jarvis-readonly": True})
async def signin_poll(flow_id: str, response: Response) -> dict[str, Any]:
    """Poll an in-progress sign-in until GitHub reports approval."""
    response.headers["Cache-Control"] = "no-store"
    task = _flows.get(flow_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown or already-finished flow")
    if not task.done():
        return {"status": "pending"}
    _flows.pop(flow_id, None)
    try:
        return task.result()
    except Exception as exc:  # noqa: BLE001 - surface, never crash the poll
        return {"status": "error", "error": str(exc)}


@router.delete("/identity")
async def sign_out() -> dict[str, Any]:
    """Sign out: drop the stored publisher token."""
    from jarvis.marketplace.publish import PUBLISHER_TOKEN_ID

    TokenStore().delete(PUBLISHER_TOKEN_ID)
    return {"ok": True}


class SubmissionDraft(BaseModel):
    """The submission as the form holds it — validation happens server-side
    in ``validate_draft`` so the wire shape stays loose on purpose."""

    kind: str
    name: str = ""
    version: str = ""
    title: str | None = None
    description: str | None = None
    categories: list[str] | None = None
    skill_md: str | None = None
    plugin_json: dict[str, Any] | None = None
    mcp_json: dict[str, Any] | None = None
    usage_card: str | None = None


@router.post("/validate", openapi_extra={"x-jarvis-readonly": True})
async def validate(body: SubmissionDraft) -> dict[str, Any]:
    """Field-level pre-check with the endpoint's own rule set (advisory —
    the endpoint and the registry CI remain the authority)."""
    from jarvis.marketplace.publish import validate_draft

    _, errors = validate_draft(body.model_dump())
    return {"ok": not errors, "errors": errors}


# Dangerous: publishes content publicly under the user's GitHub name. The
# flag makes the auto-generated `jarvis api` layer demand --yes, mirroring
# the view's explicit Publish click.
@router.post("/submit", openapi_extra={"x-jarvis-dangerous": True})
async def submit(body: SubmissionDraft) -> dict[str, Any]:
    """Validate, then POST to the storefront endpoint as the signed-in user."""
    from jarvis.marketplace.publish import SubmitError, validate_draft
    from jarvis.marketplace.publish import submit as do_submit

    normalized, errors = validate_draft(body.model_dump())
    if normalized is None:
        first = errors[0]
        raise HTTPException(
            status_code=422,
            detail={"error": first["error"], "field": first["field"], "errors": errors},
        )
    try:
        result = await do_submit(normalized)
    except SubmitError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"error": exc.error, "field": exc.field}
        ) from exc
    return {"ok": True, "name": normalized["name"], "version": normalized["version"], **result}


@router.get("/status", openapi_extra={"x-jarvis-readonly": True})
async def status(
    name: str, version: str, response: Response, force: bool = False
) -> dict[str, Any]:
    """Whether name@version has reached the live community feed yet."""
    from jarvis.marketplace.publish import live_status

    response.headers["Cache-Control"] = "no-store"
    return await live_status(name, version, force=force)
