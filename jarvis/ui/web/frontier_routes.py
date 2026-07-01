"""Frontier routes — REST API for the auto-switch modal.

GET /api/frontier/pending → the list of switches the user has not yet
   acknowledged with OK. The frontend renders a blocking modal.
POST /api/frontier/ack → marks all pending switches as acknowledged.
   The modal closes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.brain.frontier_autoswitch import (
    ack_pending_switches,
    get_pending_switches_as_dict,
)

router = APIRouter(prefix="/api/frontier", tags=["frontier"])


@router.get("/pending")
async def list_pending_switches() -> list[dict[str, Any]]:
    """Returns all frontier switches not yet acknowledged.

    The frontend polls this on mount + after every WS reconnect, so the
    modal isn't missed if the user didn't have the tab open during the
    switch.
    """
    return get_pending_switches_as_dict()


@router.post("/ack")
async def ack_switches() -> dict[str, int]:
    """Marks all pending switches as acknowledged (the user pressed OK).

    Returns ``{"acked": <count>}``.
    """
    count = ack_pending_switches()
    return {"acked": count}
