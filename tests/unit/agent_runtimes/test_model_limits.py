"""Runtime configs and both gateway response modes share the model budget."""

import pytest

from jarvis.agent_runtimes import gateway
from jarvis.agent_runtimes.base import RuntimeTurn
from jarvis.agent_runtimes.hermes import HermesRuntime
from jarvis.agent_runtimes.model_limits import ModelLimits, resolve_limits
from jarvis.agent_runtimes.model_map import ModelRoute, prepare_route
from jarvis.agent_runtimes.openclaw import OpenClawRuntime
from jarvis.brain.model_catalog import CatalogResult, ModelCatalog, ModelInfo, parse_models_response
from jarvis.core.config import BrainProviderConfig, JarvisConfig, OllamaModelOptions
from jarvis.core.protocols import BrainDelta, BrainRequest


def config(context=None, output=None):
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        models={"small:latest": OllamaModelOptions(num_ctx=context, num_predict=output)}
    )
    return cfg


@pytest.mark.parametrize(
    ("allocated", "native", "expected"),
    [
        (8192, 131072, 8192),
        (65536, 16384, 16384),
        (65536, None, 65536),
        (None, 8192, 8192),
        # No num_ctx chosen: a bounded default, never the whole native window
        # (Ollama allocates it up front; 256k froze a 32 GB desktop).
        (None, 262144, 65536),
        (None, 131072, 65536),
        (None, None, 32768),
        # A larger window chosen on the model card is honoured.
        (131072, 262144, 131072),
    ],
)
def test_local_allocation_is_bounded_by_native_context(allocated, native, expected):
    metadata = ModelInfo("small:latest", "Small", context_length=native)
    limits = resolve_limits(config(allocated), "ollama", "small", metadata)
    assert limits.context_window == expected
    assert limits.max_output_tokens is None


def test_per_model_output_and_large_catalog_limits():
    limits = resolve_limits(config(8192, 1024), "ollama", "small")
    assert limits == ModelLimits(8192, 1024)
    large = ModelInfo("large", "Large", context_length=1_000_000, max_output_tokens=65536)
    assert resolve_limits(config(), "openrouter", "large", large) == ModelLimits(1_000_000, 65536)
    small = ModelInfo("small", "Small", context_length=8192, max_output_tokens=1024)
    assert resolve_limits(config(8192, 64000), "ollama", "small", small).max_output_tokens == 1024


def test_latest_alias_never_bypasses_a_saved_smaller_context():
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(models={
        "example": OllamaModelOptions(num_ctx=8192, num_predict=1024),
    })
    metadata = ModelInfo("example:latest", "Example", context_length=131072)
    assert resolve_limits(cfg, "ollama", "example:latest", metadata) == ModelLimits(8192, 1024)


@pytest.mark.parametrize("context,output", [(8192, 1024), (1_000_000, 65536)])
def test_both_runtime_configs_use_the_resolved_model(context, output, tmp_path):
    route = ModelRoute(
        "ollama", "small", "http://localhost/gateway", "chat_completions", None, context, output
    )
    turn = RuntimeTurn("eval", "Eval", "society:eval", tmp_path, route, None, False)
    assert HermesRuntime().config_for(turn)["model"]["context_length"] == context
    runtime_config = OpenClawRuntime().config_for(turn, port=1, token="test")  # noqa: S106
    model = runtime_config["models"]["providers"]["jarvis"]["models"][0]
    assert (model["contextWindow"], model["maxTokens"]) == (context, output)


@pytest.mark.parametrize("streaming", [True, False])
async def test_gateway_clamps_output_before_provider_call(monkeypatch, streaming):
    gateway.reset()
    token = gateway.grant_token("eval", "ollama")
    gateway.register_model(token, "small", ModelLimits(8192, 1024))
    grant = gateway.verify(token)
    requests = []

    async def deltas(_grant, _model, request):
        requests.append(request)
        yield BrainDelta(content="answer", finish_reason="stop")

    monkeypatch.setattr(gateway, "_deltas", deltas)
    request = BrainRequest(messages=(), max_tokens=64000)
    if streaming:
        result = await gateway.open_chat_stream(grant, "small", request)
        assert b"answer" in b"".join([part async for part in result])
    else:
        result = await gateway.complete_chat(grant, "small", request)
        assert result["choices"][0]["message"]["content"] == "answer"
    assert requests[0].max_tokens == 1024
    assert request.max_tokens == 64000  # the caller's frozen request is unchanged
    gateway.reset()


