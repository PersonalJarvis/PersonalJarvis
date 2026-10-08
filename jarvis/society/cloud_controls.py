"""Explicit emergency control of every known hosted owner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


async def relay_kill_switch(runtime: Any, *, release: bool = False) -> list[str]:
    """Attempt each cloud owner once; return agents whose state is unconfirmed."""
    from .cloud_host import CloudHost, ownership_lock, placements_for

    host = CloudHost(runtime)
    rows = placements_for(runtime.store.path.parent)
    path = "/api/society/kill-switch" + ("/release" if release else "")

    async def send(row: dict[str, Any]) -> str | None:
        try:
            async with ownership_lock(runtime.store.path.parent, row["agent_id"]):
                current = host.placement(row["agent_id"])
                if current is None:
                    return None  # Preparation rolled back; the local stop already applies.
                status, result = await host.request(row["agent_id"], "POST", path, {})
            if status == 200 and result.get("engaged") is (not release):
                return None
        except Exception as exc:
            log.warning(
                "Cloud emergency control failed for %s (%s)", row["agent_id"], type(exc).__name__
            )
        return row["agent_id"]

    return [agent for agent in await asyncio.gather(*(send(row) for row in rows)) if agent]
