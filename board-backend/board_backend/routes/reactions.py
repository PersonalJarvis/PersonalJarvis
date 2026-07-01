"""Reactions routes (Phase D).

- ``POST /api/v1/reactions`` (signed by owner) — the owner reacts to an
  item whose author is a friend. The owner's backend forwards the reaction
  to the friend's backend via ``POST /api/v1/federation/reactions/inbound``.

- ``POST /api/v1/federation/reactions/inbound`` (signed by friend) —
  the friend's backend pushes a reaction to an item whose author is the
  owner's backend. We verify that ``viewer_pubkey`` is a known friend, then
  persist.
"""
from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import (
    SignedAuth,
    FederationAuth,
    get_db,
    get_owner_identity,
    require_federation_signed,
    require_signed_request,
)
from ..crypto import canonical_json, sign
from ..models import ActivityItem, Friend, Reaction
from ..schemas import (
    InboundReactionRequest,
    ReactionAck,
    ReactionRequest,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["reactions"])
fed_router = APIRouter(prefix="/api/v1/federation", tags=["federation"])


# ----------------------------------------------------------------------
# Owner side — a reaction to a friend's item
# ----------------------------------------------------------------------

@router.post("/reactions", response_model=ReactionAck)
async def post_reaction(
    request: Request,
    auth: SignedAuth = Depends(require_signed_request),
) -> ReactionAck:
    try:
        body = ReactionRequest.model_validate(auth.payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    target_url: str | None = None
    with get_db(request) as session:
        owner = get_owner_identity(session)
        if auth.identity.pubkey != owner.pubkey:
            raise HTTPException(status_code=403, detail="not the owner")

        if body.author_pubkey == owner.pubkey:
            # Self-reaction (rare, but possible): persist directly.
            local_item = session.get(ActivityItem, body.item_id)
            if local_item is None:
                raise HTTPException(status_code=404, detail="item not found")
            _persist_reaction(session, body.item_id, owner.pubkey, body.reaction)
            session.commit()
            return ReactionAck(accepted=True)

        friend = session.get(Friend, (owner.pubkey, body.author_pubkey))
        if friend is None:
            raise HTTPException(status_code=404, detail="not a friend")
        target_url = friend.friend_url

    # HTTP forward outside the session. ``auth.body_bytes`` holds the
    # raw body already read (require_signed_request consumes it).
    ok = await _forward_reaction(request, target_url, auth.body_bytes)
    if not ok:
        raise HTTPException(status_code=502, detail="friend backend unreachable")
    return ReactionAck(accepted=True)


# ----------------------------------------------------------------------
# Federation side — an incoming reaction from the friend's backend
# ----------------------------------------------------------------------

@fed_router.post("/reactions/inbound", response_model=ReactionAck)
def reactions_inbound(
    request: Request,
    auth: FederationAuth = Depends(require_federation_signed),
) -> ReactionAck:
    try:
        body = InboundReactionRequest.model_validate(auth.payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with get_db(request) as session:
        owner = get_owner_identity(session)
        # Reactor must be a friend.
        friend = session.get(Friend, (owner.pubkey, auth.viewer_pubkey))
        if friend is None:
            raise HTTPException(status_code=403, detail="not a friend")
        item = session.get(ActivityItem, body.item_id)
        if item is None or item.author_pubkey != owner.pubkey:
            raise HTTPException(status_code=404, detail="item not found")
        _persist_reaction(session, body.item_id, auth.viewer_pubkey, body.reaction)
        session.commit()
    return ReactionAck(accepted=True)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _persist_reaction(session, item_id: str, reactor_pubkey: str, reaction: str) -> None:
    """Idempotent insert via UNIQUE constraint."""
    from sqlalchemy.exc import IntegrityError
    session.add(Reaction(
        item_id=item_id,
        reactor_pubkey=reactor_pubkey,
        reaction=reaction,
    ))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        # already present, OK


async def _forward_reaction(request: Request, target_url: str, raw_body: bytes) -> bool:
    """Forwards the signed body 1:1 to the friend's backend.

    The owner's backend has **no private key** — the signature was
    generated in the frontend (or the local Jarvis client) and delivered
    in the X-Jarvis-Sig header. We pass the raw body + the signature
    header through as-is; the friend's backend verifies against the
    (same) owner pubkey.

    This is a safe path because the friend receiver validates
    ``InboundReactionRequest`` with ``extra='forbid'`` and checks the
    signature against the reactor pubkey from the ``X-Pubkey`` header —
    i.e. against the owner. The friend's backend must have this owner
    registered as a ``Friend``, otherwise 403.
    """
    headers_pass = {
        "Content-Type": "application/json",
        "X-Pubkey": request.headers.get("x-pubkey", ""),
        "X-Jarvis-Sig": request.headers.get("x-jarvis-sig", ""),
    }
    timeout = httpx.Timeout(5.0)
    try:
        # Tests can inject a MockTransport via app.state.federation_http —
        # production uses the default.
        forwarder = getattr(request.app.state, "federation_http", None)
        if forwarder is None:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{target_url.rstrip('/')}/api/v1/federation/reactions/inbound",
                    content=raw_body, headers=headers_pass,
                )
        else:
            resp = await forwarder.post(
                f"{target_url.rstrip('/')}/api/v1/federation/reactions/inbound",
                content=raw_body, headers=headers_pass,
            )
        return resp.status_code == 200
    except httpx.HTTPError:
        log.exception("reaction forward failed for %s", target_url)
        return False