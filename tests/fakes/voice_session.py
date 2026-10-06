"""Put a test ``SpeechPipeline`` inside an open classic voice conversation.

The classic TTS voice speaks an announcement only while the user is in an
open voice session (``SpeechPipeline._classic_voice_session_open``). Tests
that exercise that playback path open a session with this helper; tests
that prove the "no voice out of nowhere" rule leave it closed.
"""
from __future__ import annotations

from typing import Any


def open_classic_voice_session(pipeline: Any, *, session_id: str = "test-session") -> Any:
    """Mark ``pipeline`` as being inside an open classic voice session."""
    from jarvis.speech.pipeline import TurnTakingState

    pipeline._current_voice_session_id = session_id
    if getattr(pipeline, "_turn_state", TurnTakingState.IDLE) is TurnTakingState.IDLE:
        pipeline._turn_state = TurnTakingState.LISTENING
    return pipeline
