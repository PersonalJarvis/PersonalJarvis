"""Keyless local Ollama brain: endpoint normalization, discovery, honest errors.

The provider must work with ZERO credentials (§3): the SDK client gets a
dummy key, the server root comes from config override → OLLAMA_HOST → the
localhost default, and every failure surfaces an honest, actionable English
message instead of a fake model id that would 404.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import jarvis.core.config as cfg
from jarvis.core.config import BrainConfig, BrainProviderConfig, JarvisConfig
from jarvis.plugins.brain.ollama import (
    DEFAULT_SERVER_ROOT,
    RECOMMENDED_PULL,
    OllamaBrain,
    default_server_root,
    normalize_server_root,
)


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOpenAI.last_kwargs = kwargs


def _no_override(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


def _override(url: str, monkeypatch) -> None:
    conf = JarvisConfig(brain=BrainConfig(providers={"ollama": BrainProviderConfig(base_url=url)}))
    monkeypatch.setattr(cfg, "load_config", lambda: conf)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


# ── Server-root normalization ────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:11434", "http://localhost:11434"),
        ("http://localhost:11434/", "http://localhost:11434"),
        # A pasted OpenAI-compat suffix must not double up to /v1/v1.
        ("http://localhost:11434/v1", "http://localhost:11434"),
        ("http://localhost:11434/api", "http://localhost:11434"),
        # Bare host:port (the OLLAMA_HOST shape) gains a scheme.
        ("127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("mybox:11434", "http://mybox:11434"),
        # 0.0.0.0 is a server BIND address — as a client target it fails on
        # Windows, so it maps to localhost.
        ("0.0.0.0:11434", "http://localhost:11434"),
        ("https://gpu.lan:11434/", "https://gpu.lan:11434"),
        ("", DEFAULT_SERVER_ROOT),
        ("   ", DEFAULT_SERVER_ROOT),
    ],
)
def test_normalize_server_root(raw: str, expected: str) -> None:
    assert normalize_server_root(raw) == expected


def test_default_server_root_honors_ollama_host(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:12345")
    assert default_server_root() == "http://localhost:12345"


def test_default_server_root_without_env(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert default_server_root() == DEFAULT_SERVER_ROOT


# ── Client construction (keyless) ────────────────────────────────────────
def test_client_defaults_to_localhost_v1_with_dummy_key(monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _no_override(monkeypatch)
    OllamaBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "http://localhost:11434/v1"
    # Keyless: the SDK insists on a non-empty key, Ollama ignores it.
    assert _FakeOpenAI.last_kwargs["api_key"] == "ollama"


def test_client_uses_config_override_root(monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _override("http://gpu.lan:11434", monkeypatch)
    OllamaBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "http://gpu.lan:11434/v1"


def test_client_normalizes_pasted_v1_override(monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _override("http://gpu.lan:11434/v1/", monkeypatch)
    OllamaBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "http://gpu.lan:11434/v1"


def test_client_timeout_fast_connect_wide_read(monkeypatch) -> None:
    """A dead local server must fail fast so the chain crosses families,
    while a slow CPU-bound generation may stream for minutes."""
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _no_override(monkeypatch)
    OllamaBrain()._ensure_client()
    timeout = _FakeOpenAI.last_kwargs["timeout"]
    assert timeout.connect <= 2.0
    assert timeout.read >= 120.0


# ── Model discovery via native /api/tags ─────────────────────────────────
class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    payload: dict[str, Any] = {}
    fail: bool = False
    last_url: str | None = None

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        _FakeAsyncClient.last_url = url
        if _FakeAsyncClient.fail:
            raise httpx.ConnectError("connection refused")
        return _FakeResponse(_FakeAsyncClient.payload)


@pytest.fixture()
def fake_tags(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.fail = False
    _FakeAsyncClient.payload = {}
    _FakeAsyncClient.last_url = None
    return _FakeAsyncClient


async def test_configured_model_skips_discovery(monkeypatch, fake_tags) -> None:
    _no_override(monkeypatch)
    brain = OllamaBrain(model="qwen3.5:9b")
    assert await brain._resolve_model() == "qwen3.5:9b"
    assert fake_tags.last_url is None  # no HTTP call


async def test_discovery_uses_first_installed_model(monkeypatch, fake_tags) -> None:
    _no_override(monkeypatch)
    fake_tags.payload = {"models": [{"name": "qwen3.5:9b"}, {"name": "gemma4:12b"}]}
    brain = OllamaBrain()
    assert await brain._resolve_model() == "qwen3.5:9b"
    assert fake_tags.last_url == "http://localhost:11434/api/tags"
    # Cached: a second resolve must not depend on the server again.
    fake_tags.fail = True
    assert await brain._resolve_model() == "qwen3.5:9b"


async def test_empty_server_gives_honest_pull_hint(monkeypatch, fake_tags) -> None:
    _no_override(monkeypatch)
    fake_tags.payload = {"models": []}
    with pytest.raises(RuntimeError) as err:
        await OllamaBrain()._resolve_model()
    assert f"ollama pull {RECOMMENDED_PULL}" in str(err.value)


async def test_unreachable_server_gives_honest_error(monkeypatch, fake_tags) -> None:
    _no_override(monkeypatch)
    fake_tags.fail = True
    with pytest.raises(RuntimeError) as err:
        await OllamaBrain()._resolve_model()
    msg = str(err.value)
    assert "not reachable" in msg
    assert "http://localhost:11434" in msg


# ── Protocol surface ─────────────────────────────────────────────────────
def test_capability_flags_and_cost() -> None:
    brain = OllamaBrain(model="x")
    assert brain.supports_tools is True
    assert brain.can_call_tools() is True
    assert brain.supports_vision is True
    # Local inference bills nothing — the cost meter must see 0.
    req_like = type("R", (), {"messages": (), "max_tokens": 100})()
    assert brain.estimate_cost(req_like) == 0.0


def test_tags_payload_shape_matches_documented_api() -> None:
    """Pin the parsed shape: /api/tags returns {"models": [{"name": ...}]}.

    If Ollama ever changes this, the discovery test data here must be updated
    together with ``_resolve_model`` — this test documents the contract.
    """
    payload = json.loads('{"models": [{"name": "qwen3.5:9b", "size": 1}]}')
    names = [m.get("name") for m in payload["models"]]
    assert names == ["qwen3.5:9b"]
