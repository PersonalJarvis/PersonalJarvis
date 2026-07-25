"""Model-catalog behavior pins: cloud regression half (S3a) + local providers.

REGRESSION HALF (written and committed green BEFORE the endpoint refactor,
maintainer amendment 2026-07-25): for every cloud catalog provider this pins
the OBSERVABLE fetch behavior — exact URL, auth attachment shape, no-key
behavior, and the bearer_opt anonymous retry — plus the parser output per
payload shape. The refactor that makes endpoints resolve through
``resolve_provider_endpoint`` MUST keep every one of these green without
touching this file: with no base-url override configured, cloud behavior is
byte-identical.
"""

from __future__ import annotations

from typing import Any

import pytest

import jarvis.core.config as cfg
from jarvis.brain.model_catalog import (
    CATALOG_PROVIDERS,
    ModelCatalog,
    parse_models_response,
)
from jarvis.core.config import JarvisConfig


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Records every GET; optionally rejects the first (authed) call with 401."""

    def __init__(self, payload: dict[str, Any], reject_authed: bool = False) -> None:
        self.payload = payload
        self.reject_authed = reject_authed
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        if self.reject_authed and headers and "Authorization" in headers:
            return _FakeResponse({}, status_code=401)
        return _FakeResponse(self.payload)


def _catalog(tmp_path, client: _FakeClient) -> ModelCatalog:
    return ModelCatalog(
        cache_path=tmp_path / "cache.json",
        http_client_factory=lambda: client,
    )


def _plain_env(monkeypatch, keys: dict[str, str | None]) -> None:
    """No base-url overrides, no team proxy — the stock cloud setup."""
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: keys.get(pid))


_OPENAI_SHAPE = {"data": [{"id": "model-b"}, {"id": "model-a"}]}
_GEMINI_SHAPE = {"models": [{"name": "models/gemini-x", "displayName": "Gemini X"}]}

# The pinned cloud contract: provider → (URL, auth shape). Deliberately a
# LITERAL copy, not an import of _ENDPOINTS — the whole point is detecting an
# accidental change of the wire behavior during the endpoint refactor.
_PINNED: dict[str, tuple[str, str]] = {
    "claude-api": ("https://api.anthropic.com/v1/models", "x-api-key"),
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models", "query"),
    "openrouter": ("https://openrouter.ai/api/v1/models", "bearer_opt"),
    "grok": ("https://api.x.ai/v1/models", "bearer"),
    "nvidia": ("https://integrate.api.nvidia.com/v1/models", "bearer_opt"),
}


def test_pinned_contract_covers_every_cloud_catalog_provider() -> None:
    """A provider added to CATALOG_PROVIDERS must be pinned here (or is local,
    covered by the local half below)."""
    cloud = [p for p in CATALOG_PROVIDERS if p in _PINNED]
    assert set(cloud) == set(_PINNED), (
        "CATALOG_PROVIDERS and the pinned cloud contract diverged — pin the new "
        "provider's URL + auth shape here before shipping it"
    )


@pytest.mark.parametrize("provider", sorted(_PINNED))
async def test_cloud_fetch_url_and_auth_are_byte_identical(
    provider: str, tmp_path, monkeypatch
) -> None:
    url, auth = _PINNED[provider]
    payload = _GEMINI_SHAPE if provider == "gemini" else _OPENAI_SHAPE
    client = _FakeClient(payload)
    _plain_env(monkeypatch, {provider: "sk-pin-test"})

    result = await _catalog(tmp_path, client).list_models(provider)

    assert result.source == "live"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == url
    if auth == "x-api-key":
        assert call["headers"] == {
            "x-api-key": "sk-pin-test",
            "anthropic-version": "2023-06-01",
        }
        assert call["params"] == {}
    elif auth == "bearer":
        assert call["headers"] == {"Authorization": "Bearer sk-pin-test"}
        assert call["params"] == {}
    elif auth == "bearer_opt":
        assert call["headers"] == {"Authorization": "Bearer sk-pin-test"}
        assert call["params"] == {}
    elif auth == "query":
        assert call["headers"] == {}
        assert call["params"] == {"key": "sk-pin-test"}


@pytest.mark.parametrize(
    "provider", sorted(p for p, (_, a) in _PINNED.items() if a in ("x-api-key", "bearer", "query"))
)
async def test_keyed_cloud_provider_without_key_never_fetches(
    provider: str, tmp_path, monkeypatch
) -> None:
    """No key → no network call; the picker gets the honest static fallback."""
    client = _FakeClient(_OPENAI_SHAPE)
    _plain_env(monkeypatch, {})

    result = await _catalog(tmp_path, client).list_models(provider)

    assert result.source == "static"
    assert client.calls == []
    assert result.models, "static fallback must still offer a useful list"


@pytest.mark.parametrize(
    "provider", sorted(p for p, (_, a) in _PINNED.items() if a == "bearer_opt")
)
async def test_public_catalog_fetches_anonymously_without_key(
    provider: str, tmp_path, monkeypatch
) -> None:
    client = _FakeClient(_OPENAI_SHAPE)
    _plain_env(monkeypatch, {})

    result = await _catalog(tmp_path, client).list_models(provider)

    assert result.source == "live"
    assert len(client.calls) == 1
    assert "Authorization" not in client.calls[0]["headers"]


@pytest.mark.parametrize(
    "provider", sorted(p for p, (_, a) in _PINNED.items() if a == "bearer_opt")
)
async def test_public_catalog_retries_anonymously_on_rejected_key(
    provider: str, tmp_path, monkeypatch
) -> None:
    """A stale optional key must not hide a PUBLIC catalog (pinned behavior:
    one authed attempt, then one anonymous retry)."""
    client = _FakeClient(_OPENAI_SHAPE, reject_authed=True)
    _plain_env(monkeypatch, {provider: "sk-stale"})

    result = await _catalog(tmp_path, client).list_models(provider)

    assert result.source == "live"
    assert len(client.calls) == 2
    assert client.calls[0]["headers"] == {"Authorization": "Bearer sk-stale"}
    assert "Authorization" not in client.calls[1]["headers"]


# ── Parser pins (shapes the refactor must not disturb) ───────────────────
def test_parser_openai_compatible_shape() -> None:
    models = parse_models_response("openai", {"data": [{"id": "gpt-x"}, {"id": ""}]})
    assert [(m.id, m.label) for m in models] == [("gpt-x", "gpt-x")]


def test_parser_openrouter_uses_human_name_as_label() -> None:
    models = parse_models_response("openrouter", {"data": [{"id": "a/b", "name": "A B"}]})
    assert [(m.id, m.label) for m in models] == [("a/b", "A B")]


def test_parser_gemini_strips_prefix_and_gates_on_generate_content() -> None:
    payload = {
        "models": [
            {"name": "models/gemini-x", "displayName": "Gemini X"},
            {
                "name": "models/embedding-001",
                "supportedGenerationMethods": ["embedContent"],
            },
        ]
    }
    models = parse_models_response("gemini", payload)
    assert [(m.id, m.label) for m in models] == [("gemini-x", "Gemini X")]
