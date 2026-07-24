"""Offline unit tests for the UltraWiki rerank backends.

HTTP rides ``httpx.MockTransport`` through the injectable ``transport``
parameter; secrets are monkeypatched at the rerank module's import site.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import jarvis.ultrawiki.rerank as rr
from jarvis.ultrawiki.rerank import (
    DEFAULT_RERANK_MODELS,
    RERANK_BACKENDS,
    CohereReranker,
    RerankError,
    VoyageReranker,
    available_rerankers,
    resolve_reranker,
)

FAKE_KEY = "unit-test-key-456"


@pytest.fixture
def no_secrets(monkeypatch):
    monkeypatch.setattr(rr, "get_secret", lambda key, env_fallback=None: None)


@pytest.fixture
def fake_secrets(monkeypatch):
    monkeypatch.setattr(rr, "get_secret", lambda key, env_fallback=None: FAKE_KEY)


def _json_handler(payload, captured, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=payload)

    return handler


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


# ----------------------------------------------------------------------
# Happy paths
# ----------------------------------------------------------------------


async def test_voyage_rerank_posts_payload_and_parses_index_score_pairs(fake_secrets):
    captured: list[httpx.Request] = []
    handler = _json_handler(
        {
            "data": [
                {"index": 2, "relevance_score": 0.91},
                {"index": 0, "relevance_score": 0.42},
            ]
        },
        captured,
    )
    backend = VoyageReranker(transport=httpx.MockTransport(handler))

    result = await backend.rerank("what broke?", ["a", "b", "c"], top_k=2)

    assert result == [(2, 0.91), (0, 0.42)]
    request = captured[0]
    assert str(request.url) == "https://api.voyageai.com/v1/rerank"
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert _body(request) == {
        "model": "rerank-2.5",
        "query": "what broke?",
        "documents": ["a", "b", "c"],
        "top_k": 2,
    }


async def test_cohere_rerank_posts_top_n_payload_and_parses_results(fake_secrets):
    captured: list[httpx.Request] = []
    handler = _json_handler(
        {"results": [{"index": 1, "relevance_score": 0.88}]}, captured
    )
    backend = CohereReranker(transport=httpx.MockTransport(handler))

    result = await backend.rerank("who is Alice?", ["x", "y"], top_k=1)

    assert result == [(1, 0.88)]
    request = captured[0]
    assert str(request.url) == "https://api.cohere.com/v2/rerank"
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert _body(request) == {
        "model": "rerank-v3.5",
        "query": "who is Alice?",
        "documents": ["x", "y"],
        "top_n": 1,
    }


async def test_rerank_empty_documents_short_circuits_without_http(fake_secrets):
    captured: list[httpx.Request] = []
    backend = VoyageReranker(
        transport=httpx.MockTransport(_json_handler({}, captured))
    )

    assert await backend.rerank("q", [], top_k=5) == []
    assert captured == []


# ----------------------------------------------------------------------
# Errors + readiness
# ----------------------------------------------------------------------


async def test_rerank_http_error_maps_to_rerank_error(fake_secrets):
    handler = _json_handler({"message": "rate limited"}, [], status=429)
    backend = VoyageReranker(transport=httpx.MockTransport(handler))
    with pytest.raises(RerankError, match="HTTP 429"):
        await backend.rerank("q", ["a"], top_k=1)


async def test_rerank_unexpected_shape_maps_to_rerank_error(fake_secrets):
    handler = _json_handler({"unexpected": []}, [])
    backend = CohereReranker(transport=httpx.MockTransport(handler))
    with pytest.raises(RerankError, match="response shape"):
        await backend.rerank("q", ["a"], top_k=1)


async def test_rerank_without_key_raises_rerank_error(no_secrets):
    backend = VoyageReranker(transport=httpx.MockTransport(_json_handler({}, [])))
    with pytest.raises(RerankError, match="no API key"):
        await backend.rerank("q", ["a"], top_k=1)


@pytest.mark.parametrize("backend_class", [VoyageReranker, CohereReranker])
def test_ready_without_key_is_false_with_honest_reason(no_secrets, backend_class):
    usable, reason = backend_class().ready()
    assert usable is False
    assert reason
    assert "API-Keys" in reason  # in-app recovery hint


@pytest.mark.parametrize("backend_class", [VoyageReranker, CohereReranker])
def test_ready_with_key_is_true(fake_secrets, backend_class):
    assert backend_class().ready() == (True, "")


# ----------------------------------------------------------------------
# Registry + honest skip
# ----------------------------------------------------------------------


def test_registry_and_default_models_cover_the_same_backends():
    assert set(RERANK_BACKENDS) == {"voyage", "cohere"}
    assert set(DEFAULT_RERANK_MODELS) == {"voyage", "cohere"}


def test_available_rerankers_without_keys_reports_all_not_ready(no_secrets):
    rows = available_rerankers(SimpleNamespace())

    assert [row["name"] for row in rows] == list(RERANK_BACKENDS)
    for row in rows:
        assert row["ready"] is False
        assert row["reason"]
        assert row["default_model"] == DEFAULT_RERANK_MODELS[row["name"]]


def test_resolve_reranker_unconfigured_returns_none_for_honest_skip(fake_secrets):
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(rerank_provider=""))
    assert resolve_reranker(cfg) is None


def test_resolve_reranker_unknown_provider_returns_none(fake_secrets):
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(rerank_provider="does-not-exist"))
    assert resolve_reranker(cfg) is None


def test_resolve_reranker_keyless_provider_returns_none(no_secrets):
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(rerank_provider="voyage"))
    assert resolve_reranker(cfg) is None


def test_resolve_reranker_ready_provider_returns_backend(fake_secrets):
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(rerank_provider="cohere"))
    backend = resolve_reranker(cfg)
    assert isinstance(backend, CohereReranker)
