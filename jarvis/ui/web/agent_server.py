"""Start durable agent services without waiting for a browser to open their pages."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


async def start_agents(state: Any) -> None:
    """Restore chats and deliveries after HTTP is ready; never call a model to warm it."""
    from jarvis.ui.web.agent_chat_routes import _service_from_state

    try:
        service = await asyncio.to_thread(_service_from_state, state)
        if service is None:
            raise RuntimeError("The agent chat service is unavailable.")
        # The route helper owns the singleton and its startup synchronization.
        runtime = getattr(state, "society", None) or state.society_factory()
        state.society = runtime
        await runtime.ensure_started()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Independent agent server could not restore agent services")
        state.agent_server_error = "Agent services could not start. Check the server log."
        return
    state.agent_server_ready = True
    log.info("Independent agent server restored chats, routines and delivery")
