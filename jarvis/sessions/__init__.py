"""Voice-session recording subsystem.

Writes every voice session (wake -> hangup), including user transcripts,
Jarvis replies, tool calls, and latencies, into ``data/sessions.db``.
Shown in the desktop app under the "Transcription" tab.

Bootstrapped in ``server.py::_init_sessions_stack()`` via
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
