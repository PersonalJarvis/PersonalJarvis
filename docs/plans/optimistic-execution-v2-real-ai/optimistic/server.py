"""FastAPI server — wires the optimistic-execution pieces into a real service.

Endpoints:
    POST /api/utterance          -> instant ACK (Main Jarvis) in the HTTP response
    GET  /api/stream             -> SSE stream (async worker answers + corrections)
    POST /api/vad/speech_started -> mark the session as speaking
    POST /api/vad/speech_ended   -> turn boundary: flush Oops corrections to SSE
    GET  /api/health             -> provider/model info

The in-process EventBus remains the internal backbone (AD-OE2, no broker). SSE is
only the client transport. The LLM provider is fully configured via .env
(config.py) — local Ollama or any OpenAI-compatible cloud endpoint.

Run:
    python -m optimistic.server          # uvicorn on JARVIS_HOST:JARVIS_PORT
    uvicorn optimistic.server:app        # equivalent
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from pydantic import BaseModel

from optimistic.bus import EventBus
from optimistic.config import LLMSettings, load_settings
from optimistic.oops import OopsProtocol
from optimistic.sse import SSEHub, build_sse_router
from optimistic.talker import Talker
from optimistic.vad import VADRegistry, build_vad_router
from optimistic.worker import HeavyDutyWorker

_log = logging.getLogger("optimistic.server")


class _UtteranceRequest(BaseModel):
    text: str
    session_id: str = "default"


def create_app(settings: LLMSettings | None = None) -> FastAPI:
    """Build a fully wired FastAPI app. Pass mock ``settings`` in tests."""
    settings = settings or load_settings()

    bus = EventBus()
    hub = SSEHub(bus)
    worker = HeavyDutyWorker(bus, settings)
    oops = OopsProtocol(bus)
    vad_registry = VADRegistry()
    talker = Talker(bus, worker=worker, oops=oops)

    async def on_turn_boundary(session_id: str) -> list[str]:
        """VAD turn boundary: flush this session's invisible corrections and push
        each as an organic 'correction' SSE event (AD-OE5)."""
        phrases = oops.flush(session_id)
        for phrase in phrases:
            await hub.push(session_id, "correction", {"text": phrase})
        return phrases

    app = FastAPI(title="Optimistic Execution v2", version="2.0.0")
    app.include_router(build_sse_router(hub))
    app.include_router(build_vad_router(vad_registry, on_turn_boundary))

    @app.post("/api/utterance")
    async def utterance(req: _UtteranceRequest) -> dict:
        """Main Jarvis: classify + emit the instant optimistic ACK. The heavy work
        runs in the background worker and is delivered later over the SSE stream."""
        ack = await talker.handle_utterance(req.text, session_id=req.session_id)
        return {"ack": ack, "session_id": req.session_id}

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "backend": settings.backend,
            "model": settings.model,
            "base_url": settings.base_url,
        }

    # Expose internals for tests / debugging.
    app.state.bus = bus
    app.state.hub = hub
    app.state.worker = worker
    app.state.oops = oops
    app.state.vad = vad_registry
    app.state.talker = talker
    app.state.settings = settings
    return app


# Module-level app for `uvicorn optimistic.server:app`.
app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("JARVIS_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_PORT", "8008"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
    _log.info("Optimistic Execution v2 server on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
