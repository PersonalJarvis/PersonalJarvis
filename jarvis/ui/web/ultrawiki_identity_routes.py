"""UltraWiki identity REST surface — people, merge proposals, merge audit.

The identity layer (``jarvis.ultrawiki.identity`` + ``identity_store``) decides
which mentions across a corpus are the SAME person, place or project. It only
ever merges on deterministic evidence (a shared e-mail, phone or contact slug);
everything weaker becomes a proposal a human decides, and every merge stays
reversible. These routes are what makes that decidable from outside the
database: the People list, one person's profile, the confirmation queue, the
two decisions, the undo, and the audit trail behind all of it.

Own module rather than more lines in ``ultrawiki_routes.py`` (already past
2200 lines and edited concurrently in this shared tree) — same prefix and same
``ultrawiki`` tag, so the CLI-first contract still resolves every handler to
``jarvis api ultrawiki <op>``. The mode gate and the store accessor are reused
from the main module: one definition of "is UltraWiki answering right now".

Everything is a thin shell over :class:`jarvis.ultrawiki.service.UltraWikiService`
— no matching, ranking or merge logic lives here. Heavy imports stay inside the
handlers (AP-26); nothing on this surface is on the voice hot path (AP-9) and
no handler calls a model.

Refusals: the store raises ``IdentityError`` when an OPERATION is impossible
(unknown queue id, a merge already undone, an out-of-order unmerge). Those
become 409 with the store's own sentence rather than a route-side 404/409
split — re-deriving that classification here would be a second opinion on a
question the store already answers, which is exactly the drift class CLAUDE.md
§5 forbids.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from jarvis.ui.web.ultrawiki_routes import _require_active, _store_of

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ultrawiki", tags=["ultrawiki"])

#: ``status=all`` on the queue means "every decision, not just the open ones".
_QUEUE_ANY = "all"

__all__ = ["router"]


def _queue_status(raw: str) -> str | None:
    """Validate a queue status filter; ``None`` means "every status".

    Rejects an unknown value instead of silently listing everything: a typo in
    a CLI filter that returns MORE rows than asked for is worse than an error.
    """
    from jarvis.ultrawiki.identity import QueueStatus  # noqa: PLC0415 — lazy (AP-26)

    wanted = str(raw or "").strip().lower()
    if wanted in ("", _QUEUE_ANY):
        return None
    allowed = {str(member) for member in QueueStatus}
    if wanted not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown status {wanted!r} (one of: "
                f"{', '.join(sorted(allowed))}, {_QUEUE_ANY})"
            ),
        )
    return wanted


def _refused(exc: Exception) -> HTTPException:
    """Turn an identity refusal into a 409 that repeats the honest reason."""
    return HTTPException(status_code=409, detail=str(exc))


async def _identity_counts(service: Any) -> dict[str, int]:
    """Honest counters for every list answer (index-only, no scan).

    They travel with EVERY list, not just the empty ones: an empty People list
    with ``entities: 0`` is "nothing was ever seeded", the same list with
    ``entities: 40`` is "your filter matched nobody" — two different problems
    that look identical without the counters.
    """
    try:
        store = await _store_of(service)
        return await store.identity_counts()
    except Exception:  # noqa: BLE001 — counters must never break a list answer
        log.debug("identity counts unavailable", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@router.get(
    "/identity/people",
    summary="List the people the knowledge base has identified",
)
async def list_people(
    request: Request,
    q: str = Query(
        default="", description="Filter by display name or any identifier value"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Live people, name-ordered, with how many identifiers each one carries.

    "Live" excludes entities that were merged away: after a merge the loser is
    a tombstone pointing at the survivor, and listing both would report two
    people where the user decided there is one. Non-person entities (places,
    orgs, projects, topics) are the Explore surface's job, not this one's.
    """
    service = _require_active(request)
    people = await service.list_people(query=q.strip(), limit=limit, offset=offset)
    return {
        "ok": True,
        "people": people,
        "query": q.strip(),
        "limit": limit,
        "offset": offset,
        "counts": await _identity_counts(service),
    }


