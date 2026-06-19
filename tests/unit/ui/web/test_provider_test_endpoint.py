"""Tests for POST /api/providers/{id}/test and its anti-drift status parity.

The endpoint runs a REAL connectivity probe; here we monkeypatch the probe seam
so the route's own contract (spec lookup, serialization, 404 on unknown id) is
tested hermetically. The parity test guards the five-layer enum invariant: the
Pydantic Literal must mirror the Python single-source-of-truth exactly.
"""
from __future__ import annotations

from typing import get_args

import pytest
from fastapi.testclient import TestClient

from jarvis.brain.provider_test import PROVIDER_TEST_STATUSES, ProviderTestResult
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


def test_response_status_literal_matches_ssot() -> None:
    from jarvis.ui.web.provider_routes import ProviderTestStatusLiteral

    assert set(get_args(ProviderTestStatusLiteral)) == set(PROVIDER_TEST_STATUSES)


@pytest.fixture
def server() -> WebServer:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    return srv


def test_test_endpoint_serializes_result(server: WebServer, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(spec, cfg, **kwargs):
        return ProviderTestResult(
            provider=spec.id, status="bad_key", detail="invalid x-api-key", latency_ms=12.0,
        )

    monkeypatch.setattr("jarvis.brain.provider_test.run_provider_test", fake_run)

    with TestClient(server.app) as client:
        resp = client.post("/api/providers/claude-api/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "claude-api"
        assert body["status"] == "bad_key"
        assert body["detail"] == "invalid x-api-key"
        assert body["latency_ms"] == pytest.approx(12.0)
        # The UI needs to know a bad_key still means the integration is sound.
        assert body["integration_ok"] is True


def test_test_endpoint_unknown_provider_is_404(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/does-not-exist/test")
        assert resp.status_code == 404


def test_test_endpoint_unreachable_marks_integration_not_ok(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(spec, cfg, **kwargs):
        return ProviderTestResult(provider=spec.id, status="unreachable", detail="timeout")

    monkeypatch.setattr("jarvis.brain.provider_test.run_provider_test", fake_run)

    with TestClient(server.app) as client:
        body = client.post("/api/providers/gemini/test").json()
        assert body["status"] == "unreachable"
        assert body["integration_ok"] is False
