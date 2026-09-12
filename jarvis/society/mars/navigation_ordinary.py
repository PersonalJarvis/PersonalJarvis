"""Read existing ordinary-agent authority without opening a task or chat turn."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.society.events import AgentState

from .navigation_models import TravelMode

if TYPE_CHECKING:
    from jarvis.society.runtime import SocietyRuntime


class OrdinaryNavigationAuthority:
    """Logical visits need an active roster identity and respect the kill switch.

    No provider, session, capability grant, task scheduler or external tool is
    involved. Task execution continues using its separate station authority.
    """

    def __init__(self, runtime: SocietyRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, agent_id: str, _destination: str, mode: TravelMode) -> bool:
        if mode is not TravelMode.PEDESTRIAN:
            return False
        agent = await self.runtime.roster.get(agent_id)
        return (
            agent is not None
            and agent.state is AgentState.ACTIVE
            and not await self.runtime.store.kill_switch()
        )
