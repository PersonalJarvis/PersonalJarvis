"""Right-to-be-forgotten — DELETE /api/v1/federation/identity/{pubkey}.

GDPR-mässig: ein Friend signiert mit seinem Privkey einen DELETE-Request,
der seine eigene Identity (= seine Friendship-Spur + alle seine Reaktionen)
auf unserem Backend löscht. Activity-Items des Friends gibt es bei uns
gar nicht (er hostet die selbst), aber wir cleanen seine Reactions.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from ..auth import FederationAuth, get_db, get_owner_identity, require_federation_signed
from ..models import Friend, Reaction
from ..schemas import ForgetMeAck

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/federation", tags=["federation"])


@router.delete("/identity/{pubkey}", response_model=ForgetMeAck)
def forget_me(
    pubkey: str = Path(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    auth: FederationAuth = Depends(require_federation_signed),
    request: Request = None,
) -> ForgetMeAck:
    """Friend selbst-loescht alle seine Spuren bei uns.

    Plan §D: signed by friend. ``pubkey`` im Path muss == ``viewer_pubkey``
    aus der Sig sein, sonst 403.
    """
    if pubkey.lower() != auth.viewer_pubkey.lower():
        raise HTTPException(
            status_code=403,
            detail="path pubkey must match X-Pubkey",
        )
    with get_db(request) as session:
        owner = get_owner_identity(session)

        # Friendship loeschen
        friend_row = session.get(Friend, (owner.pubkey, pubkey))
        deleted_friendship = False
        if friend_row is not None:
            session.delete(friend_row)
            deleted_friendship = True

        # Reactions des Reactors loeschen
        reactions = session.query(Reaction).filter(
            Reaction.reactor_pubkey == pubkey
        ).all()
        deleted_reactions = len(reactions)
        for r in reactions:
            session.delete(r)

        # Activity-Items: wir hosten nur eigene Items, der friend hat hier
        # keine Items (sein author_pubkey kommt im ActivityItem-Schema nicht
        # als author vor, weil author == owner). Daher 0.
        deleted_activities = 0

        session.commit()

    return ForgetMeAck(
        deleted_friendship=deleted_friendship,
        deleted_activities=deleted_activities,
        deleted_reactions=deleted_reactions,
    )
