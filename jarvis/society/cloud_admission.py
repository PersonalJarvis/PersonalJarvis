"""Fence desktop execution after an agent's durable cloud handoff begins."""

from __future__ import annotations

from typing import Any


def assert_local_owner(runtime: Any, agent_id: str) -> None:
    """Fail closed while a transfer is prepared, active, or being reconciled."""
    from .cloud_host import placement_for

    placement = placement_for(runtime.store.path.parent, agent_id)
    if placement is not None:
        raise PermissionError(
            "This agent is hosted in the cloud or is being transferred. "
            "Continue through its cloud connection; local execution is disabled."
        )
