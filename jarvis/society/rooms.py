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

import json
from dataclasses import dataclass
from typing import Any, Final

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
    #: Members who have had their turn in the current round (spoke or passed).
    turned: list[str]
    #: Whether anybody spoke in the current round.
    spoke_this_round: bool

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
        )

    def _members_json(self) -> str:
        return json.dumps(
            {
                "members": self.members,
                "turned": self.turned,
                "spoke_this_round": self.spoke_this_round,
            }
        )


class Rooms:
    def __init__(self, store: SocietyStore) -> None:
        self._store = store

    async def open(
        self, *, opened_by: str, members: list[str], topic: str = "", room_id: str | None = None
    ) -> Room:
        unique = list(dict.fromkeys(m.strip() for m in members if m and m.strip()))
        if not MIN_MEMBERS <= len(unique) <= MAX_MEMBERS:
            raise RoomError(
                FailureReason.BLOCKED_BY_POLICY,
                f"a room has {MIN_MEMBERS}-{MAX_MEMBERS} members, not {len(unique)}",
            )
        rid = room_id or f"room-{now_ms():x}-{abs(hash(tuple(unique))) & 0xFFFF:04x}"
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
        )
        await self._store.insert_room(
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
            }
        )
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.ROOM_OPEN,
                from_agent=opened_by,
                to_agent=None,
                trace_id=trace_id,
                payload={
                    "room_id": rid,
                    "members": unique,
                    "text": topic,
                    "max_rounds": MAX_ROUNDS,
                    "max_messages": MAX_MESSAGES,
                },
            )
        )
        return room

    async def get(self, room_id: str) -> Room | None:
        row = await self._store.get_room_row(room_id)
        return Room.from_row(row) if row else None

    async def list(self, *, state: RoomState | None = None) -> list[Room]:
        rows = await self._store.list_room_rows(state=str(state) if state else None)
        return [Room.from_row(r) for r in rows]

    async def say(self, room_id: str, member: str, text: str, *, cost_usd: float = 0.0) -> Room:
        """``member`` speaks. Refused when it is not their turn or the room
        is not running; settles the room when a cap is reached."""
        room = await self._require_running(room_id)
        self._require_turn(room, member)
        text = (text or "").strip()[:_MAX_TEXT]
        if not text:
            return await self.pass_turn(room_id, member)
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.SAY,
                from_agent=member,
                to_agent=None,
                trace_id=room.trace_id,
                cost_usd=cost_usd,
                payload={"room_id": room.room_id, "round": room.round, "text": text},
            )
        )
        room.message_count += 1
        room.spoke_this_round = True
        room.turned.append(member)
        return await self._advance(room)

    async def pass_turn(self, room_id: str, member: str) -> Room:
        """``member`` stays silent this round (silence is allowed)."""
        room = await self._require_running(room_id)
        self._require_turn(room, member)
        room.turned.append(member)
        return await self._advance(room)

    async def settle(self, room_id: str, *, reason: str, by: str = "scheduler") -> Room:
        room = await self.get(room_id)
        if room is None:
            raise RoomError(FailureReason.TARGET_UNKNOWN, f"room {room_id!r} not found")
        if room.state in (RoomState.SETTLED, RoomState.FAILED):
            return room
        room.state = RoomState.SETTLED
        room.settle_reason = reason
        await self._persist(room)
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.ROOM_SETTLE,
                from_agent=by,
                to_agent=None,
                trace_id=room.trace_id,
                payload={
                    "room_id": room.room_id,
                    "reason": reason,
                    "rounds": room.round,
                    "messages": room.message_count,
                },
            )
        )
        return room

    async def fail(self, room_id: str, *, reason: str) -> Room:
        room = await self.get(room_id)
        if room is None:
            raise RoomError(FailureReason.TARGET_UNKNOWN, f"room {room_id!r} not found")
        room.state = RoomState.FAILED
        room.settle_reason = reason
        await self._persist(room)
        return room

    # ------------------------------------------------------------- policy

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

    async def _advance(self, room: Room) -> Room:
        if room.message_count >= MAX_MESSAGES:
            await self._persist(room)
            return await self.settle(room.room_id, reason="message_cap")
        if len(room.turned) >= len(room.members):
            # A full pass over the members ends the round.
            if not room.spoke_this_round:
                await self._persist(room)
                return await self.settle(room.room_id, reason="silence")
            if room.round >= MAX_ROUNDS:
                await self._persist(room)
                return await self.settle(room.room_id, reason="round_cap")
            room.round += 1
            room.turned = []
            room.spoke_this_round = False
        await self._persist(room)
        return room

    async def _persist(self, room: Room) -> None:
        await self._store.update_room(
            room.room_id,
            {
                "members_json": room._members_json(),
                "round": room.round,
                "message_count": room.message_count,
                "state": str(room.state),
                "settle_reason": room.settle_reason,
            },
        )
