"""Offline unit tests for the UltraWiki embedding backends.

All HTTP goes through ``httpx.MockTransport`` injected via each adapter's
``transport`` parameter; secrets are monkeypatched at the module's import
site. No network, no credentials, no SDKs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import jarvis.ultrawiki.embeddings as emb
from jarvis.ultrawiki.embeddings import (
    DEFAULT_MODELS,
    EMBEDDING_BACKENDS,
    CohereEmbedding,
    EmbeddingError,
    GeminiEmbedding,
    MistralEmbedding,
    OllamaEmbedding,
    OpenAIEmbedding,
    VoyageEmbedding,
    available_backends,
)
from jarvis.ultrawiki.types import EmbeddingBackend

FAKE_KEY = "unit-test-key-123"


@pytest.fixture
def no_secrets(monkeypatch):
    monkeypatch.setattr(emb, "get_secret", lambda key, env_fallback=None: None)
    monkeypatch.setattr(emb, "get_secret_any", lambda candidates: None)


@pytest.fixture
def fake_secrets(monkeypatch):
    monkeypatch.setattr(emb, "get_secret", lambda key, env_fallback=None: FAKE_KEY)
    monkeypatch.setattr(emb, "get_secret_any", lambda candidates: FAKE_KEY)


def _json_handler(payload, captured, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=payload)

    return handler


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


# ----------------------------------------------------------------------
# Protocol conformance + registry shape
# ----------------------------------------------------------------------


def test_every_backend_satisfies_the_frozen_protocol():
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(ollama_endpoint="http://x:1"))
    for name, factory in EMBEDDING_BACKENDS.items():
        backend = factory(cfg)
        assert isinstance(backend, EmbeddingBackend)
        assert backend.name == name


def test_registry_and_default_models_cover_the_same_six_backends():
    expected = {"ollama", "gemini", "openai", "voyage", "mistral", "cohere"}
    assert set(EMBEDDING_BACKENDS) == expected
    assert set(DEFAULT_MODELS) == expected
    assert all(DEFAULT_MODELS[name] for name in expected)


# ----------------------------------------------------------------------
# Ollama
# ----------------------------------------------------------------------


async def test_ollama_embed_posts_batch_and_parses_vectors():
    captured: list[httpx.Request] = []
    handler = _json_handler({"embeddings": [[0.1, 0.2], [0.3, 0.4]]}, captured)
    backend = OllamaEmbedding(
        endpoint="http://ollama.test:11434", transport=httpx.MockTransport(handler)
    )

    vectors = await backend.embed(["alpha", "beta"], model="bge-m3")

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert str(captured[0].url) == "http://ollama.test:11434/api/embed"
    assert _body(captured[0]) == {"model": "bge-m3", "input": ["alpha", "beta"]}


def test_ollama_ready_probes_the_version_endpoint():
    captured: list[httpx.Request] = []
    handler = _json_handler({"version": "0.5.0"}, captured)
    backend = OllamaEmbedding(
        endpoint="http://ollama.test:11434", transport=httpx.MockTransport(handler)
    )

    assert backend.ready() == (True, "")
    assert str(captured[0].url) == "http://ollama.test:11434/api/version"


def test_ollama_ready_unreachable_endpoint_is_false_with_honest_reason():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    backend = OllamaEmbedding(
        endpoint="http://ollama.test:11434", transport=httpx.MockTransport(refuse)
    )

    usable, reason = backend.ready()
    assert usable is False
    assert "http://ollama.test:11434" in reason
    assert reason  # honest English message, never empty on failure


async def test_ollama_embed_maps_transport_error_to_embedding_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    backend = OllamaEmbedding(
        endpoint="http://ollama.test:11434", transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(EmbeddingError):
        await backend.embed(["alpha"], model="bge-m3")


async def test_ollama_embed_vector_count_mismatch_raises():
    handler = _json_handler({"embeddings": [[0.1]]}, [])
    backend = OllamaEmbedding(transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError):
        await backend.embed(["alpha", "beta"], model="bge-m3")


async def test_embed_chunks_input_at_96_texts_per_call():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        count = len(_body(request)["input"])
        return httpx.Response(200, json={"embeddings": [[1.0]] * count})

    backend = OllamaEmbedding(transport=httpx.MockTransport(handler))
    vectors = await backend.embed([f"text {i}" for i in range(100)], model="bge-m3")

    assert len(vectors) == 100
    assert len(captured) == 2
    assert len(_body(captured[0])["input"]) == 96
    assert len(_body(captured[1])["input"]) == 4


async def test_embed_empty_input_returns_empty_without_http():
    captured: list[httpx.Request] = []
    handler = _json_handler({"embeddings": []}, captured)
    backend = OllamaEmbedding(transport=httpx.MockTransport(handler))

    assert await backend.embed([], model="bge-m3") == []
    assert captured == []


# ----------------------------------------------------------------------
# Gemini
# ----------------------------------------------------------------------


async def test_gemini_embed_uses_batch_endpoint_header_key_and_parses(fake_secrets):
    captured: list[httpx.Request] = []
    handler = _json_handler(
        {"embeddings": [{"values": [1.0, 2.0]}, {"values": [3.0, 4.0]}]}, captured
    )
    backend = GeminiEmbedding(transport=httpx.MockTransport(handler))

    vectors = await backend.embed(["one", "two"], model="gemini-embedding-001")

    assert vectors == [[1.0, 2.0], [3.0, 4.0]]
    request = captured[0]
    assert str(request.url).endswith("/v1beta/models/gemini-embedding-001:batchEmbedContents")
    assert request.headers["x-goog-api-key"] == FAKE_KEY
    body = _body(request)
    assert body["requests"][0]["model"] == "models/gemini-embedding-001"
    assert body["requests"][0]["content"]["parts"] == [{"text": "one"}]
    assert body["requests"][1]["content"]["parts"] == [{"text": "two"}]


def test_gemini_ready_without_key_is_false_with_honest_reason(no_secrets):
    usable, reason = GeminiEmbedding().ready()
    assert usable is False
    assert "gemini_api_key" in reason


@pytest.fixture
def vertex_routed_key(monkeypatch):
    """A fake AQ. key whose route verdict is pre-cached as Vertex — offline.

    The verdict is produced by the REAL resolver logic against a mock
    transport (AI Studio rejects, Vertex countTokens accepts), then served
    from the process-wide cache inside ``embed()`` — no network anywhere.
    """
    from jarvis.core import google_genai as gg

    key = "AQ.unit-vertex-key"
    monkeypatch.setattr(emb, "get_secret", lambda k, env_fallback=None: key)
    monkeypatch.setattr(emb, "get_secret_any", lambda candidates: key)
    gg.reset_route_cache()

    def probe(request: httpx.Request) -> httpx.Response:
        if request.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(401, json={})
        return httpx.Response(200, json={})

    assert gg.resolve_google_key_route(key, transport=httpx.MockTransport(probe)) == "vertex"
    yield key
    gg.reset_route_cache()


async def test_gemini_embed_routes_vertex_keys_through_predict(vertex_routed_key):
    captured: list[httpx.Request] = []
    handler = _json_handler({"predictions": [{"embeddings": {"values": [1.0, 2.0]}}]}, captured)
    backend = GeminiEmbedding(transport=httpx.MockTransport(handler))

    vectors = await backend.embed(["one", "two"], model="gemini-embedding-001")

    assert vectors == [[1.0, 2.0], [1.0, 2.0]]
    # One instance per request: Vertex serves gemini-embedding-001 singly.
    assert len(captured) == 2
    assert str(captured[0].url).endswith(
        "/v1beta1/publishers/google/models/gemini-embedding-001:predict"
    )
    assert captured[0].headers["x-goog-api-key"] == vertex_routed_key
    assert _body(captured[0]) == {"instances": [{"content": "one"}]}
    assert _body(captured[1]) == {"instances": [{"content": "two"}]}


async def test_gemini_vertex_rejection_names_the_way_out(vertex_routed_key):
    handler = _json_handler({"error": {"status": "PERMISSION_DENIED"}}, [], status=403)
    backend = GeminiEmbedding(transport=httpx.MockTransport(handler))

    with pytest.raises(EmbeddingError) as exc_info:
        await backend.embed(["one"], model="gemini-embedding-001")

    # Express embeddings are undocumented territory — a rejection must tell
    # the user what to do (switch to an AI Studio key), not just an HTTP code.
    assert "AI Studio key" in str(exc_info.value)


# ----------------------------------------------------------------------
# OpenAI-shaped backends (openai, voyage, mistral)
# ----------------------------------------------------------------------


async def test_openai_embed_posts_bearer_payload_and_restores_index_order(fake_secrets):
    captured: list[httpx.Request] = []
    handler = _json_handler(
        {
            "data": [
                {"index": 1, "embedding": [3.0]},
                {"index": 0, "embedding": [1.0]},
            ]
        },
        captured,
    )
    backend = OpenAIEmbedding(transport=httpx.MockTransport(handler))

    vectors = await backend.embed(["a", "b"], model="text-embedding-3-small")

    assert vectors == [[1.0], [3.0]]  # re-sorted by index
    request = captured[0]
    assert str(request.url) == "https://api.openai.com/v1/embeddings"
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert _body(request) == {"model": "text-embedding-3-small", "input": ["a", "b"]}


async def test_voyage_embed_targets_voyage_url(fake_secrets):
    captured: list[httpx.Request] = []
    handler = _json_handler({"data": [{"index": 0, "embedding": [0.5]}]}, captured)
    backend = VoyageEmbedding(transport=httpx.MockTransport(handler))

    vectors = await backend.embed(["a"], model="voyage-3.5")

    assert vectors == [[0.5]]
    assert str(captured[0].url) == "https://api.voyageai.com/v1/embeddings"
    assert captured[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert _body(captured[0])["model"] == "voyage-3.5"


async def test_mistral_embed_targets_mistral_url(fake_secrets):
    captured: list[httpx.Request] = []
    handler = _json_handler({"data": [{"index": 0, "embedding": [0.7]}]}, captured)
    backend = MistralEmbedding(transport=httpx.MockTransport(handler))

    vectors = await backend.embed(["a"], model="mistral-embed")

    assert vectors == [[0.7]]
    assert str(captured[0].url) == "https://api.mistral.ai/v1/embeddings"
    assert _body(captured[0])["model"] == "mistral-embed"


async def test_openai_http_error_maps_to_embedding_error(fake_secrets):
    handler = _json_handler({"error": {"message": "server exploded"}}, [], status=500)
    backend = OpenAIEmbedding(transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="HTTP 500"):
        await backend.embed(["a"], model="text-embedding-3-small")


async def test_embed_without_key_raises_embedding_error(no_secrets):
    backend = OpenAIEmbedding(transport=httpx.MockTransport(_json_handler({}, [])))
    with pytest.raises(EmbeddingError, match="no API key"):
        await backend.embed(["a"], model="text-embedding-3-small")


# ----------------------------------------------------------------------
# Cohere
# ----------------------------------------------------------------------


async def test_cohere_embed_requests_float_type_and_parses(fake_secrets):
    captured: list[httpx.Request] = []
    handler = _json_handler({"embeddings": {"float": [[0.9, 0.8]]}}, captured)
    backend = CohereEmbedding(transport=httpx.MockTransport(handler))

    vectors = await backend.embed(["a"], model="embed-v4.0")

    assert vectors == [[0.9, 0.8]]
    request = captured[0]
    assert str(request.url) == "https://api.cohere.com/v2/embed"
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = _body(request)
    assert body["texts"] == ["a"]
    assert body["embedding_types"] == ["float"]
    assert body["input_type"] == "search_document"


async def test_cohere_unexpected_shape_maps_to_embedding_error(fake_secrets):
    handler = _json_handler({"embeddings": {}}, [])
    backend = CohereEmbedding(transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="response shape"):
        await backend.embed(["a"], model="embed-v4.0")


# ----------------------------------------------------------------------
# ready() without keys + available_backends
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "backend_class",
    [GeminiEmbedding, OpenAIEmbedding, VoyageEmbedding, MistralEmbedding, CohereEmbedding],
)
def test_keyed_backend_ready_without_key_is_false_and_honest(no_secrets, backend_class):
    usable, reason = backend_class().ready()
    assert usable is False
    assert reason  # never an empty reason on a not-ready verdict
    # The reason must name the HEADLESS recovery path — the environment
    # variable — because a server install has no card to type into. The
    # desktop path is not spelled out here on purpose: the key field sits
    # directly below this line, so repeating its raw snake_case slot name is
    # noise. (Asserted by env var rather than by the name of a UI screen: that
    # literal went stale the moment the field moved onto the provider's card.)
    backend = backend_class()
    slot = getattr(backend, "_SECRET_SLOT", "")
    if slot:
        assert slot.upper() in reason
        assert "add one below" in reason


@pytest.mark.parametrize(
    "backend_class",
    [GeminiEmbedding, OpenAIEmbedding, VoyageEmbedding, MistralEmbedding, CohereEmbedding],
)
def test_keyed_backend_ready_with_key_is_true(fake_secrets, backend_class):
    assert backend_class().ready() == (True, "")


def test_available_backends_reports_all_six_with_honest_reasons(no_secrets):
    # Port 9 (discard) on loopback refuses instantly on every OS — the Ollama
    # probe stays offline-safe and fast.
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(ollama_endpoint="http://127.0.0.1:9"))

    rows = available_backends(cfg)

    assert [row["name"] for row in rows] == list(EMBEDDING_BACKENDS)
    for row in rows:
        assert row["ready"] is False
        assert row["reason"]
        assert row["default_model"] == DEFAULT_MODELS[row["name"]]


def test_available_backends_uses_configured_ollama_endpoint(monkeypatch, no_secrets):
    seen: dict[str, str] = {}

    class _Probe:
        name = "ollama"

        def ready(self):
            return True, ""

    def fake_factory(endpoint="", **kwargs):
        seen["endpoint"] = endpoint
        return _Probe()

    monkeypatch.setattr(emb, "OllamaEmbedding", fake_factory)
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(ollama_endpoint="http://box:9999"))

    rows = available_backends(cfg)

    assert seen["endpoint"] == "http://box:9999"
    assert rows[0] == {
        "name": "ollama",
        "ready": True,
        "reason": "",
        "default_model": DEFAULT_MODELS["ollama"],
    }


# ---------------------------------------------------------------------------
# The failure CODE beside the status (jarvis.ultrawiki.embeddings._error_code)
# ---------------------------------------------------------------------------


def test_an_exhausted_quota_is_distinguishable_from_a_rate_limit(fake_secrets):
    """Both answer HTTP 429, and for a backlog they are opposites: one clears
    itself, the other never does. Only the body's code slug can tell them
    apart, so it has to survive into the error text the pipeline reads."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "message": "You exceeded your current quota, please check "
                    "your plan and billing details.",
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            },
        )

    backend = OpenAIEmbedding(transport=httpx.MockTransport(refuse))
    with pytest.raises(EmbeddingError) as excinfo:
        import asyncio

        asyncio.run(backend.embed(["hello"], model="text-embedding-3-small"))

    message = str(excinfo.value)
    assert "HTTP 429" in message
    assert "insufficient_quota" in message


def test_the_error_text_never_carries_the_providers_prose(fake_secrets):
    """A message can quote the submitted text straight back, and this string
    is logged and stored on the item row. Only a slug is ever taken."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "Invalid input: 'my private note text'"}},
        )

    backend = OpenAIEmbedding(transport=httpx.MockTransport(refuse))
    with pytest.raises(EmbeddingError) as excinfo:
        import asyncio

        asyncio.run(backend.embed(["my private note text"], model="m"))

    message = str(excinfo.value)
    assert "HTTP 400" in message
    assert "private note" not in message


def test_a_body_without_a_code_still_yields_an_honest_error(fake_secrets):
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    backend = OpenAIEmbedding(transport=httpx.MockTransport(refuse))
    with pytest.raises(EmbeddingError, match="HTTP 503"):
        import asyncio

        asyncio.run(backend.embed(["hello"], model="m"))
