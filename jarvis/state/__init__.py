"""Laufzeit-State fuer die Desktop-App (Supervisor, ChatStore).

Diese Objekte sind Prozess-weit eindeutig und werden vom `WebServer` ueber
`bind_*`-Setter an die FastAPI-State-Map angebunden.
"""
from __future__ import annotations

from .chat_store import ChatMessage, ChatStore
from .supervisor import Supervisor, SupervisorState

__all__ = ["Supervisor", "SupervisorState", "ChatStore", "ChatMessage"]
