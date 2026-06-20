"""Admin endpoints — issue/list/revoke tokens + usage report, bearer-guarded."""

from __future__ import annotations

import httpx
import pytest

from keyproxy.app import create_app
from keyproxy.config import ProxyConfig
from keyproxy.store import Store
from keyproxy.tokens import TokenStore
from keyproxy.usage import UsageStore
from keyproxy.vendors import ParsedUsage

ADMIN = "admin-secret"


def build_app():
    store = Store(":memory:")
    tokens = TokenStore(store)
    usage = UsageStore(store)
    cfg = ProxyConfig(
        providers={
            "openai": ("openai_compatible", "https://api.openai.com/v1", "sk-REAL"),
        },
        admin_key=ADMIN,
        allow_insecure=True,
    )

    def upstream_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))
    app = create_app(config=cfg, tokens=tokens, usage=usage, upstream=upstream)
    return app, tokens, usage


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy"
    )


def auth() -> dict[str, str]:
    return {"authorization": f"Bearer {ADMIN}"}


@pytest.mark.anyio
async def test_issue_token_returns_plaintext_once() -> None:
    app, _tokens, _usage = build_app()
    async with client_for(app) as c:
        r = await c.post("/admin/tokens", headers=auth(), json={"label": "alice"})
    assert r.status_code == 200
    data = r.json()
    assert data["label"] == "alice"
    assert data["token"].startswith("kp_")
    assert data["id"]


@pytest.mark.anyio
async def test_admin_requires_bearer() -> None:
    app, _tokens, _usage = build_app()
    async with client_for(app) as c:
        r = await c.post("/admin/tokens", json={"label": "alice"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_admin_rejects_wrong_bearer() -> None:
    app, _tokens, _usage = build_app()
    async with client_for(app) as c:
        r = await c.get(
            "/admin/tokens", headers={"authorization": "Bearer wrong"}
        )
    assert r.status_code == 401


@pytest.mark.anyio
async def test_list_tokens_has_no_plaintext_or_hash() -> None:
    app, tokens, _usage = build_app()
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.get("/admin/tokens", headers=auth())
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    body = r.text
    # Neither the plaintext nor the sha256 is exposed via the admin list.
    assert issued.plaintext not in body
    assert "token_sha256" not in body
    assert rows[0]["id"] == issued.id


@pytest.mark.anyio
async def test_revoke_token() -> None:
    app, tokens, _usage = build_app()
    issued = tokens.issue("alice")
    async with client_for(app) as c:
        r = await c.delete(f"/admin/tokens/{issued.id}", headers=auth())
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    assert tokens.verify(issued.plaintext) is None


@pytest.mark.anyio
async def test_revoke_unknown_404() -> None:
    app, _tokens, _usage = build_app()
    async with client_for(app) as c:
        r = await c.delete("/admin/tokens/nope", headers=auth())
    assert r.status_code == 404


@pytest.mark.anyio
async def test_usage_report() -> None:
    app, tokens, usage = build_app()
    issued = tokens.issue("alice")
    usage.record(
        token_id=issued.id,
        provider_id="openai",
        parsed=ParsedUsage("gpt-4o-mini", 10, 20, 30),
    )
    async with client_for(app) as c:
        r = await c.get("/admin/usage", headers=auth())
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["token_id"] == issued.id
    assert rows[0]["calls"] == 1
    assert rows[0]["total_tokens"] == 30


@pytest.mark.anyio
async def test_healthz_is_open_and_reveals_nothing() -> None:
    app, _tokens, _usage = build_app()
    async with client_for(app) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    # Liveness only — must NOT enumerate providers / loaded keys.
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_admin_providers_requires_auth() -> None:
    app, _tokens, _usage = build_app()
    async with client_for(app) as c:
        r = await c.get("/admin/providers")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_admin_providers_lists_configured() -> None:
    app, _tokens, _usage = build_app()
    async with client_for(app) as c:
        r = await c.get("/admin/providers", headers=auth())
    assert r.status_code == 200
    assert r.json() == ["openai"]


@pytest.mark.anyio
async def test_admin_returns_401_not_503_when_no_admin_key() -> None:
    # When KEYPROXY_ADMIN_KEY is unset the admin surface must still answer 401
    # (never a 503 that reveals the key is absent).
    store = Store(":memory:")
    tokens = TokenStore(store)
    usage = UsageStore(store)
    cfg = ProxyConfig(providers={}, admin_key=None, allow_insecure=True)

    def upstream_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))
    app = create_app(config=cfg, tokens=tokens, usage=usage, upstream=upstream)
    async with client_for(app) as c:
        no_auth = await c.get("/admin/tokens")
        with_bearer = await c.get(
            "/admin/tokens", headers={"authorization": "Bearer anything"}
        )
    assert no_auth.status_code == 401
    assert with_bearer.status_code == 401
