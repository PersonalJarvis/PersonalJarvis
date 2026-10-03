"""Bounded group discussions — the meeting pavilion, and the token brake.

A room is a deterministic policy over the board (MASTERPLAN §2.2): 2–6
members, at most 3 serial rounds, at most 10 messages, silence allowed. It
is never an open model loop: the policy decides whose turn it is and when
the room settles; models only fill in the words of a ``SAY``.

History lives in ``society_events`` (``ROOM_OPEN → SAY* → ROOM_SETTLE`` on
the room's ``trace_id``); state (members, round, counters, driver state)
lives in ``society_rooms``. A restart reconstructs a room from the row and
continues instead of orphaning it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Final
from uuid import uuid4
from weakref import WeakValueDictionary

from .events import MsgType, RoomState, SocietyEnvelope, now_ms
from .failure_reasons import FailureReason
from .store import SocietyStore

__all__ = ["MAX_MEMBERS", "MAX_MESSAGES", "MAX_ROUNDS", "MIN_MEMBERS", "Room", "RoomError", "Rooms"]

MIN_MEMBERS: Final[int] = 2
MAX_MEMBERS: Final[int] = 6
MAX_ROUNDS: Final[int] = 3
MAX_MESSAGES: Final[int] = 10
_MAX_TEXT: Final[int] = 8_000


class RoomError(ValueError):
    def __init__(self, reason: FailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True)
class Room:
    room_id: str
    trace_id: str
    opened_by: str
    topic: str
    members: list[str]
    round: int
    message_count: int
    state: RoomState
    settle_reason: str
    created_ms: int
    updated_ms: int
    turned: list[str]
    spoke_this_round: bool
    live: bool = False
    inflight_member: str = ""
    inflight_claim_id: str = ""
    inflight_turn_id: str = ""
    inflight_since_ms: int = 0

    @property
    def next_speaker(self) -> str | None:
        if self.state is not RoomState.RUNNING:
            return None
        for member in self.members:
            if member not in self.turned:
                return member
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "trace_id": self.trace_id,
            "opened_by": self.opened_by,
            "topic": self.topic,
            "members": list(self.members),
            "round": self.round,
            "max_rounds": MAX_ROUNDS,
            "message_count": self.message_count,
            "max_messages": MAX_MESSAGES,
            "state": str(self.state),
            "settle_reason": self.settle_reason,
            "next_speaker": self.next_speaker,
            "live": self.live,
            "inflight_member": self.inflight_member or None,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Room:
        members = json.loads(row["members_json"])
        extra = members if isinstance(members, dict) else {"members": members}
        return cls(
            room_id=str(row["room_id"]),
            trace_id=str(row["trace_id"]),
            opened_by=str(row["opened_by"]),
            topic=str(row.get("topic") or ""),
            members=list(extra.get("members", [])),
            round=int(row.get("round") or 0),
            message_count=int(row.get("message_count") or 0),
            state=RoomState(str(row.get("state") or "queued")),
            settle_reason=str(row.get("settle_reason") or ""),
            created_ms=int(row.get("created_ms") or 0),
            updated_ms=int(row.get("updated_ms") or 0),
            turned=list(extra.get("turned", [])),
            spoke_this_round=bool(extra.get("spoke_this_round", False)),
            live=bool(extra.get("live", False)),
            inflight_member=str(extra.get("inflight_member") or ""),
            inflight_claim_id=str(extra.get("inflight_claim_id") or ""),
            inflight_turn_id=str(extra.get("inflight_turn_id") or ""),
            inflight_since_ms=int(extra.get("inflight_since_ms") or 0),
        )

    def _members_json(self) -> str:
        return json.dumps(
            {
                "members": self.members,
                "turned": self.turned,
                "spoke_this_round": self.spoke_this_round,
                "live": self.live,
                "inflight_member": self.inflight_member,
                "inflight_claim_id": self.inflight_claim_id,
                "inflight_turn_id": self.inflight_turn_id,
                "inflight_since_ms": self.inflight_since_ms,
            }
        )


class Rooms:
    def __init__(self, store: SocietyStore) -> None:
        self._store = store
        self._turn_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def _turn_lock(self, room_id: str) -> asyncio.Lock:
        lock = self._turn_locks.get(room_id)
        if lock is None:
            lock = asyncio.Lock()
            self._turn_locks[room_id] = lock
        return lock

    async def open(
        self,
        *,
        opened_by: str,
        members: list[str],
        topic: str = "",
        room_id: str | None = None,
        live: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> Room:
        unique = list(dict.fromkeys(m.strip() for m in members if m and m.strip()))
        if not MIN_MEMBERS <= len(unique) <= MAX_MEMBERS:
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"a room has {MIN_MEMBERS}-{MAX_MEMBERS} members, not {len(unique)}",
            )
        rid = room_id or f"room-{uuid4().hex}"
        trace_id = f"room:{rid}"
        now = now_ms()
        room = Room(
            room_id=rid,
            trace_id=trace_id,
            opened_by=opened_by,
            topic=topic[:_MAX_TEXT],
            members=unique,
            round=1,
            message_count=0,
            state=RoomState.RUNNING,
            settle_reason="",
            created_ms=now,
            updated_ms=now,
            turned=[],
            spoke_this_round=False,
            live=live,
        )
        event_payload: dict[str, Any] = {
            "room_id": rid,
            "members": unique,
            "text": room.topic,
            "max_rounds": MAX_ROUNDS,
            "max_messages": MAX_MESSAGES,
            "live": live,
        }
        for key in ("reply_policy", "reply_surface", "reply_session_id", "lang"):
            value = str((metadata or {}).get(key) or "").strip()
            if value:
                event_payload[key] = value
        event = SocietyEnvelope(
            msg_type=MsgType.ROOM_OPEN,
            from_agent=opened_by,
            to_agent=None,
            trace_id=trace_id,
            payload=event_payload,
        )
        await self._store.insert_room_with_events(
            {
                "room_id": rid,
                "trace_id": trace_id,
                "opened_by": opened_by,
                "topic": room.topic,
                "members_json": room._members_json(),
                "round": 1,
                "message_count": 0,
                "state": str(RoomState.RUNNING),
                "settle_reason": "",
                "created_ms": now,
                "updated_ms": now,
            },
            (event,),
        )
        return room

    async def get(self, room_id: str) -> Room | None:
        row = await self._store.get_room_row(room_id)
        return Room.from_row(row) if row else None

    async def list(self, *, state: RoomState | None = None) -> list[Room]:
        rows = await self._store.list_room_rows(state=str(state) if state else None)
        return [Room.from_row(r) for r in rows]

    async def claim_turn(self, room_id: str, claim_id: str) -> Room:
        claim_id = claim_id.strip()
        if not claim_id:
            raise RoomError(FailureReason.BLOCKED_BY_POLICY, "room turn claim id is empty")
        async with self._turn_lock(room_id):
            for _ in range(2):
                room = await self._require_running(room_id)
                if room.inflight_claim_id:
                    if room.inflight_claim_id == claim_id:
                        return room
                    raise RoomError(
                        FailureReason.BLOCKED_BY_POLICY,
                        f"room {room_id!r} already has an in-flight turn",
                    )
                member = room.next_speaker
                if member is None:
                    raise RoomError(
                        FailureReason.BLOCKED_BY_POLICY,
                        f"room {room_id!r} has no speaker to claim",
                    )
                expected_updated_ms = room.updated_ms
                room.inflight_member = member
                room.inflight_claim_id = claim_id
                room.inflight_turn_id = ""
                room.inflight_since_ms = now_ms()
                if await self._persist(room, expected_updated_ms=expected_updated_ms):
                    return room
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"room {room_id!r} changed concurrently; retry",
            )

    async def bind_turn(self, room_id: str, claim_id: str, turn_id: str) -> Room:
        turn_id = turn_id.strip()
        if not turn_id:
            raise RoomError(FailureReason.BLOCKED_BY_POLICY, "agent-chat turn id is empty")
        async with self._turn_lock(room_id):
            for _ in range(2):
                room = await self._require_running(room_id)
                self._require_claim(room, claim_id)
                if room.inflight_turn_id:
                    if room.inflight_turn_id == turn_id:
                        return room
                    raise RoomError(
                        FailureReason.BLOCKED_BY_POLICY,
                        f"room {room_id!r} claim is already bound to another turn",
                    )
                expected_updated_ms = room.updated_ms
                room.inflight_turn_id = turn_id
                if await self._persist(room, expected_updated_ms=expected_updated_ms):
                    return room
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"room {room_id!r} changed concurrently; retry",
            )

    async def release_claim(self, room_id: str, claim_id: str) -> Room:
        async with self._turn_lock(room_id):
            for _ in range(2):
                room = await self._require_running(room_id)
                if not room.inflight_claim_id:
                    return room
                self._require_claim(room, claim_id)
                expected_updated_ms = room.updated_ms
                self._clear_claim(room)
                if await self._persist(room, expected_updated_ms=expected_updated_ms):
                    return room
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"room {room_id!r} changed concurrently; retry",
            )

    async def complete_claim(
        self,
        room_id: str,
        claim_id: str,
        text: str,
        *,
        cost_usd: float = 0.0,
    ) -> Room:
        return await self._take_turn(
            room_id,
            member=None,
            text=text,
            cost_usd=cost_usd,
            claim_id=claim_id,
        )

    async def settle_claim(
        self,
        room_id: str,
        claim_id: str,
        *,
        reason: str,
        by: str = "scheduler",
        cost_usd: float = 0.0,
    ) -> Room:
        async with self._turn_lock(room_id):
            return await self._terminalize_claim(
                room_id,
                claim_id,
                reason=reason,
                by=by,
                failed=False,
                cost_usd=cost_usd,
            )

    async def fail_claim(
        self,
        room_id: str,
        claim_id: str,
        *,
        reason: str,
        cost_usd: float = 0.0,
    ) -> Room:
        async with self._turn_lock(room_id):
            return await self._terminalize_claim(
                room_id,
                claim_id,
                reason=reason,
                by="scheduler",
                failed=True,
                cost_usd=cost_usd,
            )

    async def say(self, room_id: str, member: str, text: str, *, cost_usd: float = 0.0) -> Room:
        return await self._take_turn(
            room_id,
            member=member,
            text=text,
            cost_usd=cost_usd,
            claim_id=None,
        )

    async def pass_turn(self, room_id: str, member: str) -> Room:
        return await self._take_turn(
            room_id,
            member=member,
            text="",
            cost_usd=0.0,
            claim_id=None,
        )

    async def _take_turn(
        self,
        room_id: str,
        *,
        member: str | None,
        text: str,
        cost_usd: float,
        claim_id: str | None,
    ) -> Room:
        text = (text or "").strip()[:_MAX_TEXT]
        async with self._turn_lock(room_id):
            for _ in range(2):
                room = await self._require_running(room_id)
                if claim_id is None:
                    if room.inflight_claim_id:
                        raise RoomError(
                            FailureReason.BLOCKED_BY_POLICY,
                            f"room {room_id!r} has an in-flight turn",
                        )
                    speaker = member or ""
                else:
                    self._require_claim(room, claim_id)
                    speaker = room.inflight_member
                self._require_turn(room, speaker)
                expected_updated_ms = room.updated_ms
                if claim_id is not None:
                    self._clear_claim(room)
                events = self._apply_turn(room, speaker, text, cost_usd=cost_usd)
                if await self._persist(
                    room,
                    expected_updated_ms=expected_updated_ms,
                    events=events,
                ):
                    return room
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"room {room_id!r} changed concurrently; retry",
            )

    async def _terminalize_claim(
        self,
        room_id: str,
        claim_id: str,
        *,
        reason: str,
        by: str,
        failed: bool,
        cost_usd: float,
    ) -> Room:
        cost_usd = max(0.0, float(cost_usd))
        for _ in range(2):
            room = await self.get(room_id)
            if room is None:
                raise RoomError(FailureReason.TARGET_UNKNOWN, f"room {room_id!r} not found")
            if room.state in (RoomState.SETTLED, RoomState.FAILED):
                return room
            self._require_claim(room, claim_id)
            member = room.inflight_member
            expected_updated_ms = room.updated_ms
            events: list[SocietyEnvelope] = []
            if cost_usd > 0:
                events.append(self._turn_event(room, member, "", cost_usd=cost_usd))
            events.append(self._terminal_event(room, reason=reason, by=by, failed=failed))
            if await self._persist(
                room,
                expected_updated_ms=expected_updated_ms,
                events=events,
            ):
                return room
        latest = await self.get(room_id)
        if latest is not None and latest.state in (RoomState.SETTLED, RoomState.FAILED):
            return latest
        raise RoomError(
            FailureReason.BLOCKED_BY_POLICY,
            f"room {room_id!r} changed concurrently; retry",
        )

    async def settle(self, room_id: str, *, reason: str, by: str = "scheduler") -> Room:
        async with self._turn_lock(room_id):
            return await self._terminalize(room_id, reason=reason, by=by, failed=False)

    async def fail(self, room_id: str, *, reason: str) -> Room:
        async with self._turn_lock(room_id):
            return await self._terminalize(
                room_id,
                reason=reason,
                by="scheduler",
                failed=True,
            )

    async def _terminalize(
        self,
        room_id: str,
        *,
        reason: str,
        by: str,
        failed: bool,
    ) -> Room:
        for _ in range(2):
            room = await self.get(room_id)
            if room is None:
                raise RoomError(FailureReason.TARGET_UNKNOWN, f"room {room_id!r} not found")
            if room.state in (RoomState.SETTLED, RoomState.FAILED):
                return room
            expected_updated_ms = room.updated_ms
            event = self._terminal_event(room, reason=reason, by=by, failed=failed)
            if await self._persist(
                room,
                expected_updated_ms=expected_updated_ms,
                events=(event,),
            ):
                return room
        latest = await self.get(room_id)
        if latest is not None and latest.state in (RoomState.SETTLED, RoomState.FAILED):
            return latest
        raise RoomError(
            FailureReason.BLOCKED_BY_POLICY,
            f"room {room_id!r} changed concurrently; retry",
        )

    async def _require_running(self, room_id: str) -> Room:
        room = await self.get(room_id)
        if room is None:
            raise RoomError(FailureReason.TARGET_UNKNOWN, f"room {room_id!r} not found")
        if room.state is not RoomState.RUNNING:
            raise RoomError(FailureReason.BLOCKED_BY_POLICY, f"room {room_id!r} is {room.state}")
        return room

    @staticmethod
    def _require_turn(room: Room, member: str) -> None:
        if member not in room.members:
            raise RoomError(FailureReason.TARGET_UNKNOWN, f"{member!r} is not in the room")
        if room.next_speaker != member:
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"not {member!r}'s turn (next: {room.next_speaker!r})",
            )

    @staticmethod
    def _require_claim(room: Room, claim_id: str) -> None:
        if not claim_id or room.inflight_claim_id != claim_id:
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"room {room.room_id!r} turn claim does not match",
            )
        if not room.inflight_member:
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"room {room.room_id!r} turn claim has no owner",
            )

    @staticmethod
    def _clear_claim(room: Room) -> None:
        room.inflight_member = ""
        room.inflight_claim_id = ""
        room.inflight_turn_id = ""
        room.inflight_since_ms = 0

    def _apply_turn(
        self,
        room: Room,
        member: str,
        text: str,
        *,
        cost_usd: float,
    ) -> list[SocietyEnvelope]:
        events: list[SocietyEnvelope] = []
        if text or cost_usd > 0:
            events.append(self._turn_event(room, member, text, cost_usd=cost_usd))
        if text:
            room.message_count += 1
            room.spoke_this_round = True
        room.turned.append(member)
        terminal = self._advance(room)
        if terminal is not None:
            events.append(terminal)
        return events

    @staticmethod
    def _turn_event(
        room: Room,
        member: str,
        text: str,
        *,
        cost_usd: float,
    ) -> SocietyEnvelope:
        payload: dict[str, Any] = {
            "room_id": room.room_id,
            "round": room.round,
            "text": text,
        }
        if not text:
            payload["silent"] = True
        return SocietyEnvelope(
            msg_type=MsgType.SAY,
            from_agent=member,
            to_agent=None,
            trace_id=room.trace_id,
            cost_usd=cost_usd,
            payload=payload,
        )

    def _advance(self, room: Room) -> SocietyEnvelope | None:
        if room.message_count >= MAX_MESSAGES:
            return self._terminal_event(room, reason="message_cap")
        if len(room.turned) >= len(room.members):
            if not room.spoke_this_round:
                return self._terminal_event(room, reason="silence")
            if room.round >= MAX_ROUNDS:
                return self._terminal_event(room, reason="round_cap")
            room.round += 1
            room.turned = []
            room.spoke_this_round = False
        return None

    def _terminal_event(
        self,
        room: Room,
        *,
        reason: str,
        by: str = "scheduler",
        failed: bool = False,
    ) -> SocietyEnvelope:
        self._clear_claim(room)
        room.state = RoomState.FAILED if failed else RoomState.SETTLED
        room.settle_reason = reason
        payload: dict[str, Any] = {
            "room_id": room.room_id,
            "reason": reason,
            "rounds": room.round,
            "messages": room.message_count,
        }
        if failed:
            payload["failed"] = True
        return SocietyEnvelope(
            msg_type=MsgType.ROOM_SETTLE,
            from_agent=by,
            to_agent=None,
            trace_id=room.trace_id,
            payload=payload,
        )

    async def _persist(
        self,
        room: Room,
        *,
        expected_updated_ms: int,
        events: tuple[SocietyEnvelope, ...] | list[SocietyEnvelope] = (),
    ) -> bool:
        updated_ms = await self._store.transition_room(
            room.room_id,
            expected_updated_ms=expected_updated_ms,
            fields={
                "members_json": room._members_json(),
                "round": room.round,
                "message_count": room.message_count,
                "state": str(room.state),
                "settle_reason": room.settle_reason,
            },
            events=events,
        )
        if updated_ms is None:
            return False
        room.updated_ms = updated_ms
        return True
