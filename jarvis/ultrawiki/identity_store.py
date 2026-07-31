"""The SQL half of UltraWiki identity resolution (design doc 05 · D-10).

:class:`IdentityMixin` is inherited by BOTH store backends, so the identity
rules exist exactly once. It relies on nothing but the small surface both
already provide — ``_txn()``, ``_fetchall()``, ``_fetchone()``,
``_ensure_open()`` — plus two dialect hooks the backends implement:
``_IDENTITY_PARAM`` (the placeholder) and ``_id_insert()`` (how a freshly
inserted row reports its id).

The contract this module is built to keep:

- **Deterministic evidence merges; everything else asks.** A shared e-mail,
  phone number or address-book slug fuses two entities on the spot. Name
  similarity — however striking — only ever writes a row into
  ``uw_confirm_queue``.
- **Ambiguity is never resolved by guessing.** An observation whose name
  points at several live entities links to none of them; it returns
  ``ResolutionKind.AMBIGUOUS`` and proposes the collisions instead.
- **Kinds are separate namespaces.** A person, a place and a project never
  match, merge or get proposed to one another, whatever they share. Identity
  is a question asked WITHIN one sort of thing.
- **Every merge is reversible, last-in-first-out.** ``uw_merge_log`` stores
  the identifier rows that moved, the duplicates that were dropped and the
  loser's own bookkeeping, which is exactly what
  :meth:`IdentityMixin.unmerge` replays — and a merge either side of which has
  since been merged away is refused rather than half-replayed.
- **A rejected pair stays rejected.** Undoing a merge (or rejecting a
  proposal) is durable and survives later merges on BOTH sides: the same
  evidence must never re-fuse the pair behind the user's back — not directly
  and not through a third entity either side has absorbed — and must never
  ask a second time.

Nothing here calls a model, touches the network, or runs on the voice hot
path; resolution happens on the write path (AP-9/AP-26).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from jarvis.ultrawiki.identity import (
    DETERMINISTIC_KINDS,
    FUZZY_CANDIDATE_LIMIT,
    LEN_WINDOW,
    MAX_PROPOSALS,
    PREFIX_BLOCK_CHARS,
    PROPOSE_THRESHOLD,
    EntityKind,
    IdentifierKind,
    IdentifierResult,
    IdentityError,
    MatchEvidence,
    MatchTier,
    QueueStatus,
    Resolution,
    ResolutionKind,
    SeedReport,
    could_match,
    escape_like,
    name_similarity,
    normalize_identifier,
    normalize_name,
    pair_key,
)

log = logging.getLogger(__name__)

__all__ = ["IdentityMixin", "seed_from_contacts"]


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class _Observed:
    """One normalized identifier as it arrived."""

    kind: IdentifierKind
    value: str
    display: str


@dataclass(slots=True)
class _Attached:
    """Result of attaching one identifier inside an open transaction."""

    entity_id: int
    identifier_id: int | None = None
    created: bool = False
    merged: list[int] = field(default_factory=list)
    queued: list[int] = field(default_factory=list)


class IdentityMixin:
    """Entities, identifiers, the confirmation queue and reversible merges."""

    # -- dialect hooks -------------------------------------------------------

    #: Parameter placeholder of the backend ("?" for SQLite, "%s" for
    #: Postgres). Every statement below is written in the SQLite dialect and
    #: translated once, so the two backends cannot drift apart on identity SQL.
    _IDENTITY_PARAM: str = "?"

    def _id_sql(self, sql: str) -> str:
        if self._IDENTITY_PARAM == "?":
            return sql
        return sql.replace("?", self._IDENTITY_PARAM)

    async def _id_insert(self, conn: Any, sql: str, params: Sequence[Any]) -> int:
        """Run an INSERT and return the new row id (backend-specific)."""
        raise NotImplementedError  # pragma: no cover — backends override

    # -- tiny query helpers --------------------------------------------------

    async def _id_rows(
        self, conn: Any, sql: str, params: Sequence[Any] = ()
    ) -> list[Any]:
        return await self._fetchall(conn, self._id_sql(sql), params)  # type: ignore[attr-defined]

    async def _id_row(
        self, conn: Any, sql: str, params: Sequence[Any] = ()
    ) -> Any | None:
        return await self._fetchone(conn, self._id_sql(sql), params)  # type: ignore[attr-defined]

    async def _id_exec(self, conn: Any, sql: str, params: Sequence[Any] = ()) -> None:
        await conn.execute(self._id_sql(sql), params)

    # -- chain resolution ----------------------------------------------------

    async def _chain(self, conn: Any, entity_id: int | None) -> int | None:
        """Follow ``merged_into`` to the surviving entity (cycle-safe)."""
        current = _as_int(entity_id)
        if current is None:
            return None
        seen: set[int] = set()
        while current not in seen:
            seen.add(current)
            row = await self._id_row(
                conn,
                "SELECT id, merged_into FROM uw_entities WHERE id = ?",
                (current,),
            )
            if row is None:
                return None
            nxt = _as_int(row["merged_into"])
            if nxt is None:
                return current
            current = nxt
        log.warning("UltraWiki identity: merge cycle at entity %s", current)
        return current

    async def resolve_entity_id(self, entity_id: int) -> int | None:
        """Public: the live entity a (possibly merged-away) id now points at."""
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        return await self._chain(conn, entity_id)

    async def _id_closure(self, conn: Any, entity_id: int | None) -> set[int]:
        """Every id whose merge chain ends at *entity_id* — itself included.

        The inverse of :meth:`_chain`, and the set every durable per-pair
        decision has to be read over: an id the user once judged is not the id
        that answers for it after a merge, so a guard that only looks at the
        two ids in front of it forgets the decision the moment either side
        absorbs somebody else.
        """
        root = _as_int(entity_id)
        if root is None:
            return set()
        closure = {root}
        frontier = [root]
        while frontier:
            current = frontier.pop()
            for row in await self._id_rows(
                conn, "SELECT id FROM uw_entities WHERE merged_into = ?", (current,)
            ):
                child = _as_int(row["id"])
                if child is None or child in closure:
                    continue
                closure.add(child)
                frontier.append(child)
        return closure

    async def _id_kind(self, conn: Any, entity_id: int) -> str:
        """The live kind of an entity ("" when it is gone)."""
        row = await self._id_row(
            conn, "SELECT kind FROM uw_entities WHERE id = ?", (int(entity_id),)
        )
        return "" if row is None else str(row["kind"])

    # -- entity CRUD ---------------------------------------------------------

    async def upsert_entity(
        self,
        *,
        display_name: str,
        kind: EntityKind | str = EntityKind.PERSON,
        source_ref: str = "",
        profile: dict[str, Any] | None = None,
    ) -> int:
        """Create an entity (and its own name identifier); returns its id.

        When ``source_ref`` is already known the existing entity is returned
        instead — that is what makes seeding re-runnable.
        """
        async with self._txn() as conn:  # type: ignore[attr-defined]
            return await self._id_create_entity(
                conn,
                display_name=display_name,
                kind=kind,
                source_ref=source_ref,
                profile=profile,
                now=_utc_now_iso(),
            )

    async def _id_create_entity(
        self,
        conn: Any,
        *,
        display_name: str,
        kind: EntityKind | str,
        source_ref: str,
        profile: dict[str, Any] | None,
        now: str,
        with_name_identifier: bool = True,
    ) -> int:
        resolved_kind = EntityKind(kind) if not isinstance(kind, EntityKind) else kind
        label = " ".join(str(display_name or "").split())
        if not label:
            raise IdentityError("an entity needs a non-empty display name")
        ref = str(source_ref or "").strip()
        if ref:
            existing = await self._id_row(
                conn, "SELECT id FROM uw_entities WHERE source_ref = ?", (ref,)
            )
            if existing is not None:
                known = await self._chain(conn, _as_int(existing["id"]))
                if known is not None:
                    return known
        canonical = normalize_name(label) or ""
        entity_id = await self._id_insert(
            conn,
            self._id_sql(
                "INSERT INTO uw_entities"
                " (kind, display_name, canonical_key, merged_into, source_ref,"
                "  profile_json, created_at, updated_at)"
                " VALUES (?, ?, ?, NULL, ?, ?, ?, ?)"
            ),
            (
                str(resolved_kind),
                label,
                canonical,
                ref,
                json.dumps(profile or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        # An entity labelled after an e-mail address (an observation that
        # carried no name at all) must NOT gain that address as a "name" —
        # every other address at the same domain would then look like a
        # near-name of it and flood the queue with nonsense proposals.
        if canonical and with_name_identifier:
            await self._id_write_identifier(
                conn,
                entity_id,
                IdentifierKind.NAME,
                canonical,
                label,
                ref,
                now=now,
            )
        return entity_id

    async def get_entity(self, entity_id: int) -> dict[str, Any] | None:
        """The raw entity row (following merges), or ``None``."""
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        live = await self._chain(conn, entity_id)
        if live is None:
            return None
        row = await self._id_row(
            conn, "SELECT * FROM uw_entities WHERE id = ?", (live,)
        )
        return None if row is None else self._id_entity_dict(row)

    @staticmethod
    def _id_entity_dict(row: Any) -> dict[str, Any]:
        try:
            profile = json.loads(str(row["profile_json"] or "{}"))
        except (TypeError, ValueError):
            profile = {}
        return {
            "id": int(row["id"]),
            "kind": str(row["kind"]),
            "display_name": str(row["display_name"]),
            "canonical_key": str(row["canonical_key"] or ""),
            "merged_into": _as_int(row["merged_into"]),
            "source_ref": str(row["source_ref"] or ""),
            "profile": profile if isinstance(profile, dict) else {},
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    async def set_entity_source_ref(self, entity_id: int, source_ref: str) -> bool:
        """Stamp provenance onto an entity that has none yet.

        Refuses silently when the ref already belongs to another row — the
        partial unique index would reject it anyway, and a seeding pass must
        never fail because two contacts resolved onto one person.
        """
        ref = str(source_ref or "").strip()
        if not ref:
            return False
        async with self._txn() as conn:  # type: ignore[attr-defined]
            live = await self._chain(conn, entity_id)
            if live is None:
                return False
            row = await self._id_row(
                conn,
                "SELECT id, source_ref FROM uw_entities WHERE id = ?",
                (live,),
            )
            if row is None or str(row["source_ref"] or ""):
                return False
            holder = await self._id_row(
                conn, "SELECT id FROM uw_entities WHERE source_ref = ?", (ref,)
            )
            if holder is not None:
                return False
            await self._id_exec(
                conn,
                "UPDATE uw_entities SET source_ref = ?, updated_at = ? WHERE id = ?",
                (ref, _utc_now_iso(), live),
            )
            return True

    # -- identifiers ---------------------------------------------------------

    async def _id_write_identifier(
        self,
        conn: Any,
        entity_id: int,
        kind: IdentifierKind,
        value: str,
        display: str,
        source_ref: str,
        *,
        now: str,
    ) -> tuple[int, bool]:
        """Insert one normalized identifier if the entity lacks it."""
        existing = await self._id_row(
            conn,
            "SELECT id FROM uw_identifiers"
            " WHERE entity_id = ? AND kind = ? AND value = ?",
            (entity_id, str(kind), value),
        )
        if existing is not None:
            return int(existing["id"]), False
        identifier_id = await self._id_insert(
            conn,
            self._id_sql(
                "INSERT INTO uw_identifiers"
                " (entity_id, kind, value, display_value, value_len, source_ref,"
                "  created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                entity_id,
                str(kind),
                value,
                display or value,
                len(value),
                str(source_ref or ""),
                now,
            ),
        )
        return identifier_id, True

    async def _id_holders(
        self,
        conn: Any,
        kind: IdentifierKind,
        value: str,
        *,
        exclude: int | None,
        entity_kind: EntityKind | str | None = None,
    ) -> list[int]:
        """Live entities that already hold this exact normalized identifier.

        ``entity_kind`` scopes the answer to entities that ARE the same sort of
        thing. Without it a place, a project and a person that happen to share
        a name — or an ``info@`` mailbox printed on a company page and in a
        colleague's signature — are candidates for one another, and the
        deterministic tier then fuses a city into a person on evidence that was
        never about identity at all. Kinds are separate namespaces; nothing
        below ever crosses them.
        """
        wanted = str(EntityKind(entity_kind)) if entity_kind is not None else ""
        rows = await self._id_rows(
            conn,
            "SELECT DISTINCT entity_id FROM uw_identifiers"
            " WHERE kind = ? AND value = ?",
            (str(kind), value),
        )
        holders: list[int] = []
        for row in rows:
            live = await self._chain(conn, _as_int(row["entity_id"]))
            if live is None or live == exclude or live in holders:
                continue
            if wanted and await self._id_kind(conn, live) != wanted:
                continue
            holders.append(live)
        return holders

    async def add_identifier(
        self,
        entity_id: int,
        kind: IdentifierKind | str,
        raw_value: str,
        *,
        source_ref: str = "",
    ) -> IdentifierResult:
        """Attach one raw handle to an entity, applying the tier rules.

        A deterministic kind already held by another entity MERGES the two; a
        name or handle collision is proposed instead. An unusable value (an
        unparsable e-mail, a two-digit phone number) is dropped and reported
        as ``identifier_id=None``.
        """
        try:
            resolved_kind = IdentifierKind(kind)
        except ValueError as exc:
            raise IdentityError(f"unknown identifier kind: {kind!r}") from exc
        value = normalize_identifier(resolved_kind, raw_value)
        if not value:
            return IdentifierResult(entity_id=_as_int(entity_id))
        async with self._txn() as conn:  # type: ignore[attr-defined]
            live = await self._chain(conn, entity_id)
            if live is None:
                raise IdentityError(f"unknown entity id: {entity_id!r}")
            attached = await self._id_attach(
                conn,
                live,
                _Observed(resolved_kind, value, str(raw_value or "")),
                source_ref=source_ref,
                now=_utc_now_iso(),
            )
        return IdentifierResult(
            entity_id=attached.entity_id,
            identifier_id=attached.identifier_id,
            created=attached.created,
            merged=tuple(attached.merged),
            queued=tuple(attached.queued),
        )

    async def _id_attach(
        self,
        conn: Any,
        entity_id: int,
        observed: _Observed,
        *,
        source_ref: str,
        now: str,
    ) -> _Attached:
        out = _Attached(entity_id=entity_id)
        holders = await self._id_holders(
            conn,
            observed.kind,
            observed.value,
            exclude=entity_id,
            entity_kind=await self._id_kind(conn, entity_id) or None,
        )
        if holders and observed.kind in DETERMINISTIC_KINDS:
            evidence = (
                MatchEvidence(
                    tier=MatchTier.DETERMINISTIC,
                    kind=str(observed.kind),
                    value=observed.value,
                ),
            )
            candidates = [entity_id, *holders]
            winner = await self._id_pick_winner(conn, candidates)
            for other in candidates:
                if other == winner:
                    continue
                merged_id = await self._id_merge(
                    conn,
                    winner,
                    other,
                    tier=MatchTier.DETERMINISTIC,
                    reason=f"shared {observed.kind}",
                    evidence=evidence,
                    queue_id=None,
                    now=now,
                )
                if merged_id:
                    out.merged.append(merged_id)
            # NOT `winner`: a merge the rejected-pair guard refused leaves the
            # caller's entity alive and separate, and the handle then belongs
            # to the entity that was actually named. Writing it onto the winner
            # anyway would report success while the asked-for entity gained
            # nothing — the caller's own e-mail address landing on the person
            # they told us it is NOT.
            out.entity_id = await self._chain(conn, entity_id) or entity_id
        elif holders:
            evidence = (
                MatchEvidence(
                    tier=MatchTier.PROBABLE,
                    kind=f"{observed.kind}_exact",
                    value=observed.value,
                ),
            )
            for other in holders:
                queue_id = await self._id_enqueue(
                    conn, entity_id, other, 1.0, evidence, now=now
                )
                if queue_id:
                    out.queued.append(queue_id)
        identifier_id, created = await self._id_write_identifier(
            conn,
            out.entity_id,
            observed.kind,
            observed.value,
            observed.display,
            source_ref,
            now=now,
        )
        out.identifier_id = identifier_id
        out.created = created
        return out

    # -- winner selection ----------------------------------------------------

    async def _id_pick_winner(self, conn: Any, entity_ids: Iterable[int]) -> int:
        """Deterministic survivor of a merge — never "whichever came first".

        A hand-curated address-book record outranks an inferred one; among
        equals the older id wins. Deterministic so that re-running an import
        converges on the same graph instead of oscillating.
        """
        rows: list[tuple[int, int]] = []
        for entity_id in dict.fromkeys(int(x) for x in entity_ids):
            row = await self._id_row(
                conn,
                "SELECT id, source_ref FROM uw_entities WHERE id = ?",
                (entity_id,),
            )
            if row is None:
                continue
            seeded = 0 if str(row["source_ref"] or "") else 1
            rows.append((seeded, int(row["id"])))
        if not rows:
            raise IdentityError("cannot pick a merge winner among unknown entities")
        rows.sort()
        return rows[0][1]

    # -- confirmation queue --------------------------------------------------

    async def _id_enqueue(
        self,
        conn: Any,
        left_id: int,
        right_id: int,
        score: float,
        evidence: Sequence[MatchEvidence],
        *,
        now: str,
    ) -> int | None:
        """Propose a pair. Returns the queue id, or ``None`` when the pair is
        already decided (a rejected pair is never asked about again)."""
        left = await self._chain(conn, left_id)
        right = await self._chain(conn, right_id)
        if left is None or right is None or left == right:
            return None
        if await self._id_pair_rejected(conn, left, right):
            # Already settled — through these two ids or through anything they
            # have since absorbed. Asking again is the second half of the same
            # promise the merge guard keeps.
            return None
        key = pair_key(left, right)
        payload = json.dumps([item.to_dict() for item in evidence], ensure_ascii=False)
        row = await self._id_row(
            conn,
            "SELECT id, status, score FROM uw_confirm_queue WHERE pair_key = ?",
            (key,),
        )
        if row is None:
            low, high = sorted((left, right))
            return await self._id_insert(
                conn,
                self._id_sql(
                    "INSERT INTO uw_confirm_queue"
                    " (pair_key, left_entity_id, right_entity_id, status, score,"
                    "  evidence_json, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    key,
                    low,
                    high,
                    str(QueueStatus.PENDING),
                    float(score),
                    payload,
                    now,
                    now,
                ),
            )
        if str(row["status"]) != str(QueueStatus.PENDING):
            return None
        best = max(float(row["score"] or 0.0), float(score))
        await self._id_exec(
            conn,
            "UPDATE uw_confirm_queue"
            " SET score = ?, evidence_json = ?, updated_at = ? WHERE id = ?",
            (best, payload, now, int(row["id"])),
        )
        return int(row["id"])

    async def _id_pair_rejected(self, conn: Any, left: int, right: int) -> bool:
        """Has the user declared these two different — directly or via a merge?

        Checking the pair key of the two ids at hand is not enough, and the gap
        is not theoretical: reject A/B, let B merge into C on a shared mailbox,
        then let A meet C on a shared phone number, and the direct key (A, C)
        is unknown — so the layer silently re-fuses exactly the two identities
        the user separated, with no queue row and no prompt.

        The decision therefore lives on the whole merge CLOSURE of both sides:
        a rejection stands as long as one of its ids still answers for this
        side and the other still answers for that one.
        """
        left_side = await self._id_closure(conn, left)
        right_side = await self._id_closure(conn, right)
        if not left_side or not right_side or left_side & right_side:
            return False
        ids = sorted(left_side | right_side)
        marks = ",".join("?" for _ in ids)
        rows = await self._id_rows(
            conn,
            "SELECT left_entity_id, right_entity_id FROM uw_confirm_queue"  # noqa: S608 — placeholder marks only
            f" WHERE status = ? AND (left_entity_id IN ({marks})"
            f" OR right_entity_id IN ({marks}))",
            (str(QueueStatus.REJECTED), *ids, *ids),
        )
        for row in rows:
            one = _as_int(row["left_entity_id"])
            two = _as_int(row["right_entity_id"])
            if one is None or two is None:
                continue
            if (one in left_side and two in right_side) or (
                one in right_side and two in left_side
            ):
                return True
        return False

    # -- merge / unmerge -----------------------------------------------------

    async def merge_entities(
        self,
        winner_id: int,
        loser_id: int,
        *,
        tier: MatchTier | str = MatchTier.DETERMINISTIC,
        reason: str = "",
        evidence: Sequence[MatchEvidence] = (),
    ) -> int:
        """Merge two entities; returns the ``uw_merge_log`` id (0 = no-op)."""
        async with self._txn() as conn:  # type: ignore[attr-defined]
            return await self._id_merge(
                conn,
                winner_id,
                loser_id,
                tier=MatchTier(tier),
                reason=reason,
                evidence=evidence,
                queue_id=None,
                now=_utc_now_iso(),
            )

    async def _id_merge(
        self,
        conn: Any,
        winner_id: int,
        loser_id: int,
        *,
        tier: MatchTier,
        reason: str,
        evidence: Sequence[MatchEvidence],
        queue_id: int | None,
        now: str,
    ) -> int:
        winner = await self._chain(conn, winner_id)
        loser = await self._chain(conn, loser_id)
        if winner is None or loser is None:
            raise IdentityError(
                f"cannot merge unknown entities ({winner_id!r}, {loser_id!r})"
            )
        if winner == loser:
            return 0
        if queue_id is None and await self._id_pair_rejected(conn, winner, loser):
            # The user has already said these two are different. Deterministic
            # evidence does not overrule that: a shared mailbox or a family
            # phone is exactly how a rejected pair keeps colliding.
            log.info(
                "UltraWiki identity: refusing to re-merge the rejected pair %s/%s",
                winner,
                loser,
            )
            return 0

        winner_row = await self._id_row(
            conn,
            "SELECT id, kind, source_ref FROM uw_entities WHERE id = ?",
            (winner,),
        )
        loser_row = await self._id_row(
            conn,
            "SELECT id, kind, source_ref FROM uw_entities WHERE id = ?",
            (loser,),
        )
        if winner_row is None or loser_row is None:  # pragma: no cover — chain checked
            raise IdentityError("merge lost one of its entities mid-transaction")
        if str(winner_row["kind"]) != str(loser_row["kind"]):
            # Kinds are separate namespaces (see `_id_holders`). Fusing across
            # them destroys BOTH rows' meaning — the city keeps answering as
            # the person, and the person inherits the city's events.
            raise IdentityError(
                f"cannot merge a {loser_row['kind']} into a {winner_row['kind']}"
                f" (entities {loser} and {winner}) — they are different kinds"
            )

        held = {
            (str(row["kind"]), str(row["value"]))
            for row in await self._id_rows(
                conn,
                "SELECT kind, value FROM uw_identifiers WHERE entity_id = ?",
                (winner,),
            )
        }
        moved: list[int] = []
        dropped: list[dict[str, Any]] = []
        for row in await self._id_rows(
            conn,
            "SELECT id, kind, value, display_value, source_ref, created_at"
            " FROM uw_identifiers WHERE entity_id = ? ORDER BY id",
            (loser,),
        ):
            key = (str(row["kind"]), str(row["value"]))
            if key in held:
                dropped.append(
                    {
                        "kind": str(row["kind"]),
                        "value": str(row["value"]),
                        "display_value": str(row["display_value"] or ""),
                        "source_ref": str(row["source_ref"] or ""),
                        "created_at": str(row["created_at"]),
                    }
                )
                await self._id_exec(
                    conn, "DELETE FROM uw_identifiers WHERE id = ?", (int(row["id"]),)
                )
                continue
            await self._id_exec(
                conn,
                "UPDATE uw_identifiers SET entity_id = ? WHERE id = ?",
                (winner, int(row["id"])),
            )
            moved.append(int(row["id"]))
            held.add(key)

        loser_ref = str(loser_row["source_ref"] or "")
        winner_took_ref = False
        if loser_ref:
            await self._id_exec(
                conn,
                "UPDATE uw_entities SET source_ref = '', updated_at = ? WHERE id = ?",
                (now, loser),
            )
            if not str(winner_row["source_ref"] or ""):
                await self._id_exec(
                    conn,
                    "UPDATE uw_entities SET source_ref = ?, updated_at = ?"
                    " WHERE id = ?",
                    (loser_ref, now, winner),
                )
                winner_took_ref = True
        await self._id_exec(
            conn,
            "UPDATE uw_entities SET merged_into = ?, updated_at = ? WHERE id = ?",
            (winner, now, loser),
        )

        undo = {
            "moved": moved,
            "dropped": dropped,
            "loser_source_ref": loser_ref,
            "winner_took_source_ref": winner_took_ref,
            "loser_prev_merged_into": None,
        }
        merge_log_id = await self._id_insert(
            conn,
            self._id_sql(
                "INSERT INTO uw_merge_log"
                " (winner_id, loser_id, tier, reason, evidence_json, undo_json,"
                "  queue_id, merged_at, undone_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)"
            ),
            (
                winner,
                loser,
                str(tier),
                str(reason or ""),
                json.dumps(
                    [item.to_dict() for item in evidence], ensure_ascii=False
                ),
                json.dumps(undo, ensure_ascii=False),
                queue_id,
                now,
            ),
        )
        # Self-heal the queue: an open proposal for this exact pair is decided.
        await self._id_exec(
            conn,
            "UPDATE uw_confirm_queue SET status = ?, decided_at = ?,"
            " decided_by = ?, updated_at = ?"
            " WHERE pair_key = ? AND status = ?",
            (
                str(QueueStatus.CONFIRMED),
                now,
                "merge",
                now,
                pair_key(winner, loser),
                str(QueueStatus.PENDING),
            ),
        )
        log.info(
            "UltraWiki identity: merged entity %s into %s (%s)", loser, winner, tier
        )
        return merge_log_id

    async def unmerge(self, merge_log_id: int) -> bool:
        """Reverse one merge exactly, restoring the state before it.

        Identifier rows go back, dropped duplicates are re-created, the loser
        stops being a tombstone, and the pair is recorded as REJECTED so the
        same evidence cannot silently fuse it again on the next import.
        """
        now = _utc_now_iso()
        async with self._txn() as conn:  # type: ignore[attr-defined]
            row = await self._id_row(
                conn, "SELECT * FROM uw_merge_log WHERE id = ?", (int(merge_log_id),)
            )
            if row is None:
                raise IdentityError(f"unknown merge id: {merge_log_id!r}")
            if row["undone_at"]:
                raise IdentityError(f"merge {merge_log_id} was already undone")
            winner = int(row["winner_id"])
            loser = int(row["loser_id"])
            # Merges undo strictly last-in-first-out, and BOTH sides can be
            # shadowed. Watching only the loser misses the chain that matters
            # most: when the WINNER was itself merged away afterwards, the
            # identifiers this merge moved have travelled on, and replaying
            # this undo alone puts them back on the loser while the later undo
            # then hands them to the entity in the middle — which never owned
            # them. The identifier ends up on a stranger and the entity that
            # brought it is left empty.
            later = await self._id_row(
                conn,
                "SELECT id FROM uw_merge_log"
                " WHERE loser_id IN (?, ?) AND undone_at IS NULL AND id > ?"
                " ORDER BY id LIMIT 1",
                (loser, winner, int(merge_log_id)),
            )
            if later is not None:
                raise IdentityError(
                    f"merge {merge_log_id} is shadowed by the later merge "
                    f"{int(later['id'])} — undo that one first"
                )
            loser_row = await self._id_row(
                conn, "SELECT id FROM uw_entities WHERE id = ?", (loser,)
            )
            if loser_row is None:
                raise IdentityError(
                    f"entity {loser} no longer exists — merge {merge_log_id} "
                    "cannot be undone"
                )
            try:
                undo = json.loads(str(row["undo_json"] or "{}"))
            except (TypeError, ValueError):
                undo = {}

            for identifier_id in undo.get("moved", []) or []:
                await self._id_exec(
                    conn,
                    "UPDATE uw_identifiers SET entity_id = ? WHERE id = ?",
                    (loser, int(identifier_id)),
                )
            for payload in undo.get("dropped", []) or []:
                await self._id_write_identifier(
                    conn,
                    loser,
                    IdentifierKind(str(payload.get("kind"))),
                    str(payload.get("value", "")),
                    str(payload.get("display_value", "")),
                    str(payload.get("source_ref", "")),
                    now=str(payload.get("created_at") or now),
                )
            if undo.get("winner_took_source_ref"):
                await self._id_exec(
                    conn,
                    "UPDATE uw_entities SET source_ref = '', updated_at = ?"
                    " WHERE id = ?",
                    (now, winner),
                )
            loser_ref = str(undo.get("loser_source_ref") or "")
            if loser_ref:
                await self._id_exec(
                    conn,
                    "UPDATE uw_entities SET source_ref = ?, updated_at = ?"
                    " WHERE id = ?",
                    (loser_ref, now, loser),
                )
            await self._id_exec(
                conn,
                "UPDATE uw_entities SET merged_into = ?, updated_at = ? WHERE id = ?",
                (_as_int(undo.get("loser_prev_merged_into")), now, loser),
            )
            await self._id_exec(
                conn,
                "UPDATE uw_merge_log SET undone_at = ? WHERE id = ?",
                (now, int(merge_log_id)),
            )
            await self._id_record_rejection(conn, winner, loser, now=now)
        log.info(
            "UltraWiki identity: unmerged entity %s from %s (merge %s)",
            loser,
            winner,
            merge_log_id,
        )
        return True

    async def _id_record_rejection(
        self, conn: Any, left: int, right: int, *, now: str, decided_by: str = "unmerge"
    ) -> None:
        """Durably mark a pair as "not the same", creating the row if needed."""
        key = pair_key(left, right)
        row = await self._id_row(
            conn, "SELECT id FROM uw_confirm_queue WHERE pair_key = ?", (key,)
        )
        if row is None:
            low, high = sorted((int(left), int(right)))
            await self._id_insert(
                conn,
                self._id_sql(
                    "INSERT INTO uw_confirm_queue"
                    " (pair_key, left_entity_id, right_entity_id, status, score,"
                    "  evidence_json, created_at, updated_at, decided_at,"
                    "  decided_by)"
                    " VALUES (?, ?, ?, ?, 0, '[]', ?, ?, ?, ?)"
                ),
                (key, low, high, str(QueueStatus.REJECTED), now, now, now, decided_by),
            )
            return
        await self._id_exec(
            conn,
            "UPDATE uw_confirm_queue SET status = ?, decided_at = ?,"
            " decided_by = ?, updated_at = ? WHERE id = ?",
            (str(QueueStatus.REJECTED), now, decided_by, now, int(row["id"])),
        )

    async def confirm_merge(self, queue_id: int, *, decided_by: str = "user") -> int:
        """Apply a queued proposal; returns the ``uw_merge_log`` id."""
        now = _utc_now_iso()
        async with self._txn() as conn:  # type: ignore[attr-defined]
            row = await self._id_row(
                conn, "SELECT * FROM uw_confirm_queue WHERE id = ?", (int(queue_id),)
            )
            if row is None:
                raise IdentityError(f"unknown confirmation id: {queue_id!r}")
            if str(row["status"]) != str(QueueStatus.PENDING):
                raise IdentityError(
                    f"confirmation {queue_id} was already {row['status']}"
                )
            left = await self._chain(conn, _as_int(row["left_entity_id"]))
            right = await self._chain(conn, _as_int(row["right_entity_id"]))
            await self._id_exec(
                conn,
                "UPDATE uw_confirm_queue SET status = ?, decided_at = ?,"
                " decided_by = ?, updated_at = ? WHERE id = ?",
                (str(QueueStatus.CONFIRMED), now, decided_by, now, int(queue_id)),
            )
            if left is None or right is None or left == right:
                return 0
            try:
                raw = json.loads(str(row["evidence_json"] or "[]"))
            except (TypeError, ValueError):
                raw = []
            evidence = tuple(
                MatchEvidence.from_dict(item)
                for item in raw
                if isinstance(item, dict)
            )
            winner = await self._id_pick_winner(conn, (left, right))
            loser = right if winner == left else left
            return await self._id_merge(
                conn,
                winner,
                loser,
                tier=MatchTier.PROBABLE,
                reason=f"confirmed by {decided_by}",
                evidence=evidence,
                queue_id=int(queue_id),
                now=now,
            )

    async def reject_merge(self, queue_id: int, *, decided_by: str = "user") -> bool:
        """Decline a proposal permanently (it is never proposed again)."""
        now = _utc_now_iso()
        async with self._txn() as conn:  # type: ignore[attr-defined]
            row = await self._id_row(
                conn, "SELECT * FROM uw_confirm_queue WHERE id = ?", (int(queue_id),)
            )
            if row is None:
                raise IdentityError(f"unknown confirmation id: {queue_id!r}")
            if str(row["status"]) == str(QueueStatus.CONFIRMED):
                raise IdentityError(
                    f"confirmation {queue_id} was already applied — unmerge instead"
                )
            await self._id_exec(
                conn,
                "UPDATE uw_confirm_queue SET status = ?, decided_at = ?,"
                " decided_by = ?, updated_at = ? WHERE id = ?",
                (str(QueueStatus.REJECTED), now, decided_by, now, int(queue_id)),
            )
            return True

    # -- resolution ----------------------------------------------------------

    async def resolve_identity(
        self,
        *,
        name: str = "",
        emails: Sequence[str] = (),
        phones: Sequence[str] = (),
        handles: Sequence[str] = (),
        contact_slug: str = "",
        kind: EntityKind | str = EntityKind.PERSON,
        source_ref: str = "",
        create: bool = True,
    ) -> Resolution:
        """Map one observation onto an entity, applying the three tiers.

        Deterministic identifiers decide; an exact name matching exactly ONE
        live entity anchors onto it; a name matching several anchors onto none
        (``AMBIGUOUS``) and proposes the collisions; anything else creates a
        new entity and proposes its near-names. **No path here ever merges on
        name evidence.**

        Anchoring on an identical name is a lookup, not a merge: no entity is
        destroyed and nothing is fused — which is why "Ultra-Wiki" joins
        "ultra-wiki" silently while "UltraWiki" only ever gets *proposed* to
        both.

        ``source_ref`` labels the identifier rows this observation writes
        (provenance); stamping the ENTITY's own provenance is the separate,
        idempotent :meth:`set_entity_source_ref`.
        """
        async with self._txn() as conn:  # type: ignore[attr-defined]
            return await self._id_resolve_one(
                conn,
                name=name,
                emails=emails,
                phones=phones,
                handles=handles,
                contact_slug=contact_slug,
                kind=kind,
                source_ref=source_ref,
                create=create,
            )

    async def _id_resolve_one(
        self,
        conn: Any,
        *,
        name: str = "",
        emails: Sequence[str] = (),
        phones: Sequence[str] = (),
        handles: Sequence[str] = (),
        contact_slug: str = "",
        kind: EntityKind | str = EntityKind.PERSON,
        source_ref: str = "",
        create: bool = True,
    ) -> Resolution:
        """:meth:`resolve_identity` for a transaction the caller already owns."""
        observed = self._id_observe(
            name=name,
            emails=emails,
            phones=phones,
            handles=handles,
            contact_slug=contact_slug,
        )
        return await self._id_resolve(
            conn,
            observed,
            name_value=normalize_name(name) if name else None,
            display_name=str(name or "").strip(),
            kind=kind,
            source_ref=source_ref,
            create=create,
            now=_utc_now_iso(),
        )

    @asynccontextmanager
    async def identity_batch(
        self,
    ) -> AsyncIterator[Callable[..., Awaitable[Resolution]]]:
        """Resolve SEVERAL observations inside ONE transaction.

        :meth:`resolve_identity` opens its own transaction per call, which is
        right for a single observation and wasteful for a document that names a
        dozen people: N names cost N ``BEGIN``/``COMMIT`` round trips and N
        turns of the store lock. Yields a callable with
        :meth:`resolve_identity`'s keyword signature, bound to one open
        transaction.

        The trade belongs to the caller and is deliberately not hidden: one
        transaction is ONE failure domain. A statement that raises inside the
        block rolls the WHOLE batch back — including the entities earlier calls
        created — so a caller that must not lose the rest catches the exception
        and replays the observations through :meth:`resolve_identity`, which is
        independent per call. Not a fallback for convenience: on Postgres a
        failed statement poisons the surrounding transaction, so continuing
        inside the block is not an option in the first place.
        """
        async with self._txn() as conn:  # type: ignore[attr-defined]
            yield partial(self._id_resolve_one, conn)

    @staticmethod
    def _id_observe(
        *,
        name: str,
        emails: Sequence[str],
        phones: Sequence[str],
        handles: Sequence[str],
        contact_slug: str,
    ) -> list[_Observed]:
        raw: list[tuple[IdentifierKind, str]] = []
        if contact_slug:
            raw.append((IdentifierKind.CONTACT, contact_slug))
        raw.extend((IdentifierKind.EMAIL, value) for value in emails)
        raw.extend((IdentifierKind.PHONE, value) for value in phones)
        raw.extend((IdentifierKind.HANDLE, value) for value in handles)
        if name:
            raw.append((IdentifierKind.NAME, name))
        seen: set[tuple[str, str]] = set()
        observed: list[_Observed] = []
        for kind, value in raw:
            normalized = normalize_identifier(kind, value)
            if not normalized or (str(kind), normalized) in seen:
                continue
            seen.add((str(kind), normalized))
            observed.append(_Observed(kind, normalized, str(value)))
        return observed

    async def _id_resolve(
        self,
        conn: Any,
        observed: Sequence[_Observed],
        *,
        name_value: str | None,
        display_name: str,
        kind: EntityKind | str,
        source_ref: str,
        create: bool,
        now: str,
    ) -> Resolution:
        merged: list[int] = []
        queued: list[int] = []
        evidence: list[MatchEvidence] = []
        # Every lookup below is scoped to the kind the observation claims to
        # be: a place is never a candidate for a person, however identical the
        # spelling or the mailbox (see `_id_holders`).
        want_kind = EntityKind(kind)

        # Tier 1 — deterministic anchors.
        anchors: list[int] = []
        for item in observed:
            if item.kind not in DETERMINISTIC_KINDS:
                continue
            for holder in await self._id_holders(
                conn, item.kind, item.value, exclude=None, entity_kind=want_kind
            ):
                if holder not in anchors:
                    anchors.append(holder)
                    evidence.append(
                        MatchEvidence(
                            tier=MatchTier.DETERMINISTIC,
                            kind=str(item.kind),
                            value=item.value,
                        )
                    )
        entity_id: int | None = None
        resolution_kind = ResolutionKind.UNRESOLVED
        created = False
        if anchors:
            entity_id = await self._id_pick_winner(conn, anchors)
            resolution_kind = ResolutionKind.DETERMINISTIC
            for other in anchors:
                if other == entity_id:
                    continue
                merge_id = await self._id_merge(
                    conn,
                    entity_id,
                    other,
                    tier=MatchTier.DETERMINISTIC,
                    reason="shared unique identifier",
                    evidence=tuple(evidence),
                    queue_id=None,
                    now=now,
                )
                if merge_id:
                    merged.append(merge_id)
                else:
                    # A rejected pair refused the merge; the observation still
                    # belongs to the winner, both identities keep answering.
                    entity_id = await self._chain(conn, entity_id) or entity_id

        # Tier 2 — an exact name is an anchor only when it is unambiguous.
        if entity_id is None and name_value:
            holders = await self._id_holders(
                conn,
                IdentifierKind.NAME,
                name_value,
                exclude=None,
                entity_kind=want_kind,
            )
            if len(holders) == 1:
                entity_id = holders[0]
                resolution_kind = ResolutionKind.NAME_ANCHOR
                evidence.append(
                    MatchEvidence(
                        tier=MatchTier.PROBABLE, kind="name_exact", value=name_value
                    )
                )
            elif len(holders) > 1:
                pair_evidence = (
                    MatchEvidence(
                        tier=MatchTier.PROBABLE, kind="name_exact", value=name_value
                    ),
                )
                for index, left in enumerate(holders):
                    for right in holders[index + 1 :]:
                        queue_id = await self._id_enqueue(
                            conn, left, right, 1.0, pair_evidence, now=now
                        )
                        if queue_id:
                            queued.append(queue_id)
                return Resolution(
                    entity_id=None,
                    kind=ResolutionKind.AMBIGUOUS,
                    merged=tuple(merged),
                    queued=tuple(queued),
                    ambiguous=tuple(holders),
                    evidence=tuple(pair_evidence),
                )

        # Tier 3 — nothing recognized it: a new identity, plus proposals.
        if entity_id is None:
            if not create:
                return Resolution(
                    entity_id=None,
                    kind=ResolutionKind.UNRESOLVED,
                    merged=tuple(merged),
                    queued=tuple(queued),
                    evidence=tuple(evidence),
                )
            label = display_name or (observed[0].display if observed else "")
            if not str(label or "").strip():
                raise IdentityError(
                    "an observation needs a name or at least one identifier"
                )
            entity_id = await self._id_create_entity(
                conn,
                display_name=label,
                kind=kind,
                source_ref="",
                profile=None,
                now=now,
                with_name_identifier=bool(name_value),
            )
            created = True
            resolution_kind = ResolutionKind.CREATED

        for item in observed:
            attached = await self._id_attach(
                conn, entity_id, item, source_ref=source_ref, now=now
            )
            entity_id = attached.entity_id
            merged.extend(attached.merged)
            queued.extend(attached.queued)

        if created and name_value:
            for queue_id, score, hit in await self._id_fuzzy_proposals(
                conn, entity_id, name_value, entity_kind=want_kind, now=now
            ):
                queued.append(queue_id)
                evidence.append(
                    MatchEvidence(
                        tier=MatchTier.PROBABLE,
                        kind="name_similar",
                        value=hit,
                        score=score,
                    )
                )

        return Resolution(
            entity_id=entity_id,
            kind=resolution_kind,
            created=created,
            merged=tuple(dict.fromkeys(merged)),
            queued=tuple(dict.fromkeys(queued)),
            evidence=tuple(evidence),
        )

    async def _id_fuzzy_proposals(
        self,
        conn: Any,
        entity_id: int,
        name_value: str,
        *,
        entity_kind: EntityKind | str,
        now: str,
    ) -> list[tuple[int, float, str]]:
        """Propose the near-names of a freshly created entity.

        Blocking happens in SQL (length window OR shared prefix — the exact
        predicate :func:`identity.could_match` re-applies in Python, and the
        entity KIND scopes it, because two things of different sorts are not
        near-duplicates however alike they read), scoring happens in Python,
        and both are capped so one observation can never flood the queue or
        the CPU.
        """
        prefix = escape_like(name_value[:PREFIX_BLOCK_CHARS])
        rows = await self._id_rows(
            conn,
            "SELECT i.entity_id AS entity_id, i.value AS value"
            " FROM uw_identifiers i"
            " JOIN uw_entities e ON e.id = i.entity_id"
            " WHERE i.kind = ? AND e.merged_into IS NULL AND e.kind = ?"
            "   AND i.entity_id != ?"
            "   AND (i.value_len BETWEEN ? AND ? OR i.value LIKE ? ESCAPE '\\')"
            " ORDER BY i.id LIMIT ?",
            (
                str(IdentifierKind.NAME),
                str(EntityKind(entity_kind)),
                entity_id,
                max(0, len(name_value) - LEN_WINDOW),
                len(name_value) + LEN_WINDOW,
                f"{prefix}%",
                FUZZY_CANDIDATE_LIMIT,
            ),
        )
        best: dict[int, tuple[float, str]] = {}
        for row in rows:
            candidate = str(row["value"])
            if candidate == name_value or not could_match(name_value, candidate):
                continue
            score = name_similarity(name_value, candidate)
            if score < PROPOSE_THRESHOLD:
                continue
            holder = _as_int(row["entity_id"])
            if holder is None:
                continue
            if holder not in best or score > best[holder][0]:
                best[holder] = (score, candidate)
        ranked = sorted(best.items(), key=lambda pair: (-pair[1][0], pair[0]))
        proposals: list[tuple[int, float, str]] = []
        for holder, (score, candidate) in ranked[:MAX_PROPOSALS]:
            evidence = (
                MatchEvidence(
                    tier=MatchTier.PROBABLE,
                    kind="name_similar",
                    value=candidate,
                    score=score,
                ),
            )
            queue_id = await self._id_enqueue(
                conn, entity_id, holder, score, evidence, now=now
            )
            if queue_id:
                proposals.append((queue_id, score, candidate))
        return proposals

    # -- read surface (the People view and every later phase) ----------------

    async def list_people(
        self,
        *,
        query: str = "",
        kind: EntityKind | str | None = EntityKind.PERSON,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Live entities with their identifier counts, name-ordered.

        ``kind=None`` lists every kind. ``query`` matches the display name or
        any identifier value, case-insensitively.
        """
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        clauses = ["e.merged_into IS NULL"]
        params: list[Any] = []
        if kind is not None:
            clauses.append("e.kind = ?")
            params.append(str(EntityKind(kind)))
        needle = " ".join(str(query or "").split()).lower()
        if needle:
            pattern = f"%{escape_like(needle)}%"
            clauses.append(
                "(LOWER(e.display_name) LIKE ? ESCAPE '\\'"
                " OR EXISTS (SELECT 1 FROM uw_identifiers x"
                "            WHERE x.entity_id = e.id"
                "              AND LOWER(x.value) LIKE ? ESCAPE '\\'))"
            )
            params.extend([pattern, pattern])
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = await self._id_rows(
            conn,
            "SELECT e.id, e.kind, e.display_name, e.canonical_key,"  # noqa: S608 — code-owned literals, every value bound
            " e.source_ref, e.created_at, e.updated_at,"
            " (SELECT COUNT(*) FROM uw_identifiers x WHERE x.entity_id = e.id)"
            "   AS identifier_count"
            " FROM uw_entities e"
            f" WHERE {' AND '.join(clauses)}"
            " ORDER BY e.canonical_key, e.id LIMIT ? OFFSET ?",
            params,
        )
        return [
            {
                "id": int(row["id"]),
                "kind": str(row["kind"]),
                "display_name": str(row["display_name"]),
                "source_ref": str(row["source_ref"] or ""),
                "identifier_count": int(row["identifier_count"] or 0),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    async def get_person(self, entity_id: int) -> dict[str, Any] | None:
        """One profile: identifiers by kind, what was merged in, what is open.

        Accepts a merged-away id and forwards to the survivor (reporting the
        forward in ``requested_id``), so a stale link never 404s.
        """
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        live = await self._chain(conn, entity_id)
        if live is None:
            return None
        row = await self._id_row(
            conn, "SELECT * FROM uw_entities WHERE id = ?", (live,)
        )
        if row is None:  # pragma: no cover — chain guarantees the row
            return None
        profile = self._id_entity_dict(row)
        identifiers: list[dict[str, Any]] = [
            {
                "id": int(item["id"]),
                "kind": str(item["kind"]),
                "value": str(item["value"]),
                "display_value": str(item["display_value"] or ""),
                "source_ref": str(item["source_ref"] or ""),
            }
            for item in await self._id_rows(
                conn,
                "SELECT id, kind, value, display_value, source_ref"
                " FROM uw_identifiers WHERE entity_id = ? ORDER BY kind, id",
                (live,),
            )
        ]
        by_kind: dict[str, list[str]] = {str(k): [] for k in IdentifierKind}
        for item in identifiers:
            by_kind[item["kind"]].append(item["display_value"] or item["value"])
        profile["identifiers"] = identifiers
        profile["emails"] = by_kind[str(IdentifierKind.EMAIL)]
        profile["phones"] = by_kind[str(IdentifierKind.PHONE)]
        profile["handles"] = by_kind[str(IdentifierKind.HANDLE)]
        profile["names"] = by_kind[str(IdentifierKind.NAME)]
        profile["contacts"] = by_kind[str(IdentifierKind.CONTACT)]
        profile["merged_from"] = [
            {"id": int(item["id"]), "display_name": str(item["display_name"])}
            for item in await self._id_rows(
                conn,
                "SELECT id, display_name FROM uw_entities"
                " WHERE merged_into = ? ORDER BY id",
                (live,),
            )
        ]
        profile["merges"] = await self.list_merge_log(entity_id=live)
        profile["pending_proposals"] = [
            entry
            for entry in await self.list_confirm_queue(status=QueueStatus.PENDING)
            if entry["left"]["id"] == live or entry["right"]["id"] == live
        ]
        profile["requested_id"] = int(entity_id)
        return profile

    async def list_confirm_queue(
        self,
        *,
        status: QueueStatus | str | None = QueueStatus.PENDING,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Open (or decided) merge proposals, strongest evidence first.

        Rows whose two sides have meanwhile become the same entity are skipped
        — the queue self-heals instead of asking about a settled question. So
        are rows across two KINDS, which no current path can produce and which
        a merge would refuse: a corpus proposed before that rule existed drops
        its cross-kind leftovers on the next read rather than offering the user
        a button that cannot work.
        """
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("q.status = ?")
            params.append(str(QueueStatus(status)))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = await self._id_rows(
            conn,
            "SELECT q.* FROM uw_confirm_queue q"  # noqa: S608 — code-owned literals, every value bound
            f"{where}"
            " ORDER BY q.score DESC, q.id LIMIT ? OFFSET ?",
            params,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            left = await self._chain(conn, _as_int(row["left_entity_id"]))
            right = await self._chain(conn, _as_int(row["right_entity_id"]))
            if left is None or right is None or left == right:
                continue
            if await self._id_kind(conn, left) != await self._id_kind(conn, right):
                continue
            try:
                evidence = json.loads(str(row["evidence_json"] or "[]"))
            except (TypeError, ValueError):
                evidence = []
            out.append(
                {
                    "id": int(row["id"]),
                    "status": str(row["status"]),
                    "score": float(row["score"] or 0.0),
                    "evidence": evidence if isinstance(evidence, list) else [],
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "decided_at": row["decided_at"],
                    "decided_by": row["decided_by"],
                    "left": await self._id_stub(conn, left),
                    "right": await self._id_stub(conn, right),
                }
            )
        return out

    async def _id_stub(self, conn: Any, entity_id: int) -> dict[str, Any]:
        row = await self._id_row(
            conn,
            "SELECT id, kind, display_name FROM uw_entities WHERE id = ?",
            (entity_id,),
        )
        if row is None:  # pragma: no cover — callers pass live ids
            return {"id": entity_id, "kind": "", "display_name": ""}
        return {
            "id": int(row["id"]),
            "kind": str(row["kind"]),
            "display_name": str(row["display_name"]),
        }

    async def list_merge_log(
        self,
        *,
        entity_id: int | None = None,
        limit: int = 100,
        include_undone: bool = True,
    ) -> list[dict[str, Any]]:
        """The audit trail — every merge, its evidence, and whether it stands."""
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        clauses: list[str] = []
        params: list[Any] = []
        if entity_id is not None:
            clauses.append("(winner_id = ? OR loser_id = ?)")
            params.extend([int(entity_id), int(entity_id)])
        if not include_undone:
            clauses.append("undone_at IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = await self._id_rows(
            conn,
            f"SELECT * FROM uw_merge_log{where} ORDER BY id DESC LIMIT ?",  # noqa: S608 — code-owned literals, every value bound
            params,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                evidence = json.loads(str(row["evidence_json"] or "[]"))
            except (TypeError, ValueError):
                evidence = []
            out.append(
                {
                    "id": int(row["id"]),
                    "winner_id": int(row["winner_id"]),
                    "loser_id": int(row["loser_id"]),
                    "tier": str(row["tier"]),
                    "reason": str(row["reason"] or ""),
                    "evidence": evidence if isinstance(evidence, list) else [],
                    "queue_id": _as_int(row["queue_id"]),
                    "merged_at": str(row["merged_at"]),
                    "undone_at": row["undone_at"],
                }
            )
        return out

    async def identity_counts(self) -> dict[str, int]:
        """Honest counters for the status surface (cheap, index-only)."""
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        entities = await self._id_row(
            conn,
            "SELECT COUNT(*) AS n FROM uw_entities WHERE merged_into IS NULL",
        )
        people = await self._id_row(
            conn,
            "SELECT COUNT(*) AS n FROM uw_entities"
            " WHERE merged_into IS NULL AND kind = ?",
            (str(EntityKind.PERSON),),
        )
        identifiers = await self._id_row(
            conn, "SELECT COUNT(*) AS n FROM uw_identifiers"
        )
        pending = await self._id_row(
            conn,
            "SELECT COUNT(*) AS n FROM uw_confirm_queue WHERE status = ?",
            (str(QueueStatus.PENDING),),
        )
        merges = await self._id_row(
            conn, "SELECT COUNT(*) AS n FROM uw_merge_log WHERE undone_at IS NULL"
        )
        return {
            "entities": int((entities or {"n": 0})["n"] or 0),
            "people": int((people or {"n": 0})["n"] or 0),
            "identifiers": int((identifiers or {"n": 0})["n"] or 0),
            "pending_confirmations": int((pending or {"n": 0})["n"] or 0),
            "merges": int((merges or {"n": 0})["n"] or 0),
        }


# ---------------------------------------------------------------------------
# Seeding from the Jarvis contacts store
# ---------------------------------------------------------------------------


async def seed_from_contacts(
    store: Any,
    *,
    contacts: Sequence[Any] | None = None,
    contact_store: Any = None,
) -> SeedReport:
    """Seed entities from the user's address book — lazy, idempotent, re-runnable.

    Day one already knows the user's actual circle (design doc 05). The pass
    is safe to repeat: a contact resolves through its own ``contact`` slug
    identifier, so a second run links instead of duplicating, and a contact
    whose e-mail or phone already appeared in the corpus MERGES with that
    entity deterministically rather than creating a twin.

    Reading the address book is blocking file I/O and the import is lazy
    (AP-26), so neither touches the boot path.
    """
    if contacts is None:
        import asyncio  # noqa: PLC0415 — lazy, keeps the module import cheap

        if contact_store is None:
            from jarvis.contacts.store import ContactStore  # noqa: PLC0415 — lazy

            contact_store = ContactStore()
        contacts = await asyncio.to_thread(contact_store.list_all)

    created = linked = identifiers_added = merged = queued = skipped = 0
    for contact in contacts or []:
        name = str(getattr(contact, "name", "") or "").strip()
        slug = str(getattr(contact, "slug", "") or "").strip()
        if not name and not slug:
            skipped += 1
            continue
        source_ref = f"contacts:{slug}" if slug else ""
        try:
            resolution = await store.resolve_identity(
                name=name,
                emails=list(getattr(contact, "emails", []) or []),
                phones=list(getattr(contact, "phones", []) or []),
                contact_slug=slug,
                kind=EntityKind.PERSON,
                source_ref=source_ref,
                create=True,
            )
        except IdentityError as exc:
            log.info("UltraWiki identity: skipping contact %r (%s)", slug or name, exc)
            skipped += 1
            continue
        if resolution.entity_id is None:
            skipped += 1
            queued += len(resolution.queued)
            continue
        created += 1 if resolution.created else 0
        linked += 0 if resolution.created else 1
        merged += len(resolution.merged)
        queued += len(resolution.queued)
        if source_ref:
            await store.set_entity_source_ref(resolution.entity_id, source_ref)
        for alias in getattr(contact, "aliases", []) or []:
            result = await store.add_identifier(
                resolution.entity_id,
                IdentifierKind.NAME,
                alias,
                source_ref=source_ref,
            )
            identifiers_added += 1 if result.created else 0
            merged += len(result.merged)
            queued += len(result.queued)
    return SeedReport(
        created=created,
        linked=linked,
        identifiers_added=identifiers_added,
        merged=merged,
        queued=queued,
        skipped=skipped,
    )
