"""Use model capacity by default and retain explicit user/request limits."""

import pytest

from jarvis.agent_runtimes import gateway
from jarvis.agent_runtimes.model_limits import ModelLimits, resolve_limits
from jarvis.brain.model_catalog import ModelInfo
from jarvis.core.config import OllamaModelOptions
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain.ollama import OllamaBrain


def test_known_output_capacity_is_not_halved():
    info = ModelInfo("m", "M", context_length=131072, max_output_tokens=100000)
    assert resolve_limits(None, "openai", "m", info) == ModelLimits(131072, 100000)


def test_unknown_output_capacity_does_not_claim_an_eight_k_limit():
    limits = resolve_limits(None, "openai", "m")
    assert limits.max_output_tokens is None
    assert "max_output_tokens" not in limits.wire()
    req = BrainRequest(messages=(), max_tokens=64000)
    assert gateway._with_output_capacity(req, limits).max_tokens == 64000


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("requested,expected", [(None, 200000), (180000, 180000), (512, 512)])
async def test_gateway_uses_model_maximum_when_omitted(monkeypatch, streaming, requested, expected):
    gateway.reset()
    token = gateway.grant_token("full", "openai")
    gateway.register_model(token, "m", ModelLimits(1000000, 200000))
    body = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
    if requested is not None:
        body["max_completion_tokens"] = requested
    model, request = gateway.chat_request(body)
    seen = []

    async def deltas(grant, model, request):
        seen.append(request.max_tokens)
        yield BrainDelta(content="done", finish_reason="stop")

    monkeypatch.setattr(gateway, "_deltas", deltas)
    try:
        if streaming:
            stream = await gateway.open_chat_stream(gateway.verify(token), model, request)
            _ = [chunk async for chunk in stream]
        else:
            await gateway.complete_chat(gateway.verify(token), model, request)
        assert seen == [expected]
    finally:
        gateway.reset()


@pytest.mark.parametrize("bad", [True, 0, -1, "8192"])
def test_invalid_explicit_limits_are_rejected(bad):
    with pytest.raises(gateway.GatewayError, match="positive integer"):
        gateway.chat_request(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": bad}
        )


async def test_ollama_really_allocates_the_advertised_context(monkeypatch):
    from jarvis.brain import ollama_profiles

    configured = OllamaModelOptions(temperature=0.2)
    brain = OllamaBrain(model="m")
    other = OllamaBrain(model="m")
    monkeypatch.setattr(brain, "_model_options", lambda model: configured)
    monkeypatch.setattr(brain, "_resolve_root", lambda: "http://local.invalid")
    seen = []

    async def profile(root, model, options, *, headers=None):
        seen.append(options)
        return "prepared-profile"

    monkeypatch.setattr(ollama_profiles, "ensure_profile", profile)
    # This is the already resolved limit; the resolver preserves user limits.
    brain.set_context_window(131072)
    model, _ = await brain._apply_model_options("m", BrainRequest(messages=()))
    assert model == "prepared-profile"
    assert seen[0].num_ctx == brain.context_window == 131072
    assert seen[0].temperature == 0.2
    assert configured.num_ctx is None  # no mutation of saved settings
    assert other._requested_context_window is None


async def test_a_new_user_limit_is_not_overwritten_by_an_older_plan(monkeypatch):
    brain = OllamaBrain(model="m")
    brain.set_context_window(131072)
    monkeypatch.setattr(brain, "_model_options", lambda model: OllamaModelOptions(num_ctx=16384))
    with pytest.raises(RuntimeError, match="context changed"):
        await brain._apply_model_options("m", BrainRequest(messages=()))


async def test_ollama_does_not_silently_serve_a_smaller_context(monkeypatch):
    from jarvis.brain import ollama_profiles

    brain = OllamaBrain(model="m")
    brain.set_context_window(131072)
    monkeypatch.setattr(brain, "_model_options", lambda model: None)
    monkeypatch.setattr(brain, "_resolve_root", lambda: "http://local.invalid")

    async def fail(*args):
        raise RuntimeError("provider-private-body")

    monkeypatch.setattr(ollama_profiles, "ensure_profile", fail)
    with pytest.raises(RuntimeError, match="Could not prepare") as caught:
        await brain._apply_model_options("m", BrainRequest(messages=()))
    assert "private" not in str(caught.value)


async def test_gateway_configures_capable_brain_before_inference(monkeypatch):
    from jarvis.agent_chat import runner_api
    from jarvis.core import config

    gateway.reset()
    token = gateway.grant_token("full", "ollama")
    gateway.register_model(token, "m", ModelLimits(131072))
    seen = []

    class Brain:
        def set_context_window(self, tokens):
            seen.append(tokens)

        async def complete(self, request):
            assert seen == [131072]
            yield BrainDelta(content="ok")

    monkeypatch.setattr(runner_api, "build_brain", lambda provider, model: Brain())
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    try:
        result = [
            d async for d in gateway._deltas(gateway.verify(token), "m", BrainRequest(messages=()))
        ]
        assert result[0].content == "ok"
    finally:
        gateway.reset()


def test_an_omitted_limit_never_asks_for_more_than_the_context_leaves():
    """OpenRouter declares max_completion_tokens == context_length for many
    models; asking for all of it beside any prompt is refused upstream."""
    limits = ModelLimits(163_840, 163_840)
    prompt = "x" * 300_000  # about 100k tokens at three characters per token
    # max_tokens=0: what chat_request builds when the runtime named no limit.
    req = BrainRequest(messages=(BrainMessage("user", prompt),), max_tokens=0)
    budget = gateway._with_output_capacity(req, limits).max_tokens
    assert budget <= 163_840 - 100_000 - 1024
    assert budget > 50_000


def test_an_explicit_limit_is_the_runtimes_choice():
    limits = ModelLimits(163_840, 163_840)
    req = BrainRequest(messages=(BrainMessage("user", "x" * 300_000),), max_tokens=120_000)
    assert gateway._with_output_capacity(req, limits).max_tokens == 120_000


def test_a_nearly_full_window_still_sends_a_small_budget():
    limits = ModelLimits(32_768, None)
    req = BrainRequest(messages=(BrainMessage("user", "x" * 120_000),), max_tokens=0)
    assert gateway._with_output_capacity(req, limits).max_tokens == 1024

