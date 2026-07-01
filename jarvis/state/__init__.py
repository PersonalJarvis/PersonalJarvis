"""Runtime state for the desktop app (Supervisor, ChatStore).

These objects are unique per process and are wired into the FastAPI state map
by the `WebServer` via `bind_*` setters.
"""
from __future__ import annotations

from .chat_store import ChatMessage, ChatStore
from .supervisor import Supervisor, SupervisorState

__all__ = ["Supervisor", "SupervisorState", "ChatStore", "ChatMessage"]