async def test_custom_local_model_is_discoverable_with_its_limits():
    gateway.reset()
    token = gateway.grant_token("eval", "ollama")
    gateway.register_model(token, "custom-small", ModelLimits(8192, 1024))
    rows = await gateway.list_models(gateway.verify(token))
    row = next(row for row in rows if row["id"] == "custom-small")
    assert row["context_length"] == 8192 and row["max_output_tokens"] == 1024
    gateway.reset()


def test_catalog_metadata_survives_restart(tmp_path):
    path = tmp_path / "catalog.json"
    catalog = ModelCatalog(cache_path=path)
    catalog._cache["ollama"] = (
        1.0,
        [ModelInfo("small:latest", "Small", context_length=8192, max_output_tokens=1024)],
    )
    catalog._save_cache()
    restored = ModelCatalog(cache_path=path).cached_model("ollama", "small")
    assert restored.context_length == 8192 and restored.max_output_tokens == 1024


@pytest.mark.parametrize(
    "provider,payload,expected",
    [
        (
            "gemini",
            {
                "models": [
                    {"name": "models/small", "inputTokenLimit": 16384, "outputTokenLimit": 2048}
                ]
            },
            (16384, 2048),
        ),
        (
            "openrouter",
            {
                "data": [
                    {
                        "id": "large",
                        "context_length": 1000000,
                        "top_provider": {"max_completion_tokens": 65536},
                    }
                ]
            },
            (1000000, 65536),
        ),
        (
            "local-openai",
            {"data": [{"id": "small", "context_window": 8192, "max_output_tokens": 1024}]},
            (8192, 1024),
        ),
        (
            "openai",
            {"data": [{"id": "unknown", "context_length": True, "max_output_tokens": -1}]},
            (None, None),
        ),
    ],
)
def test_declared_limits_are_preserved_without_model_name_guesses(provider, payload, expected):
    row = parse_models_response(provider, payload)[0]
    assert (row.context_length, row.max_output_tokens) == expected


async def test_prepare_refreshes_limits_before_runtime_config(monkeypatch):
    import jarvis.agent_runtimes.model_map as mapping

    gateway.reset()
    token = gateway.grant_token("eval", "ollama")
    route = ModelRoute("ollama", "small", "http://localhost/gateway", "chat_completions", token)
    monkeypatch.setattr(mapping, "route_for", lambda *args, **kwargs: route)

    class Catalog:
        async def list_models(self, provider):
            assert provider == "ollama"
            return CatalogResult(
                provider, (ModelInfo("small:latest", "Small", context_length=8192),), "live", 1.0
            )

    monkeypatch.setattr(gateway, "_CATALOG", Catalog())
    actual = await prepare_route(config(65536), "ollama", "small", agent_id="eval")
    assert actual.context_window == 8192
    assert gateway.model_limits(gateway.verify(token), "small").context_window == 8192
    gateway.reset()


async def test_subscription_catalog_limits_are_account_specific(monkeypatch):
    gateway.reset()

    class Subscription:
        async def list_models(self):
            return [{"id": "large", "context_length": 400000, "max_output_tokens": 64000}]

    accounts = []

    def client(account_id):
        accounts.append(account_id)
        return Subscription()

    monkeypatch.setattr(gateway, "_client", client)
    limits = await gateway.refresh_model_limits(
        gateway.Grant("eval", "openai-codex", "seat-2"), "large", config()
    )
    assert accounts == ["seat-2"]
    assert limits == ModelLimits(400000, 64000)
    rows = await gateway.list_models(gateway.Grant("eval", "openai-codex", "seat-2"))
    assert rows[0]["context_length"] == 400000
    assert rows[0]["max_output_tokens"] == 64000


async def test_unavailable_metadata_preserves_the_prepared_limits(monkeypatch):
    gateway.reset()
    token = gateway.grant_token("eval", "openai-codex")
    gateway.register_model(token, "selected", ModelLimits(16000, 2000))

    class Subscription:
        async def list_models(self):
            raise TimeoutError

    monkeypatch.setattr(gateway, "_client", lambda _: Subscription())
    limits = await gateway.refresh_model_limits(gateway.verify(token), "selected", config())
    assert limits == ModelLimits(16000, 2000)


def test_hermes_minimum_context_error_is_actionable_without_raw_details():
    from jarvis.agent_runtimes.acp import _error_text

    error = {
        "message": "Internal error",
        "data": {
            "details": (
                "Model private-model has a context window of 32,768 tokens, "
                "which is below the minimum 64,000 required by Hermes Agent. "
                "private-provider-body"
            )
        },
    }
    message = _error_text(error)
    assert "32,768" in message and "64,000" in message and "Settings" in message
    assert "private" not in message
    assert (
        _error_text({"message": "Internal error", "data": {"details": "private"}})
        == "Internal error"
    )
