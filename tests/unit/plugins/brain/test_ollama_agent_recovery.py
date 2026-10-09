"""Ollama agents retain explicit budgets and recover without changing seats."""

import httpx
import pytest

from jarvis.brain.model_catalog import parse_models_response
from jarvis.core.config import BrainConfig, BrainProviderConfig, JarvisConfig, OllamaModelOptions
from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock
from jarvis.plugins.brain.ollama import OllamaBrain
from tests.fakes.fake_ollama_server import FakeOllamaServer
from tests.fakes.provider_config import install_provider_config


@pytest.mark.parametrize("selected,configured", [("local", "local:latest"),
    ("local:latest", "local"), ("local:4b", "local:4b")])
def test_explicit_context_is_available_before_first_request(monkeypatch, selected, configured):
    install_provider_config(monkeypatch, JarvisConfig(brain=BrainConfig(providers={
        "ollama": BrainProviderConfig(models={configured: OllamaModelOptions(num_ctx=4096)}),
    })))
    assert OllamaBrain(model=selected).context_window == 4096


@pytest.fixture
def server(monkeypatch):
    install_provider_config(monkeypatch, JarvisConfig(brain=BrainConfig(providers={
        "ollama": BrainProviderConfig(base_url="http://ollama.test"),
    })))
    fake = FakeOllamaServer()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        **{**kwargs, "transport": fake.transport()},
    ))
    return fake


async def test_transient_capability_failure_is_retried(server):
    server.add("model", capabilities=("completion", "tools", "vision"))
    brain = OllamaBrain(model="model")
    server.offline = True
    assert await brain._capabilities("model", "http://ollama.test") is None
    server.offline = False
    assert "vision" in await brain._capabilities("model", "http://ollama.test")


async def test_pinned_text_model_refuses_tool_work_without_switching_model(server):
    server.add("text", capabilities=("completion",))
    server.add("tools", capabilities=("completion", "tools"))
    brain = OllamaBrain(model="text")
    with pytest.raises(RuntimeError, match="does not support tools"):
        await brain._resolve_model(need_tools=True)
    assert brain._model == "text" and not brain.can_call_tools()


async def test_temporary_vision_probe_failure_does_not_disable_future_turns(server):
    server.add("model", capabilities=("completion", "tools", "vision"))
    brain = OllamaBrain(model="model")
    brain._client = object()  # No inference is reached while native metadata is unavailable.
    request = BrainRequest(messages=(BrainMessage("user", "Describe", images=(
        ImageBlock(mime="image/png", data_b64="aGk="),)),))
    server.offline = True
    with pytest.raises(RuntimeError, match="Check the server connection and retry"):
        _ = [delta async for delta in brain.complete(request)]
    assert brain.supports_vision
    server.offline = False
    assert await brain._pinned_model_can_see("model")


async def test_discovery_skips_internal_profiles_but_keeps_user_imports(server):
    server.add("local-jarvis-deadbeef:latest", size=1)
    server.add("local-voice-8k:latest", size=2)
    server.add("llamacpp:custom", size=3)
    assert await OllamaBrain()._resolve_model(need_tools=True) == "llamacpp:custom"
    models = parse_models_response("ollama", {"models": [
        {"name": name} for name in server.models
    ]})
    assert [model.id for model in models] == ["llamacpp:custom"]
