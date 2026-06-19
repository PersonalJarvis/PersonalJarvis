"""Unit tests for VADRegistry and build_vad_router (vad.py).

TDD — written first, run RED, then implemented, run GREEN.
HTTP endpoints tested in-process via httpx.ASGITransport — no real network.
Uses asyncio.run() — no pytest-asyncio needed.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from optimistic.vad import VADRegistry, build_vad_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_app(registry: VADRegistry, on_turn_boundary) -> FastAPI:
    """Assemble a minimal FastAPI app with the VAD router."""
    app = FastAPI()
    app.include_router(build_vad_router(registry, on_turn_boundary))
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# VADRegistry unit tests (no HTTP, no async needed)
# ---------------------------------------------------------------------------

def test_registry_initial_state_not_speaking():
    """A fresh VADRegistry reports is_speaking False for any session."""
    reg = VADRegistry()
    assert reg.is_speaking("s1") is False
    assert reg.is_speaking("default") is False


def test_registry_speech_started_sets_flag():
    reg = VADRegistry()
    reg.speech_started("s1")
    assert reg.is_speaking("s1") is True


def test_registry_speech_ended_clears_flag():
    reg = VADRegistry()
    reg.speech_started("s1")
    reg.speech_ended("s1")
    assert reg.is_speaking("s1") is False


def test_registry_sessions_isolated():
    """speech_started on s1 must not affect s2."""
    reg = VADRegistry()
    reg.speech_started("s1")
    assert reg.is_speaking("s1") is True
    assert reg.is_speaking("s2") is False


def test_registry_speech_ended_idempotent_when_not_started():
    """speech_ended on a session that never started is a no-op (no exception)."""
    reg = VADRegistry()
    reg.speech_ended("never-started")
    assert reg.is_speaking("never-started") is False


# ---------------------------------------------------------------------------
# HTTP endpoint tests via ASGITransport
# ---------------------------------------------------------------------------

def test_speech_started_endpoint_returns_ok_and_speaking_true():
    """POST /api/vad/speech_started -> 200, ok=True, speaking=True."""

    async def scenario():
        reg = VADRegistry()
        calls = []

        async def fake_cb(session_id: str) -> list[str]:
            calls.append(session_id)
            return ["Korrektur X"]

        app = _build_app(reg, fake_cb)
        async with _client(app) as client:
            resp = await client.post(
                "/api/vad/speech_started",
                json={"session_id": "s1"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["speaking"] is True

        # Registry updated
        assert reg.is_speaking("s1") is True

        # Callback NOT called on speech_started
        assert calls == [], f"on_turn_boundary must NOT be called on speech_started; got: {calls}"

    _run(scenario())


def test_speech_started_default_session_id():
    """When session_id is omitted, the endpoint uses 'default'."""

    async def scenario():
        reg = VADRegistry()

        async def fake_cb(session_id: str) -> list[str]:
            return []

        app = _build_app(reg, fake_cb)
        async with _client(app) as client:
            resp = await client.post(
                "/api/vad/speech_started",
                json={},  # no session_id
            )

        assert resp.status_code == 200
        assert reg.is_speaking("default") is True

    _run(scenario())


def test_speech_ended_endpoint_calls_callback_and_returns_corrections():
    """POST /api/vad/speech_ended -> calls on_turn_boundary, returns corrections."""

    async def scenario():
        reg = VADRegistry()
        reg.speech_started("s1")  # set up speaking state first

        called_with = []

        async def fake_cb(session_id: str) -> list[str]:
            called_with.append(session_id)
            return ["Korrektur X"]

        app = _build_app(reg, fake_cb)
        async with _client(app) as client:
            resp = await client.post(
                "/api/vad/speech_ended",
                json={"session_id": "s1"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["speaking"] is False
        assert body["corrections"] == ["Korrektur X"]

        # Registry updated
        assert reg.is_speaking("s1") is False

        # Callback called exactly once with the right session_id
        assert called_with == ["s1"], f"callback called with wrong args: {called_with}"

    _run(scenario())


def test_speech_ended_callback_called_exactly_once():
    """on_turn_boundary is called exactly once per speech_ended request."""

    async def scenario():
        reg = VADRegistry()
        call_count = [0]

        async def fake_cb(session_id: str) -> list[str]:
            call_count[0] += 1
            return []

        app = _build_app(reg, fake_cb)
        async with _client(app) as client:
            await client.post("/api/vad/speech_ended", json={"session_id": "s1"})

        assert call_count[0] == 1

    _run(scenario())


def test_speech_ended_without_prior_started_still_works():
    """speech_ended works even if speech_started was never called (no crash)."""

    async def scenario():
        reg = VADRegistry()

        async def fake_cb(session_id: str) -> list[str]:
            return ["correction"]

        app = _build_app(reg, fake_cb)
        async with _client(app) as client:
            resp = await client.post(
                "/api/vad/speech_ended",
                json={"session_id": "s99"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["speaking"] is False

    _run(scenario())


def test_speech_ended_returns_empty_corrections_when_callback_returns_empty():
    """When on_turn_boundary returns [], corrections in response is []."""

    async def scenario():
        reg = VADRegistry()

        async def fake_cb(session_id: str) -> list[str]:
            return []

        app = _build_app(reg, fake_cb)
        async with _client(app) as client:
            resp = await client.post(
                "/api/vad/speech_ended",
                json={"session_id": "s1"},
            )

        assert resp.status_code == 200
        assert resp.json()["corrections"] == []

    _run(scenario())


def test_speech_ended_default_session_id():
    """When session_id is omitted from speech_ended, 'default' is used."""

    async def scenario():
        reg = VADRegistry()
        called_with = []

        async def fake_cb(session_id: str) -> list[str]:
            called_with.append(session_id)
            return []

        app = _build_app(reg, fake_cb)
        async with _client(app) as client:
            resp = await client.post("/api/vad/speech_ended", json={})

        assert resp.status_code == 200
        assert called_with == ["default"]

    _run(scenario())


# ---------------------------------------------------------------------------
# Spec scenario (exact scenario from prompt)
# ---------------------------------------------------------------------------

def test_spec_scenario_full_flow():
    """
    Exact scenario from the sub-agent spec:
    build app with build_vad_router(VADRegistry(), fake_cb)
    where fake_cb records the session_id and returns ['Korrektur X'].

    POST /api/vad/speech_started {"session_id":"s1"}
    -> 200, is_speaking("s1") True, fake_cb NOT called.

    POST /api/vad/speech_ended {"session_id":"s1"}
    -> 200, response corrections == ['Korrektur X'], fake_cb called exactly once with 's1'.
    """

    async def scenario():
        reg = VADRegistry()
        calls: list[str] = []

        async def fake_cb(session_id: str) -> list[str]:
            calls.append(session_id)
            return ["Korrektur X"]

        app = _build_app(reg, fake_cb)

        async with _client(app) as client:
            # Step 1: speech_started
            resp1 = await client.post(
                "/api/vad/speech_started",
                json={"session_id": "s1"},
            )
            assert resp1.status_code == 200
            assert reg.is_speaking("s1") is True
            assert calls == [], "fake_cb must NOT be called on speech_started"

            # Step 2: speech_ended
            resp2 = await client.post(
                "/api/vad/speech_ended",
                json={"session_id": "s1"},
            )
            assert resp2.status_code == 200
            body = resp2.json()
            assert body["corrections"] == ["Korrektur X"]
            assert calls == ["s1"], f"fake_cb must be called exactly once with 's1'; got: {calls}"

    _run(scenario())
