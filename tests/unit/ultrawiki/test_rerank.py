"""Offline unit tests for the UltraWiki rerank backends.

HTTP rides ``httpx.MockTransport`` through the injectable ``transport``
parameter; secrets are monkeypatched at the rerank module's import site. The
LLM backend's provider chain is stubbed, never probed — a test must never
assert against the host's live credentials.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import jarvis.ultrawiki.rerank as rr
from jarvis.ultrawiki.rerank import (
    DEFAULT_RERANK_MODELS,
    MAX_SCORE,
    RERANK_BACKENDS,
    CohereReranker,
    LLMReranker,
    RerankError,
    VoyageReranker,
    available_rerankers,
    build_rerank_prompt,
    parse_rerank_scores,
    resolve_reranker,
)

FAKE_KEY = "unit-test-key-456"


@pytest.fixture
def no_secrets(monkeypatch):
    monkeypatch.setattr(rr, "get_secret", lambda key, env_fallback=None: None)


@pytest.fixture
def fake_secrets(monkeypatch):
    monkeypatch.setattr(rr, "get_secret", lambda key, env_fallback=None: FAKE_KEY)


@pytest.fixture
def dead_llm_chain(monkeypatch):
    """No credential-ready chat provider — the LLM backend reports not ready."""
    monkeypatch.setattr(LLMReranker, "_chain", lambda self: [])


@pytest.fixture
def live_llm_chain(monkeypatch):
    """One credential-ready provider family, host-independent."""
    monkeypatch.setattr(LLMReranker, "_chain", lambda self: [("fakeprovider", "cheap")])


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

    # vendor 0-1 relevance is normalized onto the shared 0-10 grade
    assert result == [(2, 9.1), (0, 4.2)]
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

    assert result == [(1, 8.8)]
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
    backend = backend_class()
    usable, reason = backend.ready()
    assert usable is False
    assert reason
    # Names the headless recovery path (the env var) and points at the field
    # on the card for everyone else. The raw snake_case slot name is
    # deliberately absent — the input box it would name is right below.
    assert backend._SECRET_SLOT.upper() in reason
    assert "add one below" in reason


@pytest.mark.parametrize("backend_class", [VoyageReranker, CohereReranker])
def test_ready_with_key_is_true(fake_secrets, backend_class):
    assert backend_class().ready() == (True, "")


# ----------------------------------------------------------------------
# Registry + honest skip
# ----------------------------------------------------------------------


def test_registry_and_default_models_cover_the_same_backends():
    assert set(RERANK_BACKENDS) == {"llm", "voyage", "cohere"}
    assert set(DEFAULT_RERANK_MODELS) == {"llm", "voyage", "cohere"}
    # the universal backend leads the dropdown
    assert next(iter(RERANK_BACKENDS)) == "llm"


def test_available_rerankers_without_keys_reports_all_not_ready(
    no_secrets, dead_llm_chain
):
    rows = available_rerankers(SimpleNamespace())

    assert [row["name"] for row in rows] == list(RERANK_BACKENDS)
    for row in rows:
        assert row["ready"] is False
        assert row["reason"]
        assert row["default_model"] == DEFAULT_RERANK_MODELS[row["name"]]
        assert row["detail"]  # every option explains itself in the UI


def test_available_rerankers_reports_llm_ready_on_a_keyless_but_local_install(
    no_secrets, live_llm_chain
):
    """The universality guarantee (§3/AP-22): an install with no rerank-vendor
    account at all still has a working rerank option."""
    rows = {row["name"]: row for row in available_rerankers(SimpleNamespace())}

    assert rows["llm"]["ready"] is True
    assert rows["voyage"]["ready"] is False
    assert rows["cohere"]["ready"] is False


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


def test_resolve_reranker_llm_needs_no_vendor_key(no_secrets, live_llm_chain):
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(rerank_provider="llm"))
    assert isinstance(resolve_reranker(cfg), LLMReranker)


def test_resolve_reranker_llm_without_any_provider_skips_honestly(
    no_secrets, dead_llm_chain
):
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(rerank_provider="llm"))
    assert resolve_reranker(cfg) is None


# ----------------------------------------------------------------------
# LLM backend — prompt, parsing, chain
# ----------------------------------------------------------------------


def test_rerank_prompt_numbers_candidates_and_pins_the_zero_to_ten_scale():
    prompt = build_rerank_prompt("who paid the invoice?", ["first doc", "second doc"])

    assert "[0] first doc" in prompt
    assert "[1] second doc" in prompt
    assert "<question>who paid the invoice?</question>" in prompt
    assert "10 = directly and completely answers" in prompt
    assert '{"scores":[{"i":0,"score":7}' in prompt  # strict-JSON contract


def test_rerank_prompt_truncates_long_candidates():
    prompt = build_rerank_prompt("q", ["x" * 5000])

    assert len(prompt) < 2500
    assert "[…]" in prompt


def test_parse_rerank_scores_sorts_by_grade_and_keeps_indices():
    pairs = parse_rerank_scores(
        '{"scores":[{"i":0,"score":3},{"i":2,"score":9},{"i":1,"score":7}]}',
        count=3,
    )

    assert pairs == [(2, 9.0), (1, 7.0), (0, 3.0)]


def test_parse_rerank_scores_survives_fences_and_prose():
    text = 'Sure! Here you go:\n```json\n{"scores":[{"i":1,"score":8}]}\n```'

    assert parse_rerank_scores(text, count=2) == [(1, 8.0)]


def test_parse_rerank_scores_drops_out_of_range_duplicate_and_junk_entries():
    pairs = parse_rerank_scores(
        '{"scores":[{"i":0,"score":5},{"i":0,"score":9},{"i":99,"score":10},'
        '{"i":1,"score":"nope"},{"nope":true},{"i":1,"score":6}]}',
        count=2,
    )

    assert pairs == [(1, 6.0), (0, 5.0)]


def test_parse_rerank_scores_clamps_out_of_scale_grades():
    pairs = parse_rerank_scores(
        '{"scores":[{"i":0,"score":42},{"i":1,"score":-5}]}', count=2
    )

    assert pairs == [(0, MAX_SCORE), (1, 0.0)]


@pytest.mark.parametrize(
    "text",
    ["not json at all", '{"nothing":"useful"}', '{"scores":[]}', '{"scores":"eight"}'],
)
def test_parse_rerank_scores_rejects_unusable_output(text):
    with pytest.raises(RerankError):
        parse_rerank_scores(text, count=2)


def test_llm_ready_without_a_chain_names_the_in_app_recovery(dead_llm_chain):
    usable, reason = LLMReranker(SimpleNamespace()).ready()

    assert usable is False
    assert "API-Keys" in reason
    assert "Ollama" in reason  # the offline path is spelled out


def test_llm_ready_with_a_chain_is_true(live_llm_chain):
    assert LLMReranker(SimpleNamespace()).ready() == (True, "")


def test_llm_ready_never_raises_when_the_chain_probe_explodes(monkeypatch):
    def boom(self):
        raise RuntimeError("registry on fire")

    monkeypatch.setattr(LLMReranker, "_chain", boom)
    usable, reason = LLMReranker(SimpleNamespace()).ready()

    assert usable is False
    assert "probe failed" in reason


async def test_llm_rerank_grades_through_the_provider_chain(monkeypatch, live_llm_chain):
    seen: dict = {}

    async def fake_complete(**kwargs):
        seen.update(kwargs)
        graded = '{"scores":[{"i":1,"score":9},{"i":0,"score":2}]}'
        return SimpleNamespace(text=graded), "fakeprovider"

    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.complete_with_fallback", fake_complete
    )
    backend = LLMReranker(SimpleNamespace(), registry=object())

    pairs = await backend.rerank("who broke the build?", ["doc a", "doc b"], top_k=2)

    assert pairs == [(1, 9.0), (0, 2.0)]
    assert seen["chain"] == [("fakeprovider", "cheap")]
    assert seen["request"].temperature == 0.0  # grading, not creativity


async def test_llm_rerank_respects_top_k(monkeypatch, live_llm_chain):
    async def fake_complete(**kwargs):
        return (
            SimpleNamespace(
                text='{"scores":[{"i":0,"score":9},{"i":1,"score":8},{"i":2,"score":7}]}'
            ),
            "fakeprovider",
        )

    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.complete_with_fallback", fake_complete
    )

    pairs = await LLMReranker(SimpleNamespace(), registry=object()).rerank(
        "q", ["a", "b", "c"], top_k=2
    )

    assert pairs == [(0, 9.0), (1, 8.0)]


async def test_llm_rerank_validator_rejects_unusable_output_so_the_chain_advances(
    monkeypatch, live_llm_chain
):
    """A transport success carrying no grades must not end the chain — the
    next provider family gets its turn (the distill validator idiom)."""
    captured: dict = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return None  # the chain exhausted itself

    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.complete_with_fallback", fake_complete
    )
    backend = LLMReranker(SimpleNamespace(), registry=object())

    with pytest.raises(RerankError, match="usable relevance grades"):
        await backend.rerank("q", ["a"], top_k=1)

    validate = captured["validate"]
    assert validate(SimpleNamespace(text="I think document one is nice")) is not None
    assert validate(SimpleNamespace(text='{"scores":[{"i":0,"score":4}]}')) is None


async def test_llm_rerank_without_a_chain_raises_rather_than_calling_out(
    dead_llm_chain,
):
    with pytest.raises(RerankError, match="no credential-ready provider"):
        await LLMReranker(SimpleNamespace()).rerank("q", ["a"], top_k=1)


async def test_llm_rerank_empty_documents_short_circuits(live_llm_chain):
    assert await LLMReranker(SimpleNamespace()).rerank("q", [], top_k=3) == []
