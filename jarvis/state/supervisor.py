"""Supervisor state machine — emits `SystemStateChanged` on every switch.

Phase 1a: minimal — state fields + provider switch. Phase 4 extends this into
a real FSM with guards and multi-harness dispatch (Plan §9 Phase 2 / §17.6).
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from jarvis.core.events import BrainProviderSwitched, SystemStateChanged

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus


class SupervisorState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"
    PAUSED = "PAUSED"


class Supervisor:
    """Central state machine + current brain-provider name.

    The Supervisor is **not** a brain instance and **not** an orchestrator —
    it is only the single source of truth for UI state display and provider
    selection. Real brain dispatch lives in `BrainManager` (Phase 2).
    """

    def __init__(self, *, bus: EventBus, initial_provider: str = "mock") -> None:
        self._bus = bus
        self._state: SupervisorState = SupervisorState.IDLE
        self._active_provider = initial_provider

    @property
    def state(self) -> str:
        return self._state.value

    @property
    def active_provider(self) -> str:
        return self._active_provider

    async def set_state(self, new_state: str) -> None:
        """Attempts a state change. No-op if the state is unknown or unchanged."""
        try:
            target = SupervisorState(new_state)
        except ValueError:
            return
        if target == self._state:
            return
        previous = self._state
        self._state = target
        await self._bus.publish(
            SystemStateChanged(
                source_layer="supervisor",
                new_state=target.value,
                previous=previous.value,
            )
        )

    async def switch_provider(self, provider_name: str) -> None:
        previous = self._active_provider
        if previous == provider_name:
            return
        self._active_provider = provider_name
        await self._bus.publish(
            BrainProviderSwitched(
                source_layer="supervisor",
                from_provider=previous,
                to_provider=provider_name,
            )
        )
