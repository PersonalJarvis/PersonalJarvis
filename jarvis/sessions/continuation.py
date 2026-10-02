"""Which archived voice session the next call continues.

Every call used to open its own ``voice_sessions`` row. Reopening an old voice
chat from the history and talking on therefore spawned a second row ("Voice
chat · 16:59") beside the one the person had opened. When the front page
continues an archived voice chat (``PUT /api/agent-chat/voice-chat`` with a
``voice_session_id``), the recorder files the next calls into that row
instead (``SessionRecorder._on_session_started``) until another chat is put
on stage.

Process-local and in memory: a restart forgets it, and the next call simply
records a new session again.
"""

from __future__ import annotations

import threading

#: Prefix of a caption segment id recorded inside a continued row:
#: ``call:<call id>:<segment id>``. Each call's audio clock restarts at zero,
#: so the transcript replay orders calls by this before the clock.
CALL_SEGMENT_PREFIX = "call:"

_lock = threading.Lock()
_target: str | None = None


def continue_voice_session(session_id: str | None) -> None:
    """Record the next calls into ``session_id``; ``None`` = a new row per call."""
    global _target
    with _lock:
        _target = (session_id or "").strip() or None


def continued_voice_session() -> str | None:
    """The archived voice session the next call continues, if any."""
    with _lock:
        return _target


__all__ = ["CALL_SEGMENT_PREFIX", "continue_voice_session", "continued_voice_session"]
