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
    RECOMMENDED_VISION_PULL,
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
    # /api/show capability map: model name -> capabilities list. Models absent
    # from the map answer with a chat-capable default.
    show_caps: dict[str, list[str]] = {}

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

    async def post(self, url: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        assert url.endswith("/api/show")
        name = str((json or {}).get("model") or "")
        caps = _FakeAsyncClient.show_caps.get(name, ["completion", "tools"])
        return _FakeResponse({"capabilities": caps})


@pytest.fixture()
def fake_tags(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.fail = False
    _FakeAsyncClient.payload = {}
    _FakeAsyncClient.last_url = None
    _FakeAsyncClient.show_caps = {}
    return _FakeAsyncClient


async def test_configured_model_skips_discovery(monkeypatch, fake_tags) -> None:
    _no_override(monkeypatch)
    brain = OllamaBrain(model="qwen3.5:9b")
    assert await brain._resolve_model() == "qwen3.5:9b"
    assert fake_tags.last_url is None  # no HTTP call


async def test_discovery_uses_smallest_downloaded_model(monkeypatch, fake_tags) -> None:
    """Live incident 2026-07-25: the first-installed pick loaded a 30B model
    (45 GB at its 256k default context) on a 32 GB box and froze the desktop.
    The silent default is the SMALLEST download; the user's pick wins."""
    _no_override(monkeypatch)
    fake_tags.payload = {
        "models": [
            {"name": "qwen3-coder:30b", "size": 18_000_000_000},
            {"name": "qwen2.5:7b", "size": 4_700_000_000},
            {"name": "deepseek-r1:14b", "size": 9_000_000_000},
        ]
    }
    brain = OllamaBrain()
    assert await brain._resolve_model() == "qwen2.5:7b"
    assert fake_tags.last_url == "http://localhost:11434/api/tags"
    # Cached: a second resolve must not depend on the server again.
    fake_tags.fail = True
    assert await brain._resolve_model() == "qwen2.5:7b"


async def test_discovery_never_picks_a_cloud_reference(monkeypatch, fake_tags) -> None:
    """``:cloud`` tags are ollama.com-proxied references, not local weights —
    the LOCAL brain must never default onto a path that leaves the machine."""
    _no_override(monkeypatch)
    fake_tags.payload = {
        "models": [
            {"name": "kimi-k2.5:cloud", "size": 1},
            {"name": "remote-thing", "size": 2, "remote": True},
            {"name": "qwen2.5:7b", "size": 4_700_000_000},
        ]
    }
    assert await OllamaBrain()._resolve_model() == "qwen2.5:7b"


async def test_discovery_skips_embedding_only_downloads(monkeypatch, fake_tags) -> None:
    """Live incident 2026-07-25 #2: the smallest download was bge-m3, an
    embedding-only model that 400s on chat. The default gates on the DECLARED
    /api/show capability (never the name, AP-21) and takes the next-smallest
    chat-capable download."""
    _no_override(monkeypatch)
    fake_tags.payload = {
        "models": [
            {"name": "bge-m3:latest", "size": 1_200_000_000},
            {"name": "qwen2.5:7b", "size": 4_700_000_000},
        ]
    }
    fake_tags.show_caps = {"bge-m3:latest": ["embedding"]}
    assert await OllamaBrain()._resolve_model() == "qwen2.5:7b"


async def test_tool_turns_require_a_tools_capable_download(monkeypatch, fake_tags) -> None:
    """Live incident 2026-07-25 #3: the smallest chat-capable download had no
    ``tools`` capability and 400ed on the first tool turn. A tool-bearing
    request gates on the declared ``tools`` capability; plain chat may still
    run the smaller model."""
    _no_override(monkeypatch)
    fake_tags.payload = {
        "models": [
            {"name": "deepseek-llm:latest", "size": 1_000},
            {"name": "qwen2.5:7b", "size": 2_000},
        ]
    }
    fake_tags.show_caps = {"deepseek-llm:latest": ["completion"]}
    brain = OllamaBrain()
    assert await brain._resolve_model() == "deepseek-llm:latest"
    assert await brain._resolve_model(need_tools=True) == "qwen2.5:7b"


async def test_embedding_only_server_gives_honest_error(monkeypatch, fake_tags) -> None:
    _no_override(monkeypatch)
    fake_tags.payload = {"models": [{"name": "bge-m3:latest", "size": 1}]}
    fake_tags.show_caps = {"bge-m3:latest": ["embedding"]}
    with pytest.raises(RuntimeError) as err:
        await OllamaBrain()._resolve_model()
    msg = str(err.value)
    assert "supports chat" in msg
    assert f"ollama pull {RECOMMENDED_PULL}" in msg


async def test_cloud_only_server_gives_honest_pull_hint(monkeypatch, fake_tags) -> None:
    _no_override(monkeypatch)
    fake_tags.payload = {"models": [{"name": "kimi-k2.5:cloud", "size": 1}]}
    with pytest.raises(RuntimeError) as err:
        await OllamaBrain()._resolve_model()
    msg = str(err.value)
    assert f"ollama pull {RECOMMENDED_PULL}" in msg
    assert "cloud references do not count" in msg


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


# ── Vision: a MODEL property, never a server property ────────────────────
@pytest.fixture()
def catalog(monkeypatch):
    """Stub the cached model catalog the synchronous vision probe reads."""
    import jarvis.brain.model_catalog as mc

    state = {"caps": {}, "has_data": False, "vision_pick": None}

    monkeypatch.setattr(
        mc,
        "model_capabilities",
        lambda provider, model: state["caps"].get(model, {"vision": None, "tools": None}),
    )
    monkeypatch.setattr(mc, "provider_has_modality_data", lambda provider: state["has_data"])
    monkeypatch.setattr(mc, "pick_vision_model", lambda provider: state["vision_pick"])
    return state


def test_pinned_model_without_vision_declares_itself_blind(catalog) -> None:
    """The whole point: a text-only pull must not advertise sight, or
    ``resolve_vision_brain`` hands it a screenshot and it answers about the
    words around a picture it never saw."""
    catalog["caps"] = {"qwen3.5:9b": {"vision": False, "tools": True}}
    assert OllamaBrain(model="qwen3.5:9b").supports_vision is False


def test_pinned_multimodal_model_declares_sight(catalog) -> None:
    catalog["caps"] = {"qwen3-vl:8b": {"vision": True, "tools": True}}
    assert OllamaBrain(model="qwen3-vl:8b").supports_vision is True


def test_unknown_capability_stays_capable(catalog) -> None:
    """Fail-open on unknown: an uncached model keeps the pre-existing answer,
    and the image path then negotiates a real vision model."""
    assert OllamaBrain(model="something-uncached").supports_vision is True


def test_server_without_any_vision_download_is_blind(catalog) -> None:
    """No pinned model + a catalog that HAS the data and lists no multimodal
    download = an informed no, so the vision chain crosses to a provider that
    can actually see (AP-22)."""
    catalog["has_data"] = True
    catalog["vision_pick"] = None
    assert OllamaBrain().supports_vision is False


def test_server_with_a_vision_download_is_sighted(catalog) -> None:
    catalog["has_data"] = True
    catalog["vision_pick"] = "qwen3-vl:8b"
    assert OllamaBrain().supports_vision is True


def test_no_catalog_data_stays_capable(catalog) -> None:
    catalog["has_data"] = False
    assert OllamaBrain().supports_vision is True


async def test_image_turn_picks_a_vision_capable_download(monkeypatch, fake_tags) -> None:
    """An image turn skips the smaller text-only download and takes the
    multimodal one — while plain chat still runs the small model."""
    _no_override(monkeypatch)
    fake_tags.payload = {
        "models": [
            {"name": "qwen3.5:4b", "size": 1_000},
            {"name": "qwen3-vl:8b", "size": 5_000},
        ]
    }
    fake_tags.show_caps = {
        "qwen3.5:4b": ["completion", "tools"],
        "qwen3-vl:8b": ["completion", "tools", "vision"],
    }
    brain = OllamaBrain()
    assert await brain._resolve_model() == "qwen3.5:4b"
    assert await brain._resolve_model(need_vision=True) == "qwen3-vl:8b"


async def test_image_turn_without_a_vision_download_errors_honestly(
    monkeypatch, fake_tags
) -> None:
    _no_override(monkeypatch)
    fake_tags.payload = {"models": [{"name": "qwen3.5:4b", "size": 1_000}]}
    fake_tags.show_caps = {"qwen3.5:4b": ["completion", "tools"]}
    brain = OllamaBrain()
    with pytest.raises(RuntimeError) as err:
        await brain._resolve_model(need_vision=True)
    assert f"ollama pull {RECOMMENDED_VISION_PULL}" in str(err.value)
    # The instance corrects its own advertisement so the next synchronous
    # resolver question gets the informed answer.
    assert brain.supports_vision is False


async def test_unprobeable_model_is_never_used_for_an_image_turn(
    monkeypatch, fake_tags
) -> None:
    """Vision is the ONE requirement that fails CLOSED: a model whose
    ``/api/show`` probe does not answer may back a chat turn, never an image
    turn — a blind answer is indistinguishable from a sighted one."""
    _no_override(monkeypatch)
    fake_tags.payload = {"models": [{"name": "mystery:latest", "size": 1_000}]}

    async def _show_unavailable(self, url: str, json: dict[str, Any] | None = None) -> None:
        raise httpx.ConnectError("show unavailable")

    monkeypatch.setattr(fake_tags, "post", _show_unavailable)
    brain = OllamaBrain()
    assert await brain._resolve_model() == "mystery:latest"
    with pytest.raises(RuntimeError):
        await brain._resolve_model(need_vision=True)


async def test_complete_refuses_an_image_on_a_pinned_blind_model(
    monkeypatch, fake_tags, catalog
) -> None:
    """A pinned model is the user's own choice, so it is not silently swapped —
    but the turn errors instead of describing a screenshot it cannot see."""
    import openai

    from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _no_override(monkeypatch)
    catalog["caps"] = {"qwen3.5:9b": {"vision": False, "tools": True}}
    fake_tags.show_caps = {"qwen3.5:9b": ["completion", "tools"]}
    req = BrainRequest(
        messages=(
            BrainMessage(
                role="user",
                content="What is on my screen?",
                images=(ImageBlock(mime="image/png", data_b64="aGk="),),
            ),
        )
    )
    brain = OllamaBrain(model="qwen3.5:9b")
    with pytest.raises(RuntimeError) as err:
        async for _ in brain.complete(req):
            pass
    msg = str(err.value)
    assert "cannot see images" in msg
    assert RECOMMENDED_VISION_PULL in msg


# ── Protocol surface ─────────────────────────────────────────────────────
def test_capability_flags_and_cost(catalog) -> None:
    brain = OllamaBrain(model="x")
    assert brain.supports_tools is True
    assert brain.can_call_tools() is True
    # Unknown capability → capable (the image path does the real gating).
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
