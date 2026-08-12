"""Route resolution for Google keys: AI Studio vs Vertex AI express mode.

The prefix ``AQ.`` is issued by BOTH Google AI Studio and Vertex express, so
the resolver may only trust shape for ``AIza`` keys and must probe the rest.
The probe is two-step (verified against live endpoint behaviour, 2026-08-12):
an AI Studio rejection alone does NOT prove a Vertex key — an invalid ``AQ.``
key is rejected by both hosts — so only a successful Vertex ``countTokens``
counter-probe confirms the Vertex route. Verdicts are cached once per key;
network trouble and both-rejected keys are never cached. These tests drive
the probe through ``httpx.MockTransport`` (a real transport, no network).
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


class _FakeGoogle:
    """Offline stand-in for both Google hosts, with per-endpoint call counts.

    ``vertex_statuses`` maps a probe-model id to its countTokens answer, so a
    test can retire one candidate model (404) and serve the next.
    """

    def __init__(
        self,
        aistudio_status: int,
        vertex_statuses: dict[str, int] | int = 401,
    ) -> None:
        self.aistudio_status = aistudio_status
        if isinstance(vertex_statuses, int):
            vertex_statuses = dict.fromkeys(gg._VERTEX_PROBE_MODELS, vertex_statuses)
        self.vertex_statuses = vertex_statuses
        self.aistudio_calls = 0
        self.vertex_calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        # The key must travel in the header, never the URL (log safety).
        assert "key" not in request.url.params
        assert request.headers.get("x-goog-api-key")
        if request.url.host == "generativelanguage.googleapis.com":
            self.aistudio_calls += 1
            return httpx.Response(self.aistudio_status, json={})
        assert request.url.host == "aiplatform.googleapis.com"
        assert request.method == "POST"
        model = request.url.path.split("/")[-1].removesuffix(":countTokens")
        self.vertex_calls.append(model)
        return httpx.Response(self.vertex_statuses[model], json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


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


def test_aistudio_200_routes_aistudio_without_vertex_probe():
    fake = _FakeGoogle(200)
    assert gg.resolve_google_key_route("AQ.studio-key-1", transport=fake.transport) == "aistudio"
    assert gg.resolve_google_key_route("AQ.studio-key-1", transport=fake.transport) == "aistudio"
    assert fake.aistudio_calls == 1, "second resolve must hit the cache"
    assert fake.vertex_calls == [], "an accepted key needs no counter-probe"


def test_aistudio_reject_plus_vertex_ok_routes_vertex_and_caches():
    fake = _FakeGoogle(401, {gg._VERTEX_PROBE_MODELS[0]: 200})
    assert gg.resolve_google_key_route("AQ.express-key-1", transport=fake.transport) == "vertex"
    assert gg.resolve_google_key_route("AQ.express-key-1", transport=fake.transport) == "vertex"
    assert fake.aistudio_calls == 1
    assert fake.vertex_calls == [gg._VERTEX_PROBE_MODELS[0]]


def test_rejected_by_both_defaults_aistudio_without_caching():
    # Measured live: an invalid AQ. key gets 401 from BOTH hosts. That is a
    # key error, not a routing signal — the honest upstream error must come
    # from the historical endpoint, and nothing may be cached.
    fake = _FakeGoogle(401, 401)
    assert gg.resolve_google_key_route("AQ.broken-key-1", transport=fake.transport) == "aistudio"
    assert gg.resolve_google_key_route("AQ.broken-key-1", transport=fake.transport) == "aistudio"
    assert fake.aistudio_calls == 2, "a both-rejected verdict must not be pinned"


def test_vertex_404_tries_the_next_probe_model():
    # A retired probe model answers 404 — that is "model gone", not an auth
    # verdict, so the next candidate decides.
    first, second = gg._VERTEX_PROBE_MODELS
    fake = _FakeGoogle(401, {first: 404, second: 200})
    assert gg.resolve_google_key_route("AQ.express-key-2", transport=fake.transport) == "vertex"
    assert fake.vertex_calls == [first, second]


def test_aistudio_5xx_defaults_aistudio_without_caching():
    fake = _FakeGoogle(503)
    assert gg.resolve_google_key_route("AQ.flaky-key-1", transport=fake.transport) == "aistudio"
    assert gg.resolve_google_key_route("AQ.flaky-key-1", transport=fake.transport) == "aistudio"
    assert fake.aistudio_calls == 2, "an outage verdict must not be pinned"
    assert fake.vertex_calls == [], "a 5xx is not an auth reject — no counter-probe"


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
    fake = _FakeGoogle(401, {gg._VERTEX_PROBE_MODELS[0]: 200})
    assert gg.resolve_google_key_route("AQ.shared-key-1", transport=fake.transport) == "vertex"
    verdict = asyncio.run(
        gg.resolve_google_key_route_async("AQ.shared-key-1", transport=fake.transport)
    )
    assert verdict == "vertex"
    assert fake.aistudio_calls == 1, "the async twin must reuse the sync verdict"


def test_async_probe_works_standalone():
    fake = _FakeGoogle(401, {gg._VERTEX_PROBE_MODELS[0]: 200})
    verdict = asyncio.run(
        gg.resolve_google_key_route_async("AQ.async-key-1", transport=fake.transport)
    )
    assert verdict == "vertex"
    assert fake.aistudio_calls == 1
    assert fake.vertex_calls == [gg._VERTEX_PROBE_MODELS[0]]


# ── config override ──────────────────────────────────────────────────────────


def test_vertex_mode_always_skips_the_probe(monkeypatch):
    monkeypatch.setattr(gg, "_configured_mode", lambda: "always")
    fake = _FakeGoogle(200)
    assert gg.resolve_google_key_route("AQ.forced-key-1", transport=fake.transport) == "vertex"
    assert fake.aistudio_calls == 0


def test_vertex_mode_never_skips_the_probe(monkeypatch):
    monkeypatch.setattr(gg, "_configured_mode", lambda: "never")
    fake = _FakeGoogle(401, 200)
    assert gg.resolve_google_key_route("AQ.forced-key-2", transport=fake.transport) == "aistudio"
    assert fake.aistudio_calls == 0


def test_vertex_mode_always_leaves_aiza_untouched(monkeypatch):
    monkeypatch.setattr(gg, "_configured_mode", lambda: "always")
    # ``always`` governs AMBIGUOUS keys only; an AIza key stays AI Studio —
    # a standard key can never authenticate against Vertex (measured: 401
    # CREDENTIALS_MISSING), so forcing it would only break a working setup.
    assert gg.resolve_google_key_route("AIzaSyClassic") == "aistudio"


def test_google_config_defaults_to_auto():
    from jarvis.core.config import JarvisConfig

    assert JarvisConfig().google.vertex_mode == "auto"


# ── client kwargs assembly ───────────────────────────────────────────────────


def test_client_kwargs_aistudio_matches_the_historical_call():
    assert gg._client_kwargs("k", "aistudio", None) == {"api_key": "k"}


def test_client_kwargs_vertex_sets_the_express_flag_only():
    # Express mode is exactly vertexai=True + the key: no project, no location
    # (officially documented; the key alone satisfies the SDK's auth gate).
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
