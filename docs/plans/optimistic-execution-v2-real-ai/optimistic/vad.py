"""VADRegistry and HTTP VAD endpoints for the optimistic-execution prototype.

The client (web, mobile, or test) calls these endpoints to tell the server
when the user starts or stops speaking. The server uses this to decide WHEN
to surface background "Oops" corrections — never mid-utterance (AD-OE5).

Design principles:
- No PyAudio, no microphone, no OS-audio code. VAD is driven purely by HTTP.
- No imports of oops.py or sse.py — decoupled via an injected async callback.
- Standard library + fastapi only. No third-party audio dependencies.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# VAD state registry
# ---------------------------------------------------------------------------

class VADRegistry:
    """Tracks per-session speaking state.

    Thread safety: for the prototype, asyncio single-threaded execution is
    assumed. In production, wrap mutations in asyncio.Lock if needed.
    """

    def __init__(self) -> None:
        self._speaking: dict[str, bool] = {}

    def speech_started(self, session_id: str) -> None:
        """Mark ``session_id`` as currently producing speech."""
        self._speaking[session_id] = True

    def speech_ended(self, session_id: str) -> None:
        """Mark ``session_id`` as having finished a speech segment."""
        self._speaking[session_id] = False

    def is_speaking(self, session_id: str) -> bool:
        """Return True if ``session_id`` is currently speaking, False otherwise."""
        return self._speaking.get(session_id, False)


# ---------------------------------------------------------------------------
# Request body model
# ---------------------------------------------------------------------------

class _VADRequest(BaseModel):
    """Body for both VAD endpoints. session_id defaults to 'default'."""

    session_id: str = "default"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def build_vad_router(
    registry: VADRegistry,
    on_turn_boundary: Callable[[str], Awaitable[list[str]]],
) -> APIRouter:
    """Build and return an APIRouter with the two VAD endpoints.

    Args:
        registry: VADRegistry instance for tracking speaking state.
        on_turn_boundary: async callable ``(session_id: str) -> list[str]``.
            Called when speech ends; returns a list of correction phrases to
            include in the response. The orchestrator wires this to
            OopsProtocol.flush + SSE push. vad.py itself never imports
            oops.py or sse.py — it only calls this injected callback.

    Routes:
        POST /api/vad/speech_started
            Body: {"session_id": "..."}  (default "default")
            Marks the session as speaking.
            Returns: {"ok": True, "speaking": True}

        POST /api/vad/speech_ended
            Body: {"session_id": "..."}  (default "default")
            Marks the session as not speaking, then calls on_turn_boundary.
            Returns: {"ok": True, "speaking": False, "corrections": [...]}
    """
    router = APIRouter(prefix="/api/vad")

    @router.post("/speech_started")
    async def speech_started(req: _VADRequest) -> dict:
        registry.speech_started(req.session_id)
        return {"ok": True, "speaking": True}

    @router.post("/speech_ended")
    async def speech_ended(req: _VADRequest) -> dict:
        registry.speech_ended(req.session_id)
        corrections = await on_turn_boundary(req.session_id)
        return {"ok": True, "speaking": False, "corrections": corrections}

    return router
