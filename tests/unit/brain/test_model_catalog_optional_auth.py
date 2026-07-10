"""Public model catalogs stay available when an optional key is stale."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from jarvis.brain import model_catalog as catalog_module
from jarvis.brain.model_catalog import ModelCatalog


class _SequenceClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _SequenceClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.calls.append({"url": url, "headers": dict(headers or {}), "params": params})
        return self.responses.pop(0)


def _response(status: int, payload: dict[str, Any]) -> httpx.Response:
    request = httpx.Request("GET", "https://catalog.example/models")
    return httpx.Response(status, request=request, json=payload)


@pytest.mark.asyncio
async def test_optional_bearer_retries_public_catalog_anonymously_after_401(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _SequenceClient([
        _response(401, {"error": {"message": "invalid key"}}),
        _response(200, {"data": [{"id": "openai/gpt-5.5", "name": "GPT-5.5"}]}),
    ])
    monkeypatch.setattr(catalog_module.cfg, "get_provider_secret", lambda _provider: "stale")
    catalog = ModelCatalog(
        cache_path=tmp_path / "catalog.json", http_client_factory=lambda: client
    )

    models = await catalog._fetch_raw("openrouter")

    assert [model.id for model in models] == ["openai/gpt-5.5"]
    assert client.calls[0]["headers"]["Authorization"] == "Bearer stale"
    assert "Authorization" not in client.calls[1]["headers"]


@pytest.mark.asyncio
async def test_required_bearer_does_not_retry_authentication_failure_anonymously(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _SequenceClient([_response(401, {"error": {"message": "invalid key"}})])
    monkeypatch.setattr(catalog_module.cfg, "get_provider_secret", lambda _provider: "stale")
    catalog = ModelCatalog(
        cache_path=tmp_path / "catalog.json", http_client_factory=lambda: client
    )

    with pytest.raises(httpx.HTTPStatusError):
        await catalog._fetch_raw("openai")

    assert len(client.calls) == 1
