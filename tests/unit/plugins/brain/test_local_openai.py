"""Generic local OpenAI-compatible brain: required base URL, optional key.

The card covers HuggingFace transformers serve, llama.cpp llama-server,
LM Studio, and vLLM with ONE adapter. Unlike the cloud brains there is no
vendor default endpoint — a missing base URL must raise an honest,
example-bearing English error, never guess a port. The key is optional and
only forwarded when the user stored one.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import jarvis.core.config as cfg
from jarvis.core.config import BrainConfig, BrainProviderConfig, JarvisConfig
from jarvis.plugins.brain.local_openai import LocalOpenAIBrain


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOpenAI.last_kwargs = kwargs


def _with_base_url(url: str | None, monkeypatch, stored_key: str | None = None) -> None:
    providers = {}
    if url is not None:
        providers["local-openai"] = BrainProviderConfig(base_url=url)
    conf = JarvisConfig(brain=BrainConfig(providers=providers))
    monkeypatch.setattr(cfg, "load_config", lambda: conf)
    monkeypatch.setattr(
        cfg,
        "get_secret",
        lambda key, env=None: stored_key if key == "local_openai_api_key" else None,
    )


# ── Base URL is REQUIRED ─────────────────────────────────────────────────
def test_missing_base_url_gives_honest_examples(monkeypatch) -> None:
    _with_base_url(None, monkeypatch)
    with pytest.raises(RuntimeError) as err:
        LocalOpenAIBrain()._resolve_root()
    msg = str(err.value)
    # The error must teach the fix: the config key and real example ports.
    assert "local-openai" in msg
    assert "http://localhost:8000" in msg  # transformers serve / vLLM
    assert "http://localhost:8080" in msg  # llama.cpp llama-server
    assert "http://localhost:1234" in msg  # LM Studio


def test_client_appends_v1_to_root(monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _with_base_url("http://localhost:8000", monkeypatch)
    LocalOpenAIBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "http://localhost:8000/v1"
    # No stored key → placeholder the server ignores.
    assert _FakeOpenAI.last_kwargs["api_key"] == "local"


def test_client_normalizes_pasted_v1(monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _with_base_url("http://localhost:1234/v1/", monkeypatch)
    LocalOpenAIBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "http://localhost:1234/v1"


def test_optional_key_is_forwarded_when_stored(monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _with_base_url("http://localhost:8000", monkeypatch, stored_key="sk-local-test")
    LocalOpenAIBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["api_key"] == "sk-local-test"


def test_client_timeout_fast_connect_wide_read(monkeypatch) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _with_base_url("http://localhost:8000", monkeypatch)
    LocalOpenAIBrain()._ensure_client()
    timeout = _FakeOpenAI.last_kwargs["timeout"]
    assert timeout.connect <= 2.0
    assert timeout.read >= 120.0


# ── Model discovery via /v1/models ───────────────────────────────────────
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
    last_headers: dict[str, str] | None = None

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_headers = headers
        if _FakeAsyncClient.fail:
            raise httpx.ConnectError("connection refused")
        return _FakeResponse(_FakeAsyncClient.payload)


@pytest.fixture()
def fake_models(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.fail = False
    _FakeAsyncClient.payload = {}
    _FakeAsyncClient.last_url = None
    _FakeAsyncClient.last_headers = None
    return _FakeAsyncClient


async def test_discovery_uses_first_served_model(monkeypatch, fake_models) -> None:
    _with_base_url("http://localhost:8000", monkeypatch)
    fake_models.payload = {"data": [{"id": "Qwen/Qwen3.5-9B"}, {"id": "other"}]}
    brain = LocalOpenAIBrain()
    assert await brain._resolve_model() == "Qwen/Qwen3.5-9B"
    assert fake_models.last_url == "http://localhost:8000/v1/models"


async def test_discovery_sends_bearer_when_key_stored(monkeypatch, fake_models) -> None:
    _with_base_url("http://localhost:8000", monkeypatch, stored_key="sk-local-test")
    fake_models.payload = {"data": [{"id": "m"}]}
    await LocalOpenAIBrain()._resolve_model()
    assert fake_models.last_headers == {"Authorization": "Bearer sk-local-test"}


async def test_empty_model_list_gives_honest_error(monkeypatch, fake_models) -> None:
    _with_base_url("http://localhost:8000", monkeypatch)
    fake_models.payload = {"data": []}
    with pytest.raises(RuntimeError) as err:
        await LocalOpenAIBrain()._resolve_model()
    assert "/v1/models is empty" in str(err.value)


async def test_unreachable_server_gives_honest_error(monkeypatch, fake_models) -> None:
    _with_base_url("http://localhost:8000", monkeypatch)
    fake_models.fail = True
    with pytest.raises(RuntimeError) as err:
        await LocalOpenAIBrain()._resolve_model()
    msg = str(err.value)
    assert "not reachable" in msg
    assert "http://localhost:8000" in msg


# ── Vision: blind unless the server itself says otherwise ────────────────
def test_vision_stays_blind_when_the_server_declares_nothing(monkeypatch) -> None:
    """The common case: an OpenAI-compatible server publishes no modality
    fields, so the honest answer is "cannot see" — a confident description of a
    picture nobody looked at is the failure worth being conservative about."""
    import jarvis.brain.model_catalog as mc

    monkeypatch.setattr(
        mc, "model_capabilities", lambda provider, model: {"vision": None, "tools": None}
    )
    assert LocalOpenAIBrain(model="Qwen/Qwen3.5-9B").supports_vision is False


def test_vision_believes_a_server_that_declares_image_input(monkeypatch) -> None:
    """Some servers DO publish ``architecture.input_modalities``; a user who
    runs a multimodal model there should not have to move to another card."""
    import jarvis.brain.model_catalog as mc

    monkeypatch.setattr(
        mc, "model_capabilities", lambda provider, model: {"vision": True, "tools": True}
    )
    assert LocalOpenAIBrain(model="Qwen/Qwen3-VL-8B").supports_vision is True


# ── Protocol surface ─────────────────────────────────────────────────────
def test_capability_flags_and_cost() -> None:
    brain = LocalOpenAIBrain(model="x")
    assert brain.supports_tools is True
    assert brain.can_call_tools() is True
    # Deliberately False without evidence: whether an arbitrary local server
    # accepts image content is unknowable — the shared streamer then DROPS
    # images with a warning instead of letting a text-only server reject the
    # whole turn.
    assert brain.supports_vision is False
    req_like = type("R", (), {"messages": (), "max_tokens": 100})()
    assert brain.estimate_cost(req_like) == 0.0


def test_wizard_whitelists_the_optional_secret() -> None:
    """Saving the optional key from the app requires the wizard slot to exist
    (ALLOWED_SECRET_KEYS is derived from it)."""
    from jarvis.setup.wizard import SECRETS

    spec = next(s for s in SECRETS if s.key == "local_openai_api_key")
    assert spec.env_fallback == "LOCAL_OPENAI_API_KEY"
    # App-only: local servers usually need no key — it must not lengthen
    # first-run onboarding.
    assert spec.prompt is False
