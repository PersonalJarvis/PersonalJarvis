"""connect_pat must validate the pasted token per auth_scheme:
  bearer        -> Authorization: Bearer <token>
  bot           -> Authorization: Bot <token>      (Discord)
  telegram_path -> token in the URL {token}, no header, body ok==true

Plus the two paste-flow refinements both layers rely on:
  * `token_prefix` may be a legacy bare string OR a list of complete literal
    alternatives (GitHub issues classic `ghp_` AND fine-grained `github_pat_`
    tokens) — the route must accept any listed shape and reject the rest;
  * a provider-reported expiry (GitHub's
    `github-authentication-token-expiration` response header) must land in the
    stored `Tokens.expires_at` — the paste flow's only chance to learn it.
"""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException

from jarvis.marketplace.catalog import PatPasteAuth, PluginSpec
from jarvis.marketplace.token_store import Tokens
from jarvis.ui.web import marketplace_routes as mr


def _spec(
    scheme: str,
    validation_endpoint: str,
    *,
    plugin_id: str = "x",
    token_prefix: str | list[str] = "",
) -> PluginSpec:
    return PluginSpec(
        id=plugin_id,
        display_name="X",
        description="d",
        category="Communication",
        logo_slug="x",
        auth=PatPasteAuth(
            mode="pat_paste",
            token_creation_url="https://x",  # noqa: S106 - URL, not a secret
            token_prefix=token_prefix,
            validation_endpoint=validation_endpoint,
            instruction_md="md",
            auth_scheme=scheme,
        ),
    )


def _capture_transport(captured, *, body=None, status=200):
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(status, json=body if body is not None else {"ok": True})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_bearer_scheme_uses_bearer_header():
    captured = {}
    validate = mr._make_validator(_capture_transport(captured))
    ok, status, expires_at = await validate(
        _spec("bearer", "https://api.example/me").auth, "tok123"
    )
    assert ok is True and status == 200
    assert expires_at is None  # no expiry header from this provider
    assert captured["auth"] == "Bearer tok123"
    assert captured["url"] == "https://api.example/me"


@pytest.mark.asyncio
async def test_bot_scheme_uses_bot_header():
    captured = {}
    validate = mr._make_validator(_capture_transport(captured))
    ok, _, _ = await validate(
        _spec("bot", "https://discord.com/api/v10/users/@me").auth, "abc.def.ghi"
    )
    assert ok is True
    assert captured["auth"] == "Bot abc.def.ghi"


@pytest.mark.asyncio
async def test_telegram_path_splices_token_and_sends_no_header():
    captured = {}
    validate = mr._make_validator(
        _capture_transport(captured, body={"ok": True})
    )
    ok, _, _ = await validate(
        _spec("telegram_path", "https://api.telegram.org/bot{token}/getMe").auth,
        "123:ABC",
    )
    assert ok is True
    assert "123:ABC" in captured["url"]
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_telegram_path_rejects_ok_false_even_on_200():
    captured = {}
    validate = mr._make_validator(
        _capture_transport(captured, body={"ok": False, "error_code": 401})
    )
    ok, _, _ = await validate(
        _spec("telegram_path", "https://api.telegram.org/bot{token}/getMe").auth,
        "bad",
    )
    assert ok is False


# ---------------------------------------------------------------------------
# Provider-reported token expiry (GitHub's expiration header)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_scheme_captures_github_expiry_header():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"login": "someone"},
            headers={"github-authentication-token-expiration": "2026-11-13 07:19:11 UTC"},
        )

    validate = mr._make_validator(httpx.MockTransport(handler))
    ok, _, expires_at = await validate(
        _spec("bearer", "https://api.github.com/user").auth, "ghp_dated"
    )
    assert ok is True
    assert expires_at == datetime(2026, 11, 13, 7, 19, 11, tzinfo=UTC)


@pytest.mark.asyncio
async def test_unparsable_expiry_header_degrades_to_none():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={},
            headers={"github-authentication-token-expiration": "someday soon"},
        )

    validate = mr._make_validator(httpx.MockTransport(handler))
    ok, _, expires_at = await validate(
        _spec("bearer", "https://api.github.com/user").auth, "ghp_dated"
    )
    assert ok is True
    assert expires_at is None  # honest absence beats a guessed timestamp


# ---------------------------------------------------------------------------
# Route-level: prefix lists + expiry persistence
# ---------------------------------------------------------------------------


class _Catalog:
    def __init__(self, specs):
        self.plugins = specs

    def by_id(self, plugin_id: str):
        return next((s for s in self.plugins if s.id == plugin_id), None)


class _DummyRequest:
    """connect_pat only touches request.app.state for channel plugins."""

    class app:  # noqa: D106 - minimal stand-in
        state = object()


def _route_fixture(monkeypatch, spec: PluginSpec, expires_at: datetime | None):
    saved: dict[str, Tokens] = {}

    class _Store:
        def save(self, plugin_id: str, tokens: Tokens) -> None:
            saved[plugin_id] = tokens

    async def _fake_validate(auth, token, instance_url=None):
        return True, 200, expires_at

    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog([spec]))
    monkeypatch.setattr(mr, "TokenStore", _Store)
    monkeypatch.setattr(mr, "_validate_token", _fake_validate)
    monkeypatch.setattr(mr, "_refresh_plugin_in_live_registry", lambda plugin_id: None)
    return saved


@pytest.mark.asyncio
async def test_connect_pat_accepts_every_listed_prefix_and_stores_expiry(monkeypatch):
    spec = _spec(
        "bearer",
        "https://api.github.com/user",
        plugin_id="gh",
        token_prefix=["ghp_", "github_pat_"],
    )
    expiry = datetime(2026, 11, 13, 7, 19, 11, tzinfo=UTC)
    saved = _route_fixture(monkeypatch, spec, expiry)

    for token in ("ghp_classic123", "github_pat_finegrained456"):
        result = await mr.connect_pat(
            "gh", mr.PatConnectBody(token=token), _DummyRequest()
        )
        assert result["ok"] is True
        assert saved["gh"].access == token
        assert saved["gh"].expires_at == expiry


@pytest.mark.asyncio
async def test_connect_pat_rejects_token_matching_no_listed_prefix(monkeypatch):
    spec = _spec(
        "bearer",
        "https://api.github.com/user",
        plugin_id="gh",
        token_prefix=["ghp_", "github_pat_"],
    )
    saved = _route_fixture(monkeypatch, spec, None)

    with pytest.raises(HTTPException) as exc_info:
        await mr.connect_pat(
            "gh",
            mr.PatConnectBody(token="gho_oauthapp"),  # noqa: S106 - test fixture
            _DummyRequest(),
        )
    assert exc_info.value.status_code == 400
    assert "'ghp_' or 'github_pat_'" in exc_info.value.detail
    assert not saved  # nothing persisted for a rejected shape
