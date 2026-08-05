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


def _local_env(
    monkeypatch,
    *,
    base_urls: dict[str, str] | None = None,
    stored_local_key: str | None = None,
) -> None:
    """Hermetic env for the LOCAL providers: optional base-url overrides, no
    real keyring, no ambient OLLAMA_HOST."""
    from jarvis.core.config import BrainConfig, BrainProviderConfig

    providers = {pid: BrainProviderConfig(base_url=url) for pid, url in (base_urls or {}).items()}
    conf = JarvisConfig(brain=BrainConfig(providers=providers))
    monkeypatch.setattr(cfg, "load_config", lambda: conf)
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: None)
    monkeypatch.setattr(
        cfg,
        "get_secret",
        lambda key, env=None: stored_local_key if key == "local_openai_api_key" else None,
    )
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


_OLLAMA_TAGS = {"models": [{"name": "qwen3.5:9b"}, {"name": "glm-5.1:latest"}]}


def test_parser_ollama_lists_downloaded_models_only() -> None:
    """The picker must show what the user actually HOLDS: ``:cloud`` entries
    are ollama.com-proxied references (maintainer report 2026-07-25), and
    ``remote`` entries likewise never represent local weights."""
    payload = {
        "models": [
            {"name": "qwen2.5:7b"},
            {"name": "kimi-k2.5:cloud"},
            {"name": "other-remote", "remote": True},
        ]
    }
    models = parse_models_response("ollama", payload)
    assert [m.id for m in models] == ["qwen2.5:7b"]


# ── Local half (S3b): ollama /api/tags ───────────────────────────────────
async def test_ollama_fetches_tags_keyless(tmp_path, monkeypatch) -> None:
    client = _FakeClient(_OLLAMA_TAGS)
    _local_env(monkeypatch)

    result = await _catalog(tmp_path, client).list_models("ollama")

    assert result.source == "live"
    # The picker applies its relevance sort — pin membership, not order.
    assert {m.id for m in result.models} == {"qwen3.5:9b", "glm-5.1:latest"}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "http://localhost:11434/api/tags"
    assert call["headers"] == {}
    assert call["params"] == {}


