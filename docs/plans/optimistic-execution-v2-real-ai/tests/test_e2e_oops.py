"""End-to-end "Oops" protocol test over HTTP (v2, mock LLM, no network).

The spec's canonical scenario, now through the full server:
User: "Schreib Max eine Mail ..." -> the gmail mission finds no address for Max
-> the worker emits an INVISIBLE WorkerCorrectionNeeded (NOT streamed) -> it is
buffered per session -> when the VAD endpoint signals the turn boundary, an
organic, scrubbed German correction surfaces in the response (and over SSE).
"""
from __future__ import annotations

import asyncio

import httpx

from optimistic.config import LLMSettings
from optimistic.server import create_app


def _mock_settings() -> LLMSettings:
    return LLMSettings(
        backend="mock",
        base_url="",
        model="mock-model",
        api_key=None,
        timeout=5.0,
        system_prompt=None,
    )


def test_missing_email_surfaces_organic_correction_at_turn_boundary() -> None:
    async def scenario() -> None:
        app = create_app(settings=_mock_settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            # The user is talking.
            await http.post("/api/vad/speech_started", json={"session_id": "s1"})

            # Optimistic ACK for a gmail task with no known contact.
            r = await http.post(
                "/api/utterance",
                json={
                    "text": "Schreib Max eine Mail, dass sich das Projekt verschiebt",  # i18n-allow: test content — user voice utterance DE
                    "session_id": "s1",
                },
            )
            assert r.status_code == 200 and r.json()["ack"].strip()

            # Let the background worker discover the missing info.
            await app.state.worker.drain()

            # The correction is buffered but INVISIBLE (not spoken mid-utterance).
            assert app.state.oops.pending("s1"), "correction must be buffered invisibly"

            # Turn boundary -> organic, scrubbed correction surfaces.
            resp = await http.post("/api/vad/speech_ended", json={"session_id": "s1"})
            corrections = resp.json()["corrections"]
            assert len(corrections) == 1
            phrase = corrections[0].lower()
            assert "max" in phrase, "correction should name the missing recipient"
            assert "gmail" not in phrase and "`" not in phrase, "must be scrubbed for voice"
            assert app.state.oops.pending("s1") == [], "buffer cleared after the turn boundary"

    asyncio.run(scenario())
