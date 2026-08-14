"""Pins: a running errand is visible on the {name}-Agents board.

Coding missions light the board up; before these subscriptions a real-world
errand — the feature the board exists to showcase — left no trace at all
(its tool calls were dropped for lack of a parent node). The registry now
translates the errand lifecycle events into the same jarvis_agent nodes the
departure board already renders, so no frontend change is needed.
"""

from __future__ import annotations

import pytest

from jarvis.agents.registry import JarvisAgentRegistry
from jarvis.core.bus import EventBus
from jarvis.core.events import ErrandCompleted, ErrandNeedsInput, ErrandUpdated


@pytest.fixture
def registry() -> JarvisAgentRegistry:
    return JarvisAgentRegistry(EventBus()).attach()


def _update(state: str, *, errand_id: str = "e-1", outcome: str = "") -> ErrandUpdated:
    return ErrandUpdated(
        errand_id=errand_id, goal="book me a flight", state=state, outcome=outcome
    )


@pytest.mark.asyncio
async def test_a_running_errand_appears_as_an_agent_node(
    registry: JarvisAgentRegistry,
) -> None:
    await registry._on_errand_updated(_update("running"))
    (node,) = registry.tree()
    assert node.kind == "jarvis_agent"  # rendered by the board without frontend changes
    assert node.status == "running"
    assert node.utterance == "book me a flight"
    assert node.trace_id == "errand-e-1"


@pytest.mark.asyncio
async def test_the_waiting_question_rides_on_the_node(
    registry: JarvisAgentRegistry,
) -> None:
    await registry._on_errand_updated(_update("running"))
    await registry._on_errand_updated(_update("needs_input"))
    await registry._on_errand_needs_input(
        ErrandNeedsInput(errand_id="e-1", questions="Which airport?\nWhich day?")
    )
    (node,) = registry.tree()
    assert node.status == "running"  # waiting is alive, not finished
    assert node.prompts == ["Which airport?", "Which day?"]

    # The user answered — the errand runs again and the question clears.
    await registry._on_errand_updated(_update("running"))
    assert registry.tree()[0].prompts == []


@pytest.mark.asyncio
async def test_terminal_states_map_and_the_first_ending_wins(
    registry: JarvisAgentRegistry,
) -> None:
    await registry._on_errand_updated(_update("running"))
    await registry._on_errand_updated(_update("stalled", outcome="the site blocks automation"))
    (node,) = registry.tree()
    assert node.status == "failed"
    assert node.error == "the site blocks automation"
    assert node.duration_ms is not None

    # A late second terminal write (cancel racing the loop) must not rewrite it.
    await registry._on_errand_updated(_update("cancelled"))
    assert registry.tree()[0].status == "failed"


@pytest.mark.asyncio
async def test_events_arrive_via_the_bus_subscription(registry: JarvisAgentRegistry) -> None:
    """The full path: publish on the bus the registry attached to."""
    bus = registry._bus
    await bus.publish(_update("running"))
    await bus.publish(_update("completed", outcome="reference XY123"))
    (node,) = registry.tree()
    assert node.status == "completed"


@pytest.mark.asyncio
async def test_an_unknown_future_state_leaves_the_node_untouched(
    registry: JarvisAgentRegistry,
) -> None:
    await registry._on_errand_updated(_update("running"))
    await registry._on_errand_updated(_update("some_future_state"))
    assert registry.tree()[0].status == "running"


@pytest.mark.asyncio
async def test_errand_completed_event_exists_for_automation() -> None:
    """ErrandCompleted stays a flat machine signal (When-Then rules) — the
    board listens to ErrandUpdated, so the two never fight."""
    event = ErrandCompleted(errand_id="e-1", status="completed", outcome="done")
    assert event.errand_id == "e-1"
