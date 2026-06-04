"""Frontier-Routes — REST-API fuer Auto-Switch-Modal.

GET /api/frontier/pending → Liste der Switches die der User noch nicht
   mit OK quittiert hat. Frontend rendert ein blockierendes Modal.
POST /api/frontier/ack → Markiert alle pending Switches als geackt.
   Modal schliesst.
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
    """Liefert alle noch nicht-geackten Frontier-Switches.

    Frontend pollt das beim Mount + nach jedem WS-Reconnect, damit das
    Modal nicht verloren geht falls der User die Tab waehrend des
    Switches nicht offen hatte.
    """
    return get_pending_switches_as_dict()


@router.post("/ack")
async def ack_switches() -> dict[str, int]:
    """Markiert alle pending Switches als geackt (User hat OK gedrueckt).

    Returns ``{"acked": <count>}``.
    """
    count = ack_pending_switches()
    return {"acked": count}
