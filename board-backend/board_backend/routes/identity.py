"""``POST /api/v1/identity/register`` — Admin-only Identitaets-Anlage."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import get_db, require_admin_token
from ..models import Identity
from ..schemas import RegisterRequest, RegisterResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/identity", tags=["identity"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    dependencies=[Depends(require_admin_token)],
)
def register(request: Request, payload: RegisterRequest) -> RegisterResponse:
    """Registriert einen neuen Pubkey beim Backend.

    Wenn der Pubkey schon existiert, aktualisieren wir lediglich den
    ``display_name`` — kein 409, weil das Re-Pairing bei Geraete-Wechsel
    eine sinnvolle Aktion ist und der Admin-Token ja vorhanden war.
    """
    with get_db(request) as session:
        ident = session.get(Identity, payload.pubkey)
        if ident is None:
            ident = Identity(
                pubkey=payload.pubkey,
                display_name=payload.display_name,
            )
            session.add(ident)
        else:
            ident.display_name = payload.display_name
        session.commit()
        session.refresh(ident)
        return RegisterResponse(
            pubkey=ident.pubkey,
            display_name=ident.display_name,
            created_at=ident.created_at,
        )