@router.get(
    "/identity/people/{entity_id}",
    summary="One person's identifiers, merge history and open proposals",
)
async def get_person(entity_id: int, request: Request) -> dict[str, Any]:
    """Everything the identity layer holds about one entity.

    A merged-away id is FORWARDED to the survivor rather than 404'd (the
    answer reports the id that was asked for in ``requested_id``), so a link
    saved before a merge keeps working instead of dead-ending.
    """
    service = _require_active(request)
    profile = await service.person_profile(entity_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown entity {entity_id}")
    return {
        "ok": True,
        "person": profile,
        # True when the requested id was a tombstone and this is the survivor.
        "forwarded": int(profile.get("id", entity_id)) != int(entity_id),
    }


@router.post(
    "/identity/seed",
    summary="Seed people from the address book (idempotent, re-runnable)",
    openapi_extra={
        # Writes entities and can fuse existing ones on deterministic evidence.
        # Idempotent and every merge is reversible, but it is a bulk write over
        # the user's own address book, so it asks first.
        "x-jarvis-dangerous": True
    },
)
async def seed_identities(request: Request) -> dict[str, Any]:
    """Import the user's contacts as identity entities; report what changed.

    Safe to run again at any time: a contact resolves through its own contact
    slug, so a second pass links instead of duplicating (``created: 0``). A
    contact whose e-mail or phone already appeared in the corpus merges with
    that entity deterministically instead of creating a twin.
    """
    service = _require_active(request)
    report = await service.seed_identities()
    return {"ok": True, "report": report, "counts": await _identity_counts(service)}


# ---------------------------------------------------------------------------
# The confirmation queue — everything the layer refused to decide alone
# ---------------------------------------------------------------------------


@router.get(
    "/identity/queue",
    summary="Merge proposals waiting for a human decision",
)
async def list_identity_queue(
    request: Request,
    status: str = Query(
        default="pending",
        description="pending | confirmed | rejected | all",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Pairs the layer thinks MIGHT be the same, strongest evidence first.

    Nothing here was merged: a proposal is what the layer produces when the
    evidence is a name similarity rather than a shared e-mail, phone or
    contact. Rows whose two sides have meanwhile become one entity are skipped
    — the queue self-heals instead of asking about a settled question.
    """
    service = _require_active(request)
    wanted = _queue_status(status)
    proposals = await service.identity_queue(status=wanted, limit=limit)
    return {
        "ok": True,
        "proposals": proposals,
        "status": wanted or _QUEUE_ANY,
        "limit": limit,
        "counts": await _identity_counts(service),
    }


@router.post(
    "/identity/queue/{queue_id}/confirm",
    summary="Confirm one merge proposal — the two become one person",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def confirm_identity_merge(queue_id: int, request: Request) -> dict[str, Any]:
    """Apply a proposal; returns the audit id that reverses it.

    The answer's ``merge_id`` is the exact argument for the unmerge route, so
    "confirmed the wrong pair" is one call away from undone. ``merge_id: 0``
    means the pair had already become one entity by other evidence — the
    proposal is closed, and there is nothing to reverse.
    """
    service = _require_active(request)
    from jarvis.ultrawiki.identity import IdentityError  # noqa: PLC0415 — lazy (AP-26)

    try:
        result = await service.confirm_identity_merge(queue_id)
    except IdentityError as exc:
        raise _refused(exc) from exc
    return {"ok": True, **result}


@router.post(
    "/identity/queue/{queue_id}/reject",
    summary="Reject one merge proposal — they stay two different people",
    openapi_extra={
        # Permanent by design: a rejected pair is never proposed again, and the
        # rejection outranks even later deterministic evidence. There is no
        # un-reject, which makes this the least reversible identity action.
        "x-jarvis-dangerous": True
    },
)
async def reject_identity_merge(queue_id: int, request: Request) -> dict[str, Any]:
    """Decline a proposal permanently — the pair is never proposed again."""
    service = _require_active(request)
    from jarvis.ultrawiki.identity import IdentityError  # noqa: PLC0415 — lazy (AP-26)

    try:
        result = await service.reject_identity_merge(queue_id)
    except IdentityError as exc:
        raise _refused(exc) from exc
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# The merge audit trail + the undo it exists for
# ---------------------------------------------------------------------------


@router.get(
    "/identity/merges",
    summary="The merge audit trail — what was fused, on what evidence",
)
async def list_identity_merges(
    request: Request,
    entity_id: int | None = Query(
        default=None, description="Only merges involving this entity"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Every merge, newest first, with its tier, evidence and undo state.

    The log outlives the entities it names (no foreign key by design), so a
    purge cannot quietly erase the record of what was once fused.
    """
    service = _require_active(request)
    merges = await service.identity_merge_log(entity_id=entity_id, limit=limit)
    return {
        "ok": True,
        "merges": merges,
        "entity_id": entity_id,
        "limit": limit,
        "counts": await _identity_counts(service),
    }


@router.post(
    "/identity/merges/{merge_id}/unmerge",
    summary="Undo one merge — both identities come back exactly as they were",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def unmerge_identity(merge_id: int, request: Request) -> dict[str, Any]:
    """Reverse one merge and make the split stick.

    Identifiers go back where they came from, dropped duplicates are
    re-created, and the pair is recorded as rejected — so the same evidence
    cannot silently fuse them again on the next import. Merges undo in reverse
    order: a merge shadowed by a later one is refused (409) naming the merge to
    undo first, rather than half-restoring a chain.
    """
    service = _require_active(request)
    from jarvis.ultrawiki.identity import IdentityError  # noqa: PLC0415 — lazy (AP-26)

    try:
        result = await service.unmerge_identity(merge_id)
    except IdentityError as exc:
        raise _refused(exc) from exc
    return {"ok": True, **result}
