"""Activity routes (Phase D).

- ``POST /api/v1/activities`` (signed by owner) — the owner creates a new
  activity item (achievement, story, milestone) with a visibility.
- ``GET  /api/v1/activities`` (signed by owner) — the owner sees their own
  items including reaction counts.
- ``GET  /api/v1/federation/feed`` (signed by viewer) — a friend OR an
  anonymous caller pulls the owner's board. Visibility filter in SQL.

Reaction visibility (Plan §D §0):
- The owner sees ``reaction_counts: {rocket: 3, brain: 1, fire: 0}``.
- Others see ``reaction_counts: null, has_reactions: true|false``.
"""
from __future__ import annotations

import json
import logging
import math
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..auth import (
    SignedAuth,
    get_db,
    get_owner_identity,
    require_federation_signed,
    require_signed_request,
)
from ..models import ActivityItem, Friend, Identity, Reaction
from ..schemas import (
    ActivityCreateRequest,
    ActivityItemDTO,
    FeedResponse,
    StoryCreateRequest,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["activity"])
fed_router = APIRouter(prefix="/api/v1/federation", tags=["federation"])

STORY_DEFAULT_HOURS = 24


# ----------------------------------------------------------------------
# Owner side: create + list
# ----------------------------------------------------------------------

@router.post("/activities", response_model=ActivityItemDTO)
def create_activity(
    request: Request,
    auth: SignedAuth = Depends(require_signed_request),
) -> ActivityItemDTO:
    try:
        body = ActivityCreateRequest.model_validate(auth.payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    expires_at: datetime | None = None
    if body.kind == "story":
        hours = body.expires_in_hours or STORY_DEFAULT_HOURS
        expires_at = now + timedelta(hours=hours)
    elif body.expires_in_hours is not None:
        expires_at = now + timedelta(hours=body.expires_in_hours)

    with get_db(request) as session:
        owner = get_owner_identity(session)
        if auth.identity.pubkey != owner.pubkey:
            raise HTTPException(status_code=403, detail="not the owner of this backend")
        item = ActivityItem(
            id=_new_id(),
            author_pubkey=owner.pubkey,
            kind=body.kind,
            payload=json.dumps(body.payload, sort_keys=True),
            created_at=now,
            visibility=body.visibility,
            expires_at=expires_at,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return _item_to_dto(item, owner_display=owner.display_name, viewer_pubkey=owner.pubkey,
                            counts={}, session=session)


@router.post("/stories", response_model=ActivityItemDTO)
def create_story(
    request: Request,
    auth: SignedAuth = Depends(require_signed_request),
) -> ActivityItemDTO:
    """Plan §D spec: a separate route for stories (24 h lifetime)."""
    try:
        body = StoryCreateRequest.model_validate(auth.payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Map onto ActivityCreateRequest — same logic, same path.
    activity_payload = {
        "ts_ms": body.ts_ms,
        "kind": "story",
        "payload": {"text": body.text},
        "visibility": body.visibility,
        "expires_in_hours": 24,
    }
    # Reuse the create_activity logic via a direct call with a patched auth payload.
    auth.payload = activity_payload
    return create_activity(request, auth)


@router.get("/activities", response_model=FeedResponse)
def list_own_activities(
    request: Request,
    sort: str = Query("latest", pattern=r"^(interesting|latest)$"),
    auth: SignedAuth = Depends(require_signed_request),
) -> FeedResponse:
    with get_db(request) as session:
        owner = get_owner_identity(session)
        items = session.execute(
            select(ActivityItem).where(ActivityItem.author_pubkey == owner.pubkey)
        ).scalars().all()
        items = _filter_expired(items)
        dtos = [
            _item_to_dto(it, owner_display=owner.display_name,
                         viewer_pubkey=owner.pubkey,
                         counts=_count_reactions(session, it.id),
                         session=session)
            for it in items
        ]
        dtos = _sort_items(dtos, sort)
        return FeedResponse(items=dtos, sort=sort, server_now=datetime.now(timezone.utc))


# ----------------------------------------------------------------------
# Federation side: GET feed
# ----------------------------------------------------------------------

@fed_router.get("/feed", response_model=FeedResponse)
def federation_feed(
    request: Request,
    sort: str = Query("interesting", pattern=r"^(interesting|latest)$"),
    since: str | None = Query(None, description="ISO-8601 timestamp (UTC). "
                              "Items with created_at < since are filtered out."),
    auth=Depends(require_federation_signed),
) -> FeedResponse:
    """Returns the owner's activity items, filtered by visibility.

    - ``visibility=public`` -> always visible
    - ``visibility=friends`` -> only if ``viewer`` is in the ``friends`` table
    - ``visibility=private`` -> only if ``viewer == owner``

    ``since`` (Plan §D spec): incremental pull. Friends store the
    ``server_now`` of their last pull and pass it in as ``since`` on the
    next one, so the backend only has to serialize the diffs.
    """
    since_dt = None
    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid since: {exc}") from exc

    with get_db(request) as session:
        owner = get_owner_identity(session)
        is_owner = (auth.viewer_pubkey == owner.pubkey)
        is_friend = is_owner or _is_friend(session, owner.pubkey, auth.viewer_pubkey)

        # SQL filter — Plan §D ALGORITHM TRANSPARENT BY DESIGN
        clauses = [ActivityItem.visibility == "public"]
        if is_friend:
            clauses.append(ActivityItem.visibility == "friends")
        if is_owner:
            clauses.append(ActivityItem.visibility == "private")

        query = (
            select(ActivityItem)
            .where(ActivityItem.author_pubkey == owner.pubkey)
            .where(or_(*clauses))
        )
        if since_dt is not None:
            query = query.where(ActivityItem.created_at >= since_dt)

        rows = session.execute(query).scalars().all()
        rows = _filter_expired(rows)

        dtos = [
            _item_to_dto(
                it,
                owner_display=owner.display_name,
                viewer_pubkey=auth.viewer_pubkey,
                counts=_count_reactions(session, it.id),
                session=session,
            )
            for it in rows
        ]
        dtos = _sort_items(dtos, sort)
        return FeedResponse(items=dtos, sort=sort, server_now=datetime.now(timezone.utc))


# ----------------------------------------------------------------------
# Sort
# ----------------------------------------------------------------------

def interesting_score(reactions_total: int, age_hours: float) -> float:
    """ALGORITHM TRANSPARENT BY DESIGN.

    interesting = reactions * exp(-age_hours / 24)

    Deterministic, parameterless, computed in a single line. The
    half-life window (24 h) is hardcoded, so there's no tunable that
    could be secretly optimized via A/B testing (Plan §0).
    """
    return reactions_total * math.exp(-age_hours / 24.0)


def _sort_items(items: list[ActivityItemDTO], sort: str) -> list[ActivityItemDTO]:
    if sort == "latest":
        return sorted(items, key=lambda i: i.created_at, reverse=True)
    # interesting
    now = datetime.now(timezone.utc)

    def _score(i: ActivityItemDTO) -> tuple[float, datetime]:
        age_h = max(0.0, (now - i.created_at).total_seconds() / 3600.0)
        total = sum((i.reaction_counts or {}).values())
        # Tie-break: created_at, so the order is deterministic when reactions=0.
        return (-interesting_score(total, age_h), -i.created_at.timestamp())

    return sorted(items, key=_score)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _new_id() -> str:
    return secrets.token_hex(16)


def _filter_expired(items: list[ActivityItem]) -> list[ActivityItem]:
    now = datetime.now(timezone.utc)
    keep: list[ActivityItem] = []
    for it in items:
        if it.expires_at is None:
            keep.append(it)
            continue
        ea = it.expires_at if it.expires_at.tzinfo else it.expires_at.replace(tzinfo=timezone.utc)
        if ea > now:
            keep.append(it)
    return keep


def _is_friend(session: Session, owner_pubkey: str, viewer_pubkey: str) -> bool:
    return session.get(Friend, (owner_pubkey, viewer_pubkey)) is not None


def _count_reactions(session: Session, item_id: str) -> dict[str, int]:
    rows = session.execute(
        select(Reaction.reaction).where(Reaction.item_id == item_id)
    ).scalars().all()
    counts = {"rocket": 0, "brain": 0, "fire": 0}
    for r in rows:
        if r in counts:
            counts[r] += 1
    return counts


def _item_to_dto(
    item: ActivityItem,
    *,
    owner_display: str,
    viewer_pubkey: str,
    counts: dict[str, int],
    session: Session,
) -> ActivityItemDTO:
    is_owner = viewer_pubkey == item.author_pubkey
    payload: dict[str, Any]
    try:
        payload = json.loads(item.payload or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except json.JSONDecodeError:
        payload = {}
    return ActivityItemDTO(
        id=item.id,
        author_pubkey=item.author_pubkey,
        author_display_name=owner_display,
        kind=item.kind,
        payload=payload,
        created_at=item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=timezone.utc),
        visibility=item.visibility,
        expires_at=item.expires_at,
        reaction_counts=counts if is_owner else None,
        has_reactions=any(v > 0 for v in counts.values()),
    )