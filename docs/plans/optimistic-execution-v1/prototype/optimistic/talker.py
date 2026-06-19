"""Talker — "Main Jarvis", the blitzschnelle front-of-house.

The Talker is the only thing on the (would-be) voice critical path. It:

1. Classifies the utterance with the pure, <150 ms ``router.classify`` (no I/O).
2. For SMALLTALK: replies directly — never wakes the worker.
3. For a DUMB tool: fires a local script in-process in milliseconds — never wakes
   the worker (AD-OE3).
4. For a SMART tool: emits the optimistic ACK **first** (AD-OE1), then publishes a
   ``MissionSpawn`` (the "silent context package") onto the bus and returns
   immediately. The Heavy-Duty Worker picks it up and runs it off-transcript
   (AD-OE2/AD-OE4) — the Talker never awaits the MCP round-trip.

When a background mission fails, the Oops protocol has already injected the
correction into the Talker's context. The Talker surfaces it organically at the
next VAD turn-boundary via :meth:`vad_turn_boundary` (AD-OE5).
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

    async def handle_utterance(self, text: str) -> str:
        """Process one user utterance and return the immediate spoken reply.

        Returns instantly for every route: the optimistic ACK for smart tasks,
        a short confirmation for dumb tasks, a direct answer for smalltalk.
        """
        utter = UserUtterance(text=text)
        await self._bus.publish(utter)
        self._transcript.append(text)

        route = classify(text)
        ack = ack_for(text, route)

        if route is RouteKind.SMALLTALK:
            await self._bus.publish(AckEmitted(text=ack, trace_id=utter.trace_id))
            return ack

        if route is RouteKind.DUMB_TOOL:
            tool_def = match_tool(text)
            name = tool_def.name if tool_def is not None else "local"
            dumb = tools_mod.get_dumb_tool(name)
            # In-process, instant. Critically: NO MissionSpawn -> worker stays asleep.
            await dumb.fire(text)
            await self._bus.publish(DumbToolFired(action=name, trace_id=utter.trace_id))
            await self._bus.publish(AckEmitted(text=ack, trace_id=utter.trace_id))
            return ack

        # --- RouteKind.SMART_TOOL ---------------------------------------------
        # AD-OE1: the optimistic ACK is emitted BEFORE the worker dispatch.
        await self._bus.publish(AckEmitted(text=ack, trace_id=utter.trace_id))

        tool_def = match_tool(text)
        tool_name = (
            tool_def.name
            if (tool_def is not None and tool_def.kind is RouteKind.SMART_TOOL)
            else None
        )
        # Publishing MissionSpawn returns instantly: the worker only schedules a
        # task in its handler (AD-OE2). The Talker never awaits the heavy work.
        await self._bus.publish(
            MissionSpawn(
                command=text,
                context=self._context_package(),
                tool_name=tool_name,
                trace_id=utter.trace_id,
            )
        )
        return ack

    def _context_package(self) -> dict:
        """The "silent context package" handed to the worker.

        Carries the recent transcript and the known contacts. ``contacts`` is
        intentionally empty by default, so the canonical "Schreib Max eine Mail"
        scenario exercises the Oops path; ``demo.py`` shows how supplying a
        contact lets the same mission complete successfully.
        """
        return {"transcript": list(self._transcript), "contacts": {}}

    def vad_turn_boundary(self) -> list[str]:
        """Silero-VAD end-of-turn signal: surface any pending organic corrections.

        Returns scrubbed, ready-to-speak German correction phrases (one per
        pending background failure) and clears the buffer. Empty list when there
        is nothing to correct.
        """
        return self._oops.vad_turn_boundary()

    def injected_context(self) -> list[str]:
        """Internal context lines for pending corrections (not spoken)."""
        return self._oops.injected_context()

    async def aclose(self) -> None:
        """Wait for all background missions to settle (test/demo convenience)."""
        await self._worker.drain()
