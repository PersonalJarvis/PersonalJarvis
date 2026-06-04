"""Reactions-Routes (Phase D).

- ``POST /api/v1/reactions`` (signed by owner) — Owner reagiert auf einen
  Item, dessen Author ein Friend ist. Owner's Backend forwardet die Reaction
  zum Friend-Backend via ``POST /api/v1/federation/reactions/inbound``.

- ``POST /api/v1/federation/reactions/inbound`` (signed by friend) —
  Friend's Backend pusht eine Reaction auf einen Item, dessen Author das
  Owner-Backend ist. Wir verifizieren, dass der ``viewer_pubkey`` ein
  bekannter Friend ist, und persistieren.
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
# Owner side — eine Reaktion auf einen Friend-Item
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
            # Self-Reaction (selten, aber moeglich): direkt persistieren.
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

    # HTTP-Forward ausserhalb der Session. ``auth.body_bytes`` enthaelt den
    # bereits gelesenen raw-Body (require_signed_request konsumiert ihn).
    ok = await _forward_reaction(request, target_url, auth.body_bytes)
    if not ok:
        raise HTTPException(status_code=502, detail="friend backend unreachable")
    return ReactionAck(accepted=True)


# ----------------------------------------------------------------------
# Federation side — eine eingehende Reaktion vom Friend-Backend
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
        # Reactor muss ein friend sein.
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
    """Idempotent insert via UNIQUE-Constraint."""
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
        # bereits vorhanden, OK


async def _forward_reaction(request: Request, target_url: str, raw_body: bytes) -> bool:
    """Forwardet den signed Body 1:1 an Friend-Backend.

    Owner-Backend hat **keinen Privkey** — die Sig wurde im Frontend (oder
    im lokalen Jarvis-Client) generiert und im X-Jarvis-Sig-Header
    geliefert. Wir leiten den raw-Body + die Sig-Header weiter; Friend-
    Backend verifiziert mit dem (gleichen) Owner-Pubkey.

    Sicherer Pfad, weil der Friend-Empfaenger ``InboundReactionRequest``
    mit ``extra='forbid'`` validiert und die Sig gegen den Reactor-Pubkey
    aus dem ``X-Pubkey``-Header prueft — also gegen den Owner. Der
    Friend-Backend muss diesen Owner als ``Friend`` registriert haben,
    sonst 403.
    """
    headers_pass = {
        "Content-Type": "application/json",
        "X-Pubkey": request.headers.get("x-pubkey", ""),
        "X-Jarvis-Sig": request.headers.get("x-jarvis-sig", ""),
    }
    timeout = httpx.Timeout(5.0)
    try:
        # Tests koennen ueber app.state.federation_http einen MockTransport
        # injizieren — Production nutzt den Default.
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