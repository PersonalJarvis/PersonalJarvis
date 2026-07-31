"""REST API for the voice orb — start or end a conversation without speaking.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET  /api/voice/state   → is a speech pipeline running, and what it is doing.
    POST /api/voice/call    → arm a wake-style voice session (the click-shaped
                              wake word — same path as the call hotkey).
    POST /api/voice/hangup  → hard-stop the voice channel (same contract as the
                              hangup hotkey).

The click path is deliberately the SAME one the call hotkey and the chats
"Speak in this conversation" button take — ``SpeechPipeline.request_voice_session``
— so a click on the orb can never behave differently from the wake word beyond
skipping the audio trigger. Under the CLI-first contract these routes also make
the orb's actions scriptable as ``jarvis api voice <op>``.

No Brain dependency; on a headless install (no microphone, voice disabled) the
state endpoint answers honestly and the actions 503 instead of 500-ing.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


def _pipeline() -> Any:
    """The live SpeechPipeline, or ``None`` (headless / voice disabled)."""
    try:
        from jarvis.core.runtime_refs import get_speech_pipeline

        return get_speech_pipeline()
    except Exception:  # noqa: BLE001 — a missing runtime ref is "no pipeline"
        return None


@router.get("/state")
async def voice_state() -> dict[str, Any]:
    """Whether voice is available here, and what the pipeline is doing now."""
    pipeline = _pipeline()
    if pipeline is None:
        return {"available": False, "state": "unavailable"}
    state = getattr(getattr(pipeline, "_state", None), "name", "unknown")
    return {"available": True, "state": str(state).lower()}


@router.post("/call")
async def voice_call() -> dict[str, Any]:
    """Start a voice conversation — functionally the wake word, by click.

    ``armed`` is ``False`` when the pipeline refused (a session already runs,
    or activation is blocked) — that is an answer, not an error.
    """
    pipeline = _pipeline()
    if pipeline is None or not hasattr(pipeline, "request_voice_session"):
        raise HTTPException(status_code=503, detail="voice-pipeline-unavailable")
    return {"armed": bool(pipeline.request_voice_session())}


@router.post("/hangup")
async def voice_hangup() -> dict[str, Any]:
    """End the running voice conversation — the hangup key's contract."""
    pipeline = _pipeline()
    if pipeline is None or not hasattr(pipeline, "request_voice_hangup"):
        raise HTTPException(status_code=503, detail="voice-pipeline-unavailable")
    return {"stopped": bool(pipeline.request_voice_hangup())}
