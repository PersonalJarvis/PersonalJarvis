"""Embedding-model discovery, driven fully offline through MockTransport.

The property that matters: the settings dropdown is NEVER empty and NEVER
wrong. Live where the provider lists its models, curated where it does not,
and curated-with-a-reason on every failure — because a picker showing nothing
sends the user straight back to typing a model id from memory, which is the
defect this module exists to end.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from jarvis.ultrawiki import embedding_models as em


@pytest.fixture
def cfg():
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(ollama_endpoint="http://localhost:11434")
    )


@pytest.fixture(autouse=True)
def no_secrets(monkeypatch):
    """No credential unless a test says so — never read the dev box's keyring."""
    monkeypatch.setattr("jarvis.ultrawiki.embedding_models.get_secret", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "jarvis.ultrawiki.embedding_models.get_secret_any", lambda *_a, **_k: None
    )


def _transport(routes: dict[str, tuple[int, object]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = routes.get(request.url.path, (404, {"error": "nope"}))
        return httpx.Response(status, content=json.dumps(payload).encode())

    return httpx.MockTransport(handler)


async def test_ollama_lists_what_is_actually_pulled(cfg):
    transport = _transport(
        {
            "/api/tags": (
                200,
                {"models": [{"name": "bge-m3:latest"}, {"name": "nomic-embed-text"}]},
            )
        }
    )
    result = await em.list_embedding_models("ollama", cfg, transport=transport)
    assert result.source == "live"
    assert [m.id for m in result.models] == ["bge-m3:latest", "nomic-embed-text"]


async def test_an_empty_ollama_says_what_to_pull(cfg):
    """"No models" must read as an instruction, not as an empty dropdown."""
    transport = _transport({"/api/tags": (200, {"models": []})})
    result = await em.list_embedding_models("ollama", cfg, transport=transport)
    assert result.source == "curated"
    assert "pull" in result.reason
    assert result.models  # the curated list still gives the user something


async def test_an_offline_ollama_falls_back_with_a_reason(cfg):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await em.list_embedding_models(
        "ollama", cfg, transport=httpx.MockTransport(handler)
    )
    assert result.source == "curated"
    assert "unreachable" in result.reason
    assert any(m.id == "qwen3-embedding:4b" for m in result.models)


async def test_gemini_keeps_only_models_that_serve_embeddings(cfg, monkeypatch):
    """Capability, not a name pattern (AP-21).

    Filtering by "does the id contain 'embed'" would drop a future embedding
    model named otherwise AND admit a chat model called `gemini-embedded-x`.
    The API states which methods each model serves; that is the gate.
    """
    monkeypatch.setattr(
        "jarvis.ultrawiki.embedding_models.get_secret_any", lambda *_a, **_k: "key"
    )
    transport = _transport(
        {
            "/v1beta/models": (
                200,
                {
                    "models": [
                        {
                            "name": "models/gemini-3.5-flash",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/gemini-embedding-001",
                            "displayName": "Gemini Embedding 001",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        }
    )
    result = await em.list_embedding_models("gemini", cfg, transport=transport)
    assert result.source == "live"
    assert [m.id for m in result.models] == ["gemini-embedding-001"]
    assert result.models[0].label == "Gemini Embedding 001"


async def test_a_keyless_cloud_provider_shows_the_curated_list(cfg):
    result = await em.list_embedding_models("openai", cfg, transport=_transport({}))
    assert result.source == "curated"
    assert "no API key" in result.reason
    assert any(m.id == "text-embedding-3-small" for m in result.models)


async def test_openai_narrows_the_full_model_list_to_embeddings(cfg, monkeypatch):
    monkeypatch.setattr(
        "jarvis.ultrawiki.embedding_models.get_secret", lambda *_a, **_k: "sk-test"
    )
    transport = _transport(
        {
            "/v1/models": (
                200,
                {
                    "data": [
                        {"id": "gpt-5.5"},
                        {"id": "text-embedding-3-small"},
                        {"id": "text-embedding-3-large"},
                    ]
                },
            )
        }
    )
    result = await em.list_embedding_models("openai", cfg, transport=transport)
    assert result.source == "live"
    assert [m.id for m in result.models] == [
        "text-embedding-3-small",
        "text-embedding-3-large",
    ]


async def test_voyage_has_no_listing_endpoint_and_says_so(cfg):
    """Curated is the honest answer, not a failure — the reason explains why."""
    result = await em.list_embedding_models("voyage", cfg, transport=_transport({}))
    assert result.source == "curated"
    assert "no model list" in result.reason
    assert any(m.id == "voyage-3.5" for m in result.models)


async def test_an_http_error_never_escapes_as_an_exception(cfg, monkeypatch):
    monkeypatch.setattr(
        "jarvis.ultrawiki.embedding_models.get_secret", lambda *_a, **_k: "sk-test"
    )
    transport = _transport({"/v1/models": (500, {"error": "boom"})})
    result = await em.list_embedding_models("openai", cfg, transport=transport)
    assert result.source == "curated"
    assert "500" in result.reason


async def test_an_unknown_provider_returns_empty_rather_than_guessing(cfg):
    result = await em.list_embedding_models("nonsense", cfg, transport=_transport({}))
    assert result.models == ()
    assert "unknown embedding provider" in result.reason


def test_every_backend_has_a_curated_list():
    """A backend with no fallback would show an empty dropdown when offline."""
    from jarvis.ultrawiki.embeddings import EMBEDDING_BACKENDS

    assert set(em.CURATED_EMBEDDING_MODELS) == set(EMBEDDING_BACKENDS)
    for provider, rows in em.CURATED_EMBEDDING_MODELS.items():
        assert rows, provider


def test_the_catalog_default_model_is_in_its_curated_list():
    """The card's placeholder must be a model the dropdown actually offers."""
    from jarvis.ultrawiki import provider_catalog

    for spec in provider_catalog.EMBEDDING_PROVIDERS:
        ids = {i for i, _label in em.CURATED_EMBEDDING_MODELS[spec.id]}
        assert spec.default_model in ids, spec.id
