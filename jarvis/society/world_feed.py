"""Speech on the board, projected onto the island.

The society bus carries every envelope. The island only cares about the six
that are somebody TALKING -- ``SAY | QUERY | ANSWER | PROPOSE`` and the two
room brackets. Everything else already reaches the world through the
checkpoint engine, and pushing it twice would tell the same story in two
voices.

This is a projection, not a second truth (MASTERPLAN 2.7): it derives nothing,
stores nothing and can refuse nothing. A failure here loses one animation and
never touches the append path (AP-18) -- and it is logged rather than
swallowed (AP-30).

What crosses the wire is a PREVIEW. ``MessageAgentTool`` allows 8 000
characters; a bubble over a figure's head holds about four short lines, every
window keeps the last 500 events in memory, and a body is already readable in
the receiver's chat and behind ``GET /api/society/events``. So the world gets
``_BUBBLE_CHARS`` characters and a flag saying there is more.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final

from .events import MsgType, SocietyEnvelope

log = logging.getLogger(__name__)

__all__ = ["ROOM_TYPES", "VISIBLE_TYPES", "WorldFeed", "preview"]

#: A line one agent addressed to another; the island draws a bubble for each.
VISIBLE_TYPES: Final[frozenset[MsgType]] = frozenset(
    {MsgType.SAY, MsgType.QUERY, MsgType.ANSWER, MsgType.PROPOSE}
)
#: The brackets around a bounded room (rooms.py).
ROOM_TYPES: Final[frozenset[MsgType]] = frozenset({MsgType.ROOM_OPEN, MsgType.ROOM_SETTLE})

#: How much of a message reaches the island. Four short lines in a bubble at
#: the default zoom; past that nobody reads the map, they read the feed.
_BUBBLE_CHARS: Final[int] = 180
#: A room's topic sits on a banner over the Town Hall -- one line, not four.
_TOPIC_CHARS: Final[int] = 80


def preview(text: str, limit: int = _BUBBLE_CHARS) -> tuple[str, int, bool]:
    """Bubble-sized ``text``: ``(preview, original_length, truncated)``.

    Whitespace is collapsed so a Markdown block does not become a twenty-line
    bubble, and the cut lands on a word boundary so no word is sliced in half.
    """
    original = len(text)
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat, original, False
    cut = flat[:limit]
    space = cut.rfind(" ")
    # A single word longer than the limit has no boundary to cut on; take it
    # whole rather than returning an ellipsis and nothing else.
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…", original, True


class WorldFeed:
    """The society bus subscriber that puts speech on the app bus.

    Same shape as :class:`jarvis.society.quests.Quests` -- ``attach`` /
    ``detach`` around one ``subscribe_all``, and a guarded publish that falls
    back to the process default bus when the app did not inject one.
    """

    def __init__(self, runtime: Any, *, publish: Callable[[Any], Any] | None = None) -> None:
        self._runtime = runtime
        self._publish = publish
        self._unsubscribe: Callable[[], None] | None = None

    # ------------------------------------------------------------ wiring

    def attach(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self._runtime.store.bus.subscribe_all(self._on_envelope)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    # ------------------------------------------------------------ the feed

    async def _on_envelope(self, env: SocietyEnvelope) -> None:
        if env.msg_type in VISIBLE_TYPES:
            await self._announce(self._message_event(env))
        elif env.msg_type in ROOM_TYPES:
            await self._announce(self._room_event(env))

    @staticmethod
    def _room_of(env: SocietyEnvelope) -> str:
        room = env.payload.get("room_id")
        if isinstance(room, str) and room:
            return room
        if env.trace_id.startswith("room:"):
            return env.trace_id[len("room:") :]
        return ""

    def _message_event(self, env: SocietyEnvelope) -> Any:
        from jarvis.core.events import SocietyMessageSent

        text, chars, truncated = preview(env.text)
        room_round = env.payload.get("round")
        return SocietyMessageSent(
            source_layer="society",
            event_id=env.event_id,
            seq=env.seq or 0,
            msg_type=str(env.msg_type),
            from_agent=env.from_agent,
            to_agent=env.to_agent or "",
            room_id=self._room_of(env),
            room_round=room_round if isinstance(room_round, int) else 0,
            society_trace=env.trace_id,
            text=text,
            text_chars=chars,
            truncated=truncated,
        )

    def _room_event(self, env: SocietyEnvelope) -> Any:
        from jarvis.core.events import SocietyRoomChanged

        payload = env.payload
        opening = env.msg_type is MsgType.ROOM_OPEN
        members = payload.get("members")
        topic, _, _ = preview(env.text, _TOPIC_CHARS) if opening else ("", 0, False)
        return SocietyRoomChanged(
            source_layer="society",
            room_id=self._room_of(env),
            phase="open" if opening else "settle",
            opened_by=env.from_agent,
            members=tuple(str(m) for m in members) if isinstance(members, list) else (),
            topic=topic,
            reason=str(payload.get("reason") or ""),
            rounds=int(payload.get("rounds") or 0),
            messages=int(payload.get("messages") or 0),
            max_rounds=int(payload.get("max_rounds") or 0),
            max_messages=int(payload.get("max_messages") or 0),
            society_trace=env.trace_id,
        )

    async def _announce(self, event: Any) -> None:
        try:
            if self._publish is not None:
                maybe = self._publish(event)
            else:
                from jarvis.core.bus import get_default_bus

                maybe = get_default_bus().publish(event)
            if hasattr(maybe, "__await__"):
                await maybe
        except Exception:  # noqa: BLE001 — a missed push costs one animation, never an append
            log.debug("society world feed: speech not pushed", exc_info=True)
