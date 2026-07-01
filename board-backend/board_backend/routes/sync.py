"""``POST /api/v1/sync`` — signed stats push.

Auth via ``require_signed_request`` (pubkey + signature + replay window).
PII filter via Pydantic ``extra='forbid'`` on ``SyncPayload``.

Writes:
- ``identity.display_name`` + ``identity.bio`` are updated on every push
  (Plan §C-Decision-2: display_name per push).
- ``identity.last_sync_at`` is set.
- ``push_log`` gets an audit row with counts (no content).
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
    # Pydantic validates the body dict — extra keys such as "text" or
    # "transcript" → 422 with "Extra inputs are not permitted".
    try:
        body = SyncPayload.model_validate(auth.payload)
    except ValidationError as exc:
        # Deliberately 422 with a detailed Pydantic message — the client
        # can learn from it which field it should not have sent.
        # But never echo the body itself — only the paths.
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
            # Race: the identity was deleted between auth and here.
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
