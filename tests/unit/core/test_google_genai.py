"""Route resolution for Google keys: AI Studio vs Vertex AI express mode.

The prefix ``AQ.`` is issued by BOTH Google AI Studio and Vertex express, so
the resolver may only trust shape for ``AIza`` keys and must probe the rest —
once, with the verdict cached, and never caching a network hiccup. These tests
drive the probe through ``httpx.MockTransport`` (a real transport, no network)
so every branch is exercised offline.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from jarvis.core import google_genai as gg


@pytest.fixture(autouse=True)
def _fresh_cache():
    gg.reset_route_cache()
    yield
    gg.reset_route_cache()


def _transport(status_code: int, counter: list[int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        counter[0] += 1
        # The key must travel in the header, never the URL (log safety).
        assert "key" not in request.url.params
        assert request.headers.get("x-goog-api-key")
        return httpx.Response(status_code, json={})

    return httpx.MockTransport(handler)


# ── shape classification ─────────────────────────────────────────────────────


def test_aiza_keys_are_aistudio_by_shape():
    assert gg.classify_google_key("AIzaSyFakeKey123") == "aistudio"


def test_aq_keys_are_ambiguous():
    assert gg.classify_google_key("AQ.fake-express-or-studio") is None


def test_foreign_shapes_fall_back_to_aistudio():
    # A wrong-provider paste must fail with the same upstream error as before
    # this module existed, not with a surprising Vertex error.
    assert gg.classify_google_key("sk-ant-somekey") == "aistudio"


# ── probe verdicts ───────────────────────────────────────────────────────────


def test_probe_200_routes_aistudio_and_caches():
    calls = [0]
    t = _transport(200, calls)
    assert gg.resolve_google_key_route("AQ.studio-key-1", transport=t) == "aistudio"
    assert gg.resolve_google_key_route("AQ.studio-key-1", transport=t) == "aistudio"
    assert calls[0] == 1, "second resolve must hit the cache, not the network"


def test_probe_400_routes_vertex_and_caches():
    calls = [0]
    t = _transport(400, calls)
    assert gg.resolve_google_key_route("AQ.express-key-1", transport=t) == "vertex"
    assert gg.resolve_google_key_route("AQ.express-key-1", transport=t) == "vertex"
    assert calls[0] == 1


def test_probe_5xx_defaults_aistudio_without_caching():
    calls = [0]
    t = _transport(503, calls)
    assert gg.resolve_google_key_route("AQ.flaky-key-1", transport=t) == "aistudio"
    assert gg.resolve_google_key_route("AQ.flaky-key-1", transport=t) == "aistudio"
    assert calls[0] == 2, "an outage verdict must not be pinned for the process"


def test_probe_network_error_defaults_aistudio_without_caching():
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        raise httpx.ConnectError("no route to host", request=request)

    t = httpx.MockTransport(handler)
    assert gg.resolve_google_key_route("AQ.offline-key-1", transport=t) == "aistudio"
    assert gg.resolve_google_key_route("AQ.offline-key-1", transport=t) == "aistudio"
    assert calls[0] == 2


def test_async_resolver_shares_the_sync_cache():
    calls = [0]
    t = _transport(400, calls)
    assert gg.resolve_google_key_route("AQ.shared-key-1", transport=t) == "vertex"
    verdict = asyncio.run(gg.resolve_google_key_route_async("AQ.shared-key-1", transport=t))
    assert verdict == "vertex"
    assert calls[0] == 1, "the async twin must reuse the sync verdict"


def test_async_probe_works_standalone():
    calls = [0]
    t = _transport(200, calls)
    verdict = asyncio.run(gg.resolve_google_key_route_async("AQ.async-key-1", transport=t))
    assert verdict == "aistudio"
    assert calls[0] == 1


# ── config override ──────────────────────────────────────────────────────────


def test_vertex_mode_always_skips_the_probe(monkeypatch):
    monkeypatch.setattr(gg, "_configured_mode", lambda: "always")
    calls = [0]
    t = _transport(200, calls)
    assert gg.resolve_google_key_route("AQ.forced-key-1", transport=t) == "vertex"
    assert calls[0] == 0


def test_vertex_mode_never_skips_the_probe(monkeypatch):
    monkeypatch.setattr(gg, "_configured_mode", lambda: "never")
    calls = [0]
    t = _transport(400, calls)
    assert gg.resolve_google_key_route("AQ.forced-key-2", transport=t) == "aistudio"
    assert calls[0] == 0


def test_vertex_mode_never_leaves_aiza_untouched(monkeypatch):
    monkeypatch.setattr(gg, "_configured_mode", lambda: "always")
    # ``always`` governs AMBIGUOUS keys only; an AIza key stays AI Studio —
    # forcing it through Vertex could never work (wrong key family).
    assert gg.resolve_google_key_route("AIzaSyClassic") == "aistudio"


def test_google_config_defaults_to_auto():
    from jarvis.core.config import JarvisConfig

    assert JarvisConfig().google.vertex_mode == "auto"


# ── client kwargs assembly ───────────────────────────────────────────────────


def test_client_kwargs_aistudio_matches_the_historical_call():
    assert gg._client_kwargs("k", "aistudio", None) == {"api_key": "k"}


def test_client_kwargs_vertex_sets_the_express_flag_only():
    assert gg._client_kwargs("k", "vertex", None) == {
        "api_key": "k",
        "vertexai": True,
    }


def test_client_kwargs_pass_http_options_through():
    opts = {"timeout": 1500}
    assert gg._client_kwargs("k", "vertex", opts) == {
        "api_key": "k",
        "vertexai": True,
        "http_options": opts,
    }
