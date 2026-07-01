"""Mock brain for phase 1a.

Simulates a real brain provider without an API call: scripted + echo-style
responses with artificial latency + state transitions (IDLE → THINKING →
SPEAKING → IDLE) so that the UI pulse animation becomes visible.

The real BrainManager (phase 2) implements the same `respond` API and
replaces this mock without any changes to the routes.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from jarvis.core.events import ResponseGenerated

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.state.chat_store import ChatStore
    from jarvis.state.supervisor import Supervisor


_NO_BRAIN_REPLY_DE = (
    "(Kein Brain-Provider an die Chat-UI angebunden. "  # i18n-allow
    "Nutze die Voice-Pipeline oder hinterlege einen API-Key in den Einstellungen.)"  # i18n-allow
)


class MockBrain:
    """Scripted brain stand-in.

    - Typing simulation: 120-300 ms initial latency (THINKING state)
    - Response: short placeholder phrase OR echo
    - State sequence: IDLE → THINKING → SPEAKING → IDLE
    """

    name = "mock"

    def __init__(
        self, *, bus: EventBus, supervisor: Supervisor | None = None
    ) -> None:
        self._bus = bus
        self._supervisor = supervisor

    def bind_supervisor(self, supervisor: Supervisor) -> None:
        self._supervisor = supervisor

    async def respond(
        self,
        *,
        thread_id: str,
        text: str,
        store: ChatStore | None = None,
    ) -> str:
        """Answers a user message. Returns the assistant message ID."""
        # 1. Write user message to the store (idempotent — routes_ws already does it)
        if store is not None:
            await store.add_message(thread_id=thread_id, role="user", text=text)

        # 2. State → THINKING (transition)
        if self._supervisor is not None:
            await self._supervisor.set_state("THINKING")

        await asyncio.sleep(0.15)

        # 3. Generate response — NO scripted reply (user request 2026-04-25:
        #    no standard phrases). Echo stays for tests; otherwise an honest
        #    message that no real brain is connected.
        if text.strip().lower().startswith("echo "):
            reply = text[5:].strip() or "(leer)"
        else:
            reply = _NO_BRAIN_REPLY_DE

        # 4. State → SPEAKING (transition)
        if self._supervisor is not None:
            await self._supervisor.set_state("SPEAKING")

        # 5. Write response to store + publish event
        msg = None
        if store is not None:
            msg = await store.add_message(
                thread_id=thread_id, role="assistant", text=reply
            )
        await self._bus.publish(
            ResponseGenerated(source_layer="brain:mock", text=reply, language="de")
        )

        await asyncio.sleep(0.15)

        # 6. Return to IDLE
        if self._supervisor is not None:
            await self._supervisor.set_state("IDLE")

        return msg.message_id if msg is not None else ""
