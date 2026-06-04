"""``POST /api/v1/sync`` — signed Stats-Push.

Auth via ``require_signed_request`` (Pubkey + Sig + Replay-Window).
PII-Filter via Pydantic-``extra='forbid'`` im ``SyncPayload``.

Schreibt:
- ``identity.display_name`` + ``identity.bio`` werden bei jedem Push
  aktualisiert (Plan §C-Decision-2: display_name pro Push).
- ``identity.last_sync_at`` wird gesetzt.
- ``push_log`` bekommt eine Audit-Zeile mit Counts (kein Inhalt).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from ..auth import SignedAuth, get_db, require_signed_request
from ..models import Identity, PushLog
from ..schemas import SyncAck, SyncPayload

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["sync"])


@router.post("/sync", response_model=SyncAck)
def sync(
    request: Request,
    auth: SignedAuth = Depends(require_signed_request),
) -> SyncAck:
    # Pydantic validiert das Body-Dict — extra-keys wie "text" oder
    # "transcript" → 422 mit "Extra inputs are not permitted".
    try:
        body = SyncPayload.model_validate(auth.payload)
    except ValidationError as exc:
        # Bewusst 422 mit detaillierter Pydantic-Message — der Client kann
        # daraus lernen, welches Feld er nicht haette schicken sollen.
        # Aber niemals den Body selbst echoen — nur die Pfade.
        forbidden = [
            ".".join(str(x) for x in err["loc"])
            for err in exc.errors() if err.get("type") == "extra_forbidden"
        ]
        if forbidden:
            log.warning(
                "rejecting sync from %s... — disallowed fields: %s",
                auth.identity.pubkey[:8], forbidden,
            )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    received_at = datetime.now(timezone.utc)

    with get_db(request) as session:
        ident = session.get(Identity, auth.identity.pubkey)
        if ident is None:
            # Race: zwischen Auth und hier wurde die Identity geloescht.
            raise HTTPException(status_code=401, detail="identity revoked")
        ident.display_name = body.display_name
        ident.last_sync_at = received_at
        if body.bio is not None:
            ident.bio = body.bio

        session.add(PushLog(
            pubkey=ident.pubkey,
            received_at=received_at,
            daily_stats_count=len(body.daily_stats),
            achievements_count=len(body.achievements),
            payload_ts_ms=body.ts_ms,
        ))
        session.commit()

    return SyncAck(
        accepted=True,
        daily_stats_count=len(body.daily_stats),
        achievements_count=len(body.achievements),
        received_at=received_at,
    )
