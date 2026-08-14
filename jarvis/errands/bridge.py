"""ErrandEventBridge — the errand runner's ``on_update`` seam, connected.

The runner was built with a deliberate callback hook ("for the UI and for
voice", runner.py) that nothing ever passed — so every errand finished into a
silent SQLite row and the opening promise "I will report back" was never kept.
This bridge is that missing listener: it translates each durable state change
into flat events on the global ``EventBus``, where the announcer, the agents
board, the session recorder and When-Then rules can all see it.

Mirrors ``jarvis/missions/task_bridge.py`` in role and in temperament: pure
translation, no speech of its own, and a failure here must never break a run —
the runner already swallows listener exceptions, this module just keeps its
own logic honest.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import ErrandCompleted, ErrandNeedsInput, ErrandUpdated
from jarvis.core.turn_language import DEFAULT_LOCALE

from .schema import TERMINAL_STATES, Errand, ErrandState

log = logging.getLogger(__name__)

ERRAND_EVENT_SOURCE_LAYER = "errands.bridge"


class ErrandEventBridge:
    """Callable ``on_update`` listener publishing errand events globally."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        #: Cancel has two writers (the cancel call and the loop's next leg
        #: boundary), so a terminal state can be persisted twice. The user
        #: must hear about an ending exactly once.
        self._terminal_sent: set[str] = set()

    async def __call__(self, errand: Errand) -> None:
        language = errand.language or DEFAULT_LOCALE
        trace_id = _trace(errand)
        await self._bus.publish(
            ErrandUpdated(
                trace_id=trace_id,
                source_layer=ERRAND_EVENT_SOURCE_LAYER,
                errand_id=errand.id,
                goal=errand.goal,
                state=str(errand.state),
                outcome=errand.outcome,
                steps_done=len(errand.steps),
                language=language,
            )
        )

        if errand.state is ErrandState.NEEDS_INPUT and errand.open_questions:
            await self._bus.publish(
                ErrandNeedsInput(
                    trace_id=trace_id,
                    source_layer=ERRAND_EVENT_SOURCE_LAYER,
                    errand_id=errand.id,
                    goal=errand.goal,
                    questions="\n".join(errand.open_questions),
                    # No steps yet = the opening clarification round, which the
                    # start_errand tool already surfaces inside the open turn.
                    # With steps it happened in the detached loop, where this
                    # event is the ONLY way it reaches anybody.
                    mid_run=bool(errand.steps),
                    language=language,
                )
            )
            return

        if errand.state in TERMINAL_STATES and errand.id not in self._terminal_sent:
            self._terminal_sent.add(errand.id)
            await self._bus.publish(
                ErrandCompleted(
                    trace_id=trace_id,
                    source_layer=ERRAND_EVENT_SOURCE_LAYER,
                    errand_id=errand.id,
                    goal=errand.goal,
                    status=str(errand.state),
                    outcome=errand.outcome,
                    evidence_count=len(errand.result_evidence),
                    language=language,
                )
            )


def _trace(errand: Errand) -> UUID:
    """Keep the correlation to the turn that gave the order, when it parses."""
    try:
        return UUID(errand.trace_id)
    except (ValueError, AttributeError, TypeError):
        return uuid4()


__all__ = ["ERRAND_EVENT_SOURCE_LAYER", "ErrandEventBridge"]