class _FakeOllamaClient(_FakeClient):
    """Adds the native ``/api/show`` capability probe to the GET fake."""

    def __init__(self, payload: dict[str, Any], show_caps: dict[str, list[str]]) -> None:
        super().__init__(payload)
        self.show_caps = show_caps
        self.shown: list[str] = []

    async def post(self, url: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        name = str((json or {}).get("model") or "")
        self.shown.append(name)
        assert url.endswith("/api/show")
        return _FakeResponse({"capabilities": self.show_caps.get(name, [])})


async def test_ollama_catalog_carries_declared_capabilities(tmp_path, monkeypatch) -> None:
    """``/api/tags`` says WHAT is installed, ``/api/show`` says what it can DO.

    Without the second half every local download reached the capability
    consumers as "unknown" — and unknown means "assume capable", which is how a
    text-only install came to advertise vision to Screen Context.
    """
    client = _FakeOllamaClient(
        {"models": [{"name": "qwen3.5:9b"}, {"name": "qwen3-vl:8b"}]},
        {
            "qwen3.5:9b": ["completion", "tools"],
            "qwen3-vl:8b": ["completion", "tools", "vision"],
        },
    )
    _local_env(monkeypatch)

    result = await _catalog(tmp_path, client).list_models("ollama")

    caps = {m.id: (m.input_modalities, m.supported_parameters) for m in result.models}
    assert caps["qwen3.5:9b"] == (("text",), ("tools",))
    assert caps["qwen3-vl:8b"] == (("text", "image"), ("tools",))
    assert sorted(client.shown) == ["qwen3-vl:8b", "qwen3.5:9b"]


async def test_ollama_catalog_drops_embedding_only_downloads(tmp_path, monkeypatch) -> None:
    """bge-m3 in a BRAIN picker guarantees a 400 on the first chat turn."""
    client = _FakeOllamaClient(
        {"models": [{"name": "bge-m3:latest"}, {"name": "qwen3.5:9b"}]},
        {"bge-m3:latest": ["embedding"], "qwen3.5:9b": ["completion"]},
    )
    _local_env(monkeypatch)

    result = await _catalog(tmp_path, client).list_models("ollama")

    assert [m.id for m in result.models] == ["qwen3.5:9b"]


async def test_ollama_catalog_survives_a_server_without_api_show(tmp_path, monkeypatch) -> None:
    """Fail-open per model: an unanswerable probe leaves the entry unknown
    (capable), exactly as before this enrichment existed."""

    class _NoShow(_FakeOllamaClient):
        async def post(self, url, json=None):  # type: ignore[override]
            raise RuntimeError("api/show unavailable")

    _local_env(monkeypatch)

    result = await _catalog(tmp_path, _NoShow(_OLLAMA_TAGS, {})).list_models("ollama")

    assert {m.id for m in result.models} == {"qwen3.5:9b", "glm-5.1:latest"}
    assert all(m.input_modalities is None for m in result.models)


async def test_ollama_honors_base_url_override(tmp_path, monkeypatch) -> None:
    """A pasted ``…/v1`` override is normalized to the server root first."""
    client = _FakeClient(_OLLAMA_TAGS)
    _local_env(monkeypatch, base_urls={"ollama": "http://gpu.lan:11434/v1/"})

    await _catalog(tmp_path, client).list_models("ollama")

    assert client.calls[0]["url"] == "http://gpu.lan:11434/api/tags"


async def test_ollama_honors_ollama_host_env(tmp_path, monkeypatch) -> None:
    client = _FakeClient(_OLLAMA_TAGS)
    _local_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:12345")

    await _catalog(tmp_path, client).list_models("ollama")

    # 0.0.0.0 is a bind address, not a client target — mapped to localhost.
    assert client.calls[0]["url"] == "http://localhost:12345/api/tags"


async def test_ollama_unreachable_returns_honest_empty_static(tmp_path, monkeypatch) -> None:
    """No fake curated list for a local provider: dead server → empty models,
    honest ``static`` source (the card's test button explains the fix)."""

    class _DeadClient(_FakeClient):
        async def get(self, url, headers=None, params=None):  # type: ignore[override]
            raise RuntimeError("connection refused")

    _local_env(monkeypatch)

    result = await _catalog(tmp_path, _DeadClient({})).list_models("ollama")

    assert result.source == "static"
    assert result.models == ()


# ── Local half (S3b): local-openai /v1/models ────────────────────────────
async def test_local_openai_without_base_url_is_honestly_empty(tmp_path, monkeypatch) -> None:
    """No configured server → no network call, no fake list, no crash."""
    client = _FakeClient(_OPENAI_SHAPE)
    _local_env(monkeypatch)

    result = await _catalog(tmp_path, client).list_models("local-openai")

    assert result.source == "static"
    assert result.models == ()
    assert client.calls == []


async def test_local_openai_fetches_v1_models_from_override(tmp_path, monkeypatch) -> None:
    client = _FakeClient({"data": [{"id": "Qwen/Qwen3.5-9B"}]})
    _local_env(monkeypatch, base_urls={"local-openai": "http://localhost:8000"})

    result = await _catalog(tmp_path, client).list_models("local-openai")

    assert result.source == "live"
    assert [m.id for m in result.models] == ["Qwen/Qwen3.5-9B"]
    call = client.calls[0]
    assert call["url"] == "http://localhost:8000/v1/models"
    assert call["headers"] == {}


async def test_local_openai_attaches_optional_stored_key(tmp_path, monkeypatch) -> None:
    client = _FakeClient({"data": [{"id": "m"}]})
    _local_env(
        monkeypatch,
        base_urls={"local-openai": "http://localhost:8000"},
        stored_local_key="sk-local-pin",
    )

    await _catalog(tmp_path, client).list_models("local-openai")

    assert client.calls[0]["headers"] == {"Authorization": "Bearer sk-local-pin"}


def test_local_ttl_capped_near_live(tmp_path) -> None:
    """The installed-model set changes with every pull/restart — a local
    catalog older than 60 s is stale even under the 6 h default TTL."""
    catalog = _catalog(tmp_path, _FakeClient({}))
    import time as _time

    two_minutes_ago = _time.time() - 120
    assert catalog._is_fresh("openai", two_minutes_ago) is True
    assert catalog._is_fresh("ollama", two_minutes_ago) is False
    assert catalog._is_fresh("local-openai", two_minutes_ago) is False


def test_parser_ollama_tags_shape() -> None:
    models = parse_models_response("ollama", {"models": [{"name": "qwen3.5:9b"}, {"name": ""}]})
    assert [(m.id, m.label) for m in models] == [("qwen3.5:9b", "qwen3.5:9b")]


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
