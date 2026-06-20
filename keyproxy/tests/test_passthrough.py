"""End-to-end passthrough behaviour against a FAKE upstream.

The app's outbound ``httpx.AsyncClient`` is injected with an
``httpx.MockTransport`` so no real vendor is contacted. We drive the app itself
via ``httpx.ASGITransport``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from keyproxy.app import create_app
from keyproxy.config import ProxyConfig
from keyproxy.store import Store
from keyproxy.tokens import TokenStore
from keyproxy.usage import UsageStore

from .conftest import stream_response


def _json_stream(status: int, obj: dict, **kw) -> httpx.Response:
    return stream_response(
        status,
        json.dumps(obj).encode(),
        headers={"content-type": "application/json"},
        **kw,
    )


# --------------------------------------------------------------------------
# Test harness: an app wired to an in-memory store + a programmable upstream.
# --------------------------------------------------------------------------


def build_harness(upstream_handler, *, providers=None, allow_insecure=True):
    """Return (app, token_store, usage_store, captured) wired to a fake upstream.

    ``captured`` is a list the upstream handler appends each received request to,
    so a test can assert on the outbound request the proxy built.
    """
    store = Store(":memory:")
    tokens = TokenStore(store)
    usage = UsageStore(store)
    cfg = ProxyConfig(
        providers=providers
        or {
            "openai": (
                "openai_compatible",
                "https://api.openai.com/v1",
                "sk-REAL-OPENAI",
            ),
            "claude-api": (
                "anthropic",
                "https://api.anthropic.com",
                "sk-ant-REAL",
            ),
            "gemini": (
                "gemini",
                "https://generativelanguage.googleapis.com",
                "REAL-GEMINI",
            ),
        },
        admin_key="admin-secret",
        allow_insecure=allow_insecure,
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))
    app = create_app(config=cfg, tokens=tokens, usage=usage, upstream=upstream)
    return app, tokens, usage


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy"
    )


# --------------------------------------------------------------------------
# Fail-closed auth / provider
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_token_is_401() -> None:
    def upstream(_req: httpx.Request) -> httpx.Response:
        return _json_stream(200, {"ok": True})

    app, _tokens, _usage = build_harness(upstream)
    async with client_for(app) as c:
        r = await c.post("/p/openai/chat/completions", json={"x": 1})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_bad_token_is_401() -> None:
    def upstream(_req: httpx.Request) -> httpx.Response:
        return _json_stream(200, {"ok": True})

    app, _tokens, _usage = build_harness(upstream)
    async with client_for(app) as c:
        r = await c.post(
            "/p/openai/chat/completions",
            headers={"authorization": "Bearer kp_not_real"},
            json={"x": 1},
        )
    assert r.status_code == 401


@pytest.mark.anyio
async def test_revoked_token_is_401() -> None:
    def upstream(_req: httpx.Request) -> httpx.Response:
        return _json_stream(200, {"ok": True})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    tokens.revoke(issued.id)
    async with client_for(app) as c:
        r = await c.post(
            "/p/openai/chat/completions",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"x": 1},
        )
    assert r.status_code == 401


@pytest.mark.anyio
async def test_unknown_provider_is_404() -> None:
    def upstream(_req: httpx.Request) -> httpx.Response:
        return _json_stream(200, {"ok": True})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.post(
            "/p/no-such-provider/x",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"x": 1},
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "provider not available"


@pytest.mark.anyio
async def test_known_but_unkeyed_provider_404_is_indistinguishable() -> None:
    # grok is in the wire contract but NOT keyed in this harness -> same generic
    # 404 + message as a totally unknown provider, so probing can't enumerate
    # known-but-unkeyed providers.
    def upstream(_req: httpx.Request) -> httpx.Response:
        return _json_stream(200, {"ok": True})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        unkeyed = await c.post(
            "/p/grok/v1/chat/completions",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"x": 1},
        )
        unknown = await c.post(
            "/p/no-such-provider/x",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"x": 1},
        )
    assert unkeyed.status_code == unknown.status_code == 404
    assert unkeyed.json() == unknown.json()


@pytest.mark.anyio
async def test_upstream_unreachable_is_502_without_leaking_url() -> None:
    real_base = "https://api.openai.com/v1"

    def upstream(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to connect to " + real_base)

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.post(
            "/p/openai/chat/completions",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"model": "gpt-4o-mini"},
        )
    assert r.status_code == 502
    body = r.text
    # The static message is returned; the real vendor URL never leaks.
    assert r.json()["detail"] == "upstream vendor could not be reached"
    assert real_base not in body
    assert "api.openai.com" not in body


# --------------------------------------------------------------------------
# Credential swap + header hygiene (openai_compatible)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_real_key_swapped_and_inbound_token_stripped() -> None:
    captured: list[httpx.Request] = []

    def upstream(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _json_stream(200, {"ok": True})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.post(
            "/p/openai/chat/completions",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"model": "gpt-4o-mini"},
        )
    assert r.status_code == 200
    assert len(captured) == 1
    out = captured[0]
    # Real key is set; the inbound proxy token is gone.
    assert out.headers["authorization"] == "Bearer sk-REAL-OPENAI"
    assert issued.plaintext not in out.headers["authorization"]
    # Target URL is real_base + "/" + path.
    assert str(out.url) == "https://api.openai.com/v1/chat/completions"
    # Body is forwarded unchanged.
    assert json.loads(out.content) == {"model": "gpt-4o-mini"}


@pytest.mark.anyio
async def test_hop_by_hop_headers_dropped() -> None:
    captured: list[httpx.Request] = []

    def upstream(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _json_stream(200, {"ok": True})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        await c.post(
            "/p/openai/chat/completions",
            headers={
                "authorization": f"Bearer {issued.plaintext}",
                # ``te`` is a hop-by-hop header that httpx does NOT re-add on the
                # outbound connection, so its absence proves our stripping.
                "te": "trailers",
                "x-custom-app-header": "keep-me",
            },
            json={"model": "gpt-4o-mini"},
        )
    out = captured[0]
    lower = {k.lower() for k in out.headers}
    assert "te" not in lower
    # The inbound auth header must never reach the upstream verbatim.
    assert out.headers.get("authorization") == "Bearer sk-REAL-OPENAI"
    # A non-hop-by-hop custom header is allowed through.
    assert out.headers.get("x-custom-app-header") == "keep-me"


# --------------------------------------------------------------------------
# anthropic + gemini credential placement
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_anthropic_x_api_key_swap() -> None:
    captured: list[httpx.Request] = []

    def upstream(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _json_stream(200, {"ok": True})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        await c.post(
            "/p/claude-api/v1/messages",
            headers={"x-api-key": issued.plaintext},
            json={"model": "claude-3-5-sonnet"},
        )
    out = captured[0]
    assert out.headers["x-api-key"] == "sk-ant-REAL"
    assert str(out.url) == "https://api.anthropic.com/v1/messages"


@pytest.mark.anyio
async def test_gemini_query_key_swapped_to_header() -> None:
    captured: list[httpx.Request] = []

    def upstream(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _json_stream(200, {"ok": True})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        await c.post(
            f"/p/gemini/v1beta/models/gemini-2.0-flash:generateContent?key={issued.plaintext}",
            json={"contents": []},
        )
    out = captured[0]
    # The inbound ?key= (the proxy token) must NOT reach Google.
    assert "key" not in dict(out.url.params)
    assert out.headers["x-goog-api-key"] == "REAL-GEMINI"


# --------------------------------------------------------------------------
# Streaming round-trip + usage row
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_streamed_completion_round_trips_and_records_usage() -> None:
    sse = (
        b'data: {"model":"gpt-4o-mini","choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"model":"gpt-4o-mini","usage":{"prompt_tokens":5,'
        b'"completion_tokens":7,"total_tokens":12}}\n\n'
        b"data: [DONE]\n\n"
    )

    def upstream(_req: httpx.Request) -> httpx.Response:
        # chunk_size forces a multi-chunk body so the streaming path (not a
        # single buffered write) is exercised.
        return stream_response(
            200,
            sse,
            headers={"content-type": "text/event-stream"},
            chunk_size=16,
        )

    app, tokens, usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.post(
            "/p/openai/chat/completions",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"model": "gpt-4o-mini", "stream": True},
        )
    assert r.status_code == 200
    assert r.content == sse  # body round-trips unchanged
    assert r.headers["content-type"] == "text/event-stream"

    rows = usage.recent()
    assert len(rows) == 1
    assert rows[0]["token_id"] == issued.id
    assert rows[0]["provider_id"] == "openai"
    assert rows[0]["prompt_tokens"] == 5
    assert rows[0]["completion_tokens"] == 7
    assert rows[0]["total_tokens"] == 12


@pytest.mark.anyio
async def test_upstream_error_status_passes_through() -> None:
    def upstream(_req: httpx.Request) -> httpx.Response:
        return _json_stream(429, {"error": "rate limited"})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.post(
            "/p/openai/chat/completions",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"model": "gpt-4o-mini"},
        )
    assert r.status_code == 429
    assert r.json() == {"error": "rate limited"}


@pytest.mark.anyio
async def test_usage_parse_miss_still_records_row() -> None:
    def upstream(_req: httpx.Request) -> httpx.Response:
        return stream_response(200, b"not parseable")

    app, tokens, usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.post(
            "/p/openai/chat/completions",
            headers={"authorization": f"Bearer {issued.plaintext}"},
            json={"model": "gpt-4o-mini"},
        )
    assert r.status_code == 200
    rows = usage.recent()
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] is None


@pytest.mark.anyio
async def test_get_method_round_trips() -> None:
    captured: list[httpx.Request] = []

    def upstream(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _json_stream(200, {"data": ["model-a"]})

    app, tokens, _usage = build_harness(upstream)
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.get(
            "/p/openai/models",
            headers={"authorization": f"Bearer {issued.plaintext}"},
        )
    assert r.status_code == 200
    assert captured[0].method == "GET"
    assert str(captured[0].url) == "https://api.openai.com/v1/models"
