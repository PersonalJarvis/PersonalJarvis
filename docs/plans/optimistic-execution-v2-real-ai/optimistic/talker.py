"""Talker — "Main Jarvis", the blitzschnelle front-of-house (v2, session-aware).

Same optimistic contract as v1 (AD-OE1: ACK before dispatch; AD-OE3: dumb tools
never wake the worker; AD-OE2: the Talker never awaits the heavy work), now
threading a ``session_id`` through every event so the SSE hub can route results
to the right network client.

The VAD turn-boundary flush of "Oops" corrections is handled by the server
(`/api/vad/speech_ended` -> ``OopsProtocol.flush`` -> SSE push), so the Talker no
longer owns that step — it just keeps the conversation moving instantly.
"""
from __future__ import annotations

import logging

from optimistic import tools as tools_mod
from optimistic.events import (
    AckEmitted,
    DumbToolFired,
    MissionSpawn,
    RouteKind,
    UserUtterance,
)
from optimistic.registry import match_tool
from optimistic.router import ack_for, classify

_log = logging.getLogger("optimistic.talker")


class Talker:
    """Optimistic front-end orchestrator wiring router + bus + tools + worker + oops."""

    def __init__(self, bus, *, worker, oops) -> None:
        self._bus = bus
        self._worker = worker
        self._oops = oops
        self._transcript: list[str] = []

    async def handle_utterance(self, text: str, session_id: str = "default") -> str:
        """Process one utterance for ``session_id`` and return the instant reply."""
        utter = UserUtterance(text=text, session_id=session_id)
        await self._bus.publish(utter)
        self._transcript.append(text)

        route = classify(text)
        ack = ack_for(text, route)

        if route is RouteKind.SMALLTALK:
            await self._bus.publish(
                AckEmitted(text=ack, trace_id=utter.trace_id, session_id=session_id)
            )
            return ack

        if route is RouteKind.DUMB_TOOL:
            tool_def = match_tool(text)
            name = tool_def.name if tool_def is not None else "local"
            dumb = tools_mod.get_dumb_tool(name)
            await dumb.fire(text)  # in-process, instant; NO MissionSpawn -> worker stays asleep
            await self._bus.publish(
                DumbToolFired(action=name, trace_id=utter.trace_id, session_id=session_id)
            )
            await self._bus.publish(
                AckEmitted(text=ack, trace_id=utter.trace_id, session_id=session_id)
            )
            return ack

        # --- RouteKind.SMART_TOOL: AD-OE1 — ACK first, then dispatch -----------
        await self._bus.publish(
            AckEmitted(text=ack, trace_id=utter.trace_id, session_id=session_id)
        )
        tool_def = match_tool(text)
        tool_name = (
            tool_def.name
            if (tool_def is not None and tool_def.kind is RouteKind.SMART_TOOL)
            else None
        )
        await self._bus.publish(
            MissionSpawn(
                command=text,
                context=self._context_package(),
                tool_name=tool_name,
                trace_id=utter.trace_id,
                session_id=session_id,
            )
        )
        return ack

    def _context_package(self) -> dict:
        """The "silent context package" handed to the worker (transcript + contacts).

        ``contacts`` is empty so the canonical "Schreib Max eine Mail" scenario
        exercises the Oops path; a real deployment would populate it.
        """
        return {"transcript": list(self._transcript), "contacts": {}}

    async def aclose(self) -> None:
        """Wait for all background missions to settle (test/demo convenience)."""
        await self._worker.drain()
