"""Explicit recovery of one owned browser, independent of its command queue."""

from __future__ import annotations

import asyncio
from typing import Any

_RECOVERY_CLOSE_TIMEOUT_S = 20.0


async def restart_browser(live: Any, agent: Any) -> None:
    """Reap the old worker before reopening its saved profile for a viewer."""
    old = live.sessions.get(agent.agent_id)
    if old is None:
        raise ValueError("No browser session is available to restart")
    binding = getattr(old, "profile_binding", None)
    if binding is None or binding.kind != "managed":
        raise ValueError("Only a browser managed by Jarvis can be restarted here")

    # A stuck control command can hold control_lock indefinitely. Recovery
    # fences creation instead, then uses the existing bounded process cleanup.
    async with live.profile_start_lock:
        if live._closed:
            raise RuntimeError("The browser service is shutting down")
        if live.sessions.get(agent.agent_id) is old:
            async with live.locks.setdefault(agent.agent_id, asyncio.Lock()):
                live.stop_turn(agent.agent_id, old.active_trace)
                old.login_guard = True
                old.state.update(manual=True)
                idle = live.idle_tasks.pop(agent.agent_id, None)
                if idle is not None:
                    idle.cancel()
                old.publish({"kind": "disconnected"})
                closing = live._begin_close(old)
                cancelled = False
                while True:
                    try:
                        # Includes blocked stdin/drain, not just response waits.
                        async with asyncio.timeout(_RECOVERY_CLOSE_TIMEOUT_S):
                            await asyncio.shield(closing)
                        break
                    except asyncio.CancelledError:
                        if closing.cancelled():
                            raise
                        cancelled = True
                    except TimeoutError:
                        closing.cancel()
                        # LiveSession.close forces the owned process tree down
                        # in finally, including when its command pipe is stuck.
                        while not closing.done():
                            try:
                                await asyncio.shield(closing)
                            except asyncio.CancelledError:  # Remember cancellation while joining the shielded browser cleanup.
                                caller = asyncio.current_task()
                                if caller is not None and caller.cancelling():
                                    cancelled = True
                        if not closing.cancelled() and closing.exception() is not None:
                            raise RuntimeError("Browser cleanup failed; try again") from None
                        proc = getattr(old, "proc", None)
                        if (
                            not old.closed
                            or proc is None
                            or proc.returncode is None
                            or old.profile_lease is not None
                            or any(not task.done() for task in (*old.tasks, *old.readers))
                        ):
                            raise RuntimeError(
                                "Browser cleanup did not finish; try again"
                            ) from None
                        break
                if live.sessions.get(agent.agent_id) is old:
                    live.sessions.pop(agent.agent_id)
                if live._closing_tasks.get(agent.agent_id) is closing:
                    live._closing_tasks.pop(agent.agent_id)
                if cancelled:
                    raise asyncio.CancelledError
    # ensure owns startup cancellation and profile locking. A viewer restart
    # uses the normal visible/manual startup and never replays an agent task.
    await live.ensure(agent, window_view=True)
