"""Server-level Definition-of-Done test for Phase 2 (mock LLM, no network).

Proves: POST /api/utterance returns an INSTANT ack, and the LLM answer (here the
deterministic mock backend) is delivered ASYNCHRONOUSLY over the SSE stream.
The live counterpart against real Ollama is `demo_client.py`.

Uses the StreamingASGIClient pattern (httpx.ASGITransport buffers the whole body
and is incompatible with infinite SSE streams).
"""
from __future__ import annotations

import asyncio

import httpx
from proto_testkit import StreamingASGIClient, read_sse_events

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


def test_phase2_instant_ack_then_async_answer_over_sse() -> None:
    async def scenario() -> None:
        app = create_app(settings=_mock_settings())
        sclient = StreamingASGIClient(app)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            captured: dict = {}

            async def _post() -> None:
                captured["resp"] = await http.post(
                    "/api/utterance",
                    json={
                        "text": "Erstelle eine kurze Zusammenfassung der Quartalszahlen",
                        "session_id": "s1",
                    },
                )

            events = await read_sse_events(
                sclient, "s1", publish_cb=_post, until_event="answer", timeout=10.0
            )

            # Instant, synchronous ACK (Main Jarvis).
            r = captured["resp"]
            assert r.status_code == 200
            assert r.json()["ack"].strip(), "server must answer instantly with an ACK"

            # Async LLM answer arrived over SSE (Worker).
            answers = [e for e in events if e["event"] == "answer"]
            assert answers, f"no 'answer' SSE event; got {events}"
            assert "mock" in answers[0]["data"]["text"].lower()

    asyncio.run(scenario())


def test_phase2_health_reports_provider() -> None:
    async def scenario() -> None:
        app = create_app(settings=_mock_settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            r = await http.get("/api/health")
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert body["backend"] == "mock"

    asyncio.run(scenario())
