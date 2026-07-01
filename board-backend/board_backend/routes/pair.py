"""Pair routes (Phase D).

Plan §D-Decision-1: URL-token-based pairing with 10 min validity.

Flow:
1. The owner calls ``POST /api/v1/pair/initiate`` (admin token) → gets
   ``{token, url, expires_at}``. The owner sends the URL to the friend
   via a private channel.
2. The friend visits the URL → the friend's backend calls the owner's
   ``POST /api/v1/pair/accept`` with ``{token, friend_pubkey, friend_url,
   friend_display_name}``. The owner registers the friendship and
   responds with its own ``{owner_pubkey, owner_url, owner_display_name}``.
   The friend's backend in turn registers it (in its ``friends`` table).
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import (
    get_db,
    get_owner_identity,
    require_admin_token,
    require_signed_request,
)
from ..models import Friend, Identity, PairToken
from ..schemas import (
    FriendItem,
    FriendsListResponse,
    FriendUpdateRequest,
    PairAcceptRequest,
    PairAcceptResponse,
    PairInitiateRequest,
    PairInitiateResponse,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pair", tags=["pair"])

PAIR_TTL = timedelta(minutes=10)
PAIR_TOKEN_BYTES = 24       # 24 bytes -> 48 hex chars (>= 16 min length)


@router.post(
    "/initiate",
    response_model=PairInitiateResponse,
    dependencies=[Depends(require_admin_token)],
)
def initiate(request: Request, payload: PairInitiateRequest) -> PairInitiateResponse:
    public_url = _public_url_for_owner(request)
    with get_db(request) as session:
        owner = get_owner_identity(session)
        token = secrets.token_hex(PAIR_TOKEN_BYTES)
        now = datetime.now(timezone.utc)
        expires_at = now + PAIR_TTL
        session.add(PairToken(
            token=token,
            owner_pubkey=owner.pubkey,
            created_at=now,
            expires_at=expires_at,
        ))
        session.commit()
    pair_url = f"{public_url.rstrip('/')}/api/v1/pair/redeem?token={token}"
    return PairInitiateResponse(
        token=token,
        url=pair_url,
        expires_at=expires_at,
        owner_pubkey=owner.pubkey,
    )


@router.post("/accept", response_model=PairAcceptResponse)
def accept(request: Request, payload: PairAcceptRequest) -> PairAcceptResponse:
    """The friend's backend calls this with the token.

    NOT signature-authenticated — the friend is still unknown here.
    The token IS the auth (single-use, 10 min).
    """
    now = datetime.now(timezone.utc)
    with get_db(request) as session:
        tok = session.get(PairToken, payload.token)
        if tok is None:
            raise HTTPException(status_code=401, detail="unknown token")
        if tok.used_at is not None:
            raise HTTPException(status_code=401, detail="token already used")
        token_expires = _aware(tok.expires_at)
        if token_expires < now:
            raise HTTPException(status_code=401, detail="token expired")

        owner = get_owner_identity(session)
        if tok.owner_pubkey != owner.pubkey:
            # Defensive: the owner changed between initiate and accept
            raise HTTPException(status_code=503, detail="owner mismatch")

        if payload.friend_pubkey == owner.pubkey:
            raise HTTPException(status_code=400, detail="cannot pair with self")

        # Consume the token
        tok.used_at = now

        existing = session.get(Friend, (owner.pubkey, payload.friend_pubkey))
        if existing is None:
            session.add(Friend(
                owner_pubkey=owner.pubkey,
                friend_pubkey=payload.friend_pubkey,
                friend_url=payload.friend_url,
                friend_display_name=payload.friend_display_name,
                paired_at=now,
            ))
        else:
            existing.friend_url = payload.friend_url
            existing.friend_display_name = payload.friend_display_name
            existing.paired_at = now

        session.commit()

    public_url = _public_url_for_owner(request)
    return PairAcceptResponse(
        accepted=True,
        owner_pubkey=owner.pubkey,
        owner_url=public_url,
        owner_display_name=owner.display_name,
        paired_at=now,
    )


# ----------------------------------------------------------------------
# Friends-List (signed by owner)
# ----------------------------------------------------------------------

friends_router = APIRouter(prefix="/api/v1", tags=["friends"])


@friends_router.get("/friends", response_model=FriendsListResponse)
def list_friends(request: Request, _=Depends(require_signed_request)) -> FriendsListResponse:
    """The owner lists their friends. Signed by the owner's pubkey."""
    with get_db(request) as session:
        owner = get_owner_identity(session)
        rows = session.query(Friend).filter(Friend.owner_pubkey == owner.pubkey).all()
        return FriendsListResponse(friends=[
            FriendItem(
                pubkey=r.friend_pubkey,
                url=r.friend_url,
                display_name=r.friend_display_name,
                paired_at=r.paired_at,
                last_pull_at=r.last_pull_at,
                pull_interval_s=r.pull_interval_s,
            )
            for r in rows
        ])


@friends_router.patch("/friends/{friend_pubkey}", response_model=FriendItem)
def update_friend(
    request: Request,
    friend_pubkey: str,
    auth=Depends(require_signed_request),
) -> FriendItem:
    """Updates the per-friend sync interval (Plan §D spec).

    Body: ``{ts_ms, pull_interval_s}`` signed by owner. Other friend
    fields (URL, paired_at) are not changeable via this route — those
    come through a new pair roundtrip.
    """
    try:
        body = FriendUpdateRequest.model_validate(auth.payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with get_db(request) as session:
        owner = get_owner_identity(session)
        if auth.identity.pubkey != owner.pubkey:
            raise HTTPException(status_code=403, detail="not the owner")
        row = session.get(Friend, (owner.pubkey, friend_pubkey))
        if row is None:
            raise HTTPException(status_code=404, detail="friend not found")
        row.pull_interval_s = body.pull_interval_s
        session.commit()
        session.refresh(row)
        return FriendItem(
            pubkey=row.friend_pubkey,
            url=row.friend_url,
            display_name=row.friend_display_name,
            paired_at=row.paired_at,
            last_pull_at=row.last_pull_at,
            pull_interval_s=row.pull_interval_s,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    """SQLite returns datetimes without a tz — we add UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _public_url_for_owner(request: Request) -> str:
    """Best effort: use the ``Host`` header and scheme.

    In production, Caddy typically sits in front and sets the
    ``Forwarded`` header. Phase D doesn't strictly assume that though;
    a local localhost test works with ``http://<host>``.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    scheme = forwarded_proto or request.url.scheme
    host = forwarded_host or request.headers.get("host") or "localhost:8765"
    return f"{scheme}://{host}"