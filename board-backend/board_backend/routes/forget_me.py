"""Right-to-be-forgotten — DELETE /api/v1/federation/identity/{pubkey}.

GDPR-style: a friend signs a DELETE request with their privkey, which
deletes their own identity (= their friendship trace + all their
reactions) on our backend. We don't have the friend's activity items
at all (they host those themselves), but we do clean up their reactions.
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
    """The friend self-deletes all their traces at our end.

    Plan §D: signed by friend. ``pubkey`` in the path must == ``viewer_pubkey``
    from the signature, otherwise 403.
    """
    if pubkey.lower() != auth.viewer_pubkey.lower():
        raise HTTPException(
            status_code=403,
            detail="path pubkey must match X-Pubkey",
        )
    with get_db(request) as session:
        owner = get_owner_identity(session)

        # Delete the friendship
        friend_row = session.get(Friend, (owner.pubkey, pubkey))
        deleted_friendship = False
        if friend_row is not None:
            session.delete(friend_row)
            deleted_friendship = True

        # Delete the reactor's reactions
        reactions = session.query(Reaction).filter(
            Reaction.reactor_pubkey == pubkey
        ).all()
        deleted_reactions = len(reactions)
        for r in reactions:
            session.delete(r)

        # Activity items: we only host our own items, the friend has no
        # items here (their author_pubkey never shows up as author in the
        # ActivityItem schema, since author == owner). Hence 0.
        deleted_activities = 0

        session.commit()

    return ForgetMeAck(
        deleted_friendship=deleted_friendship,
        deleted_activities=deleted_activities,
        deleted_reactions=deleted_reactions,
    )
