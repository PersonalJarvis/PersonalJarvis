"""Web-Backend für die Desktop-UI (Phase 1a).

Exponiert FastAPI + WebSocket auf 127.0.0.1 für die pywebview-Shell und
optional für Mobile-Companion-Clients (Phase 8+). Das Token-Auth schließt
den Endpunkt gegen neugierige Nachbarn auf localhost (Browser-Extensions,
andere Dev-Server) ab — Bind-Adresse allein reicht nicht.
"""
from __future__ import annotations

from .schema import (
    WSCommand,
    WSEventEnvelope,
    WSMessageIn,
    WSWelcome,
    event_to_ws_envelope,
)
from .server import WebServer

__all__ = [
    "WebServer",
    "WSEventEnvelope",
    "WSMessageIn",
    "WSCommand",
    "WSWelcome",
    "event_to_ws_envelope",
]
