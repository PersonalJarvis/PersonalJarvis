"""Voice-Session-Recording-Subsystem.

Schreibt jede Voice-Session (Wake -> Hangup) inkl. User-Transkripten,
Jarvis-Antworten, Tool-Calls und Latenzen in ``data/sessions.db``.
Wird in der Desktop-App unter dem "Transkription"-Tab angezeigt.

Bootstrapping in ``server.py::_init_sessions_stack()`` via
``bootstrap_sessions(bus, db_path)``.
"""
from .init import bootstrap_sessions, shutdown_sessions
from .recorder import SessionRecorder
from .store import SessionStore

__all__ = [
    "SessionRecorder",
    "SessionStore",
    "bootstrap_sessions",
    "shutdown_sessions",
]
