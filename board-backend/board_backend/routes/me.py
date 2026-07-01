"""``GET /api/v1/me`` — own identity + sync statistics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from ..auth import SignedAuth, get_db, require_signed_request
from ..models import Identity, PushLog
from ..schemas import MeResponse

router = APIRouter(prefix="/api/v1", tags=["me"])


@router.get("/me", response_model=MeResponse)
def me(request: Request, auth: SignedAuth = Depends(require_signed_request)) -> MeResponse:
    with get_db(request) as session:
        ident = session.get(Identity, auth.identity.pubkey)
        assert ident is not None  # the auth dep already checked this
        push_count = session.scalar(
            select(func.count(PushLog.id)).where(PushLog.pubkey == ident.pubkey),
        ) or 0
        return MeResponse(
            pubkey=ident.pubkey,
            display_name=ident.display_name,
            created_at=ident.created_at,
            last_sync_at=ident.last_sync_at,
            push_count=push_count,
        )
